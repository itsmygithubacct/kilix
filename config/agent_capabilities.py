"""Bounded installation inspection; never execute a component to discover it."""
from __future__ import annotations

import argparse
import ast
import codecs
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import stat
import subprocess
import time

SCHEMA = "kilix.capabilities/v1"
MAX_FILE_BYTES = 262144
# The launcher is streamed one block at a time, never held whole. Its dispatch
# scan has its own bounds, and its outcome cannot erase VERSION, revision or
# action facts, which are read beside it rather than from it.
MAX_LAUNCHER_BYTES = 4194304
MAX_LAUNCHER_LINE = 4096
LAUNCHER_BLOCK = 65536
LAUNCHER_TIMEOUT = 1.0
MAX_RESPONSE_BYTES = 32768
MAX_STRING = 1024
GIT_TIMEOUT = 1.0
REVISION = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
# A case label at the start of a line. `[^\S\n]` keeps a match on one line: a
# leading `\s*` crosses newlines, which makes blank-line input quadratic.
DISPATCH = re.compile(r"^([^\S\n]*)([^()\s]+)\)", re.M)
OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0)
PROVENANCE_KEYS = {"PLEBIAN_OS_VERSION", "PLEBIAN_OS_COMMIT", "KILIX_DIR",
                   "KILIX_REF", "KILIX_VERSION", "KILIX_SOURCE_COMMIT"}
COMMAND_FILES = {"agent-control": "config/agent_control.py",
                 "pane": "config/kilix_sdk/panes.py",
                 "tab": "config/kilix_sdk/panes.py",
                 "action": "config/agent_actions.py",
                 "models": "config/content_models.py",
                 "skills": "config/agent_skills.py",
                 "tmux": "config/tmux_control.py"}
COMMAND_NAMES = frozenset(COMMAND_FILES)


def _read(path, limit=MAX_FILE_BYTES):
    """Nonblocking open avoids FIFO/device hangs; reads only regular files."""
    try:
        fd = os.open(path, OPEN_FLAGS)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None, "not_regular"
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            return None, "too_large"
        return raw.decode("utf-8"), "observed"
    except FileNotFoundError:
        return None, "missing"
    except (OSError, UnicodeError, ValueError):
        return None, "unreadable"


def _path(value):
    try:
        if not isinstance(value, (str, Path)) or len(str(value)) > MAX_STRING:
            return None
        return Path(value).resolve()
    except (OSError, ValueError, RuntimeError):
        return None


def _git_revision(root):
    """Bound both subprocess duration and captured bytes, including bad PATH git."""
    binary = shutil.which("git")
    if not binary:
        return None, "git_missing"
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    process = None
    try:
        process = subprocess.Popen(
            [binary, "-C", str(root), "-c", "core.fsmonitor=false", "rev-parse",
             "--show-toplevel", "HEAD"], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env)
        deadline, data = time.monotonic() + GIT_TIMEOUT, bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, "timeout"
                if not selector.select(remaining):
                    return None, "timeout"
                chunk = os.read(process.stdout.fileno(), 2049 - len(data))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > 2048:
                    return None, "too_large"
        process.wait(timeout=max(0.001, deadline - time.monotonic()))
        lines = data.decode("utf-8").splitlines()
        if (process.returncode == 0 and len(lines) == 2
                and _path(lines[0]) == root and REVISION.fullmatch(lines[1])):
            return lines[1], "observed"
        return None, "unresolved"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except (OSError, UnicodeError, ValueError):
        return None, "unresolved"
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def _provenance(path):
    raw, status = _read(path, 16384)
    record = {"path": str(path), "status": status, "recorded": {}}
    if raw is None:
        return record
    try:
        for line in raw.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key not in PROVENANCE_KEYS:
                continue
            words = shlex.split(value, comments=True, posix=True)
            if len(words) != 1 or len(words[0]) > MAX_STRING or key in record["recorded"]:
                raise ValueError("invalid provenance field")
            record["recorded"][key] = words[0]
    except ValueError:
        record.update(status="malformed", recorded={})
    return record


def _literal_metadata(path):
    raw, status = _read(path)
    result = {"path": str(path), "status": status, "schema": None, "operations": {}, "request": {}}
    if raw is None:
        return result
    try:
        tree = ast.parse(raw)
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"SCHEMA", "ACTION_SCHEMA", "ACTION_OPERATIONS", "ACTION_REQUEST"}:
                    value = ast.literal_eval(node.value)
                    if target.id == "ACTION_OPERATIONS" and isinstance(value, dict):
                        result["operations"] = value
                    elif target.id == "ACTION_REQUEST" and isinstance(value, dict):
                        result["request"] = value
                    elif target.id in {"SCHEMA", "ACTION_SCHEMA"} and isinstance(value, str):
                        result["schema"] = value
        if not result["schema"] or not result["operations"]:
            result["status"] = "metadata_unresolved"
    except (SyntaxError, ValueError, TypeError, RecursionError, MemoryError):
        result.update(status="malformed", operations={})
    return result


def _labels(text, found, start=0, end=None):
    """Add tracked names from case labels that fit within a line's bounded head."""
    for space, label in DISPATCH.findall(text, start, len(text) if end is None else end):
        if len(space) + len(label) < MAX_LAUNCHER_LINE:
            found.update(COMMAND_NAMES.intersection(label.split("|")))


def _dispatch(fd, size):
    """Collect tracked case labels from complete lines, one bounded block at a time.

    Only a complete scan counts: excess size, the deadline or invalid UTF-8
    yields no partial command list. A line longer than MAX_LAUNCHER_LINE keeps
    only its head, so one huge line costs no more memory than a short one.
    """
    if size > MAX_LAUNCHER_BYTES:
        return "too_large", frozenset()
    decoder = codecs.getincrementaldecoder("utf-8")()
    deadline = time.monotonic() + LAUNCHER_TIMEOUT
    found, total, head, overlong = set(), 0, "", False
    try:
        while True:
            if time.monotonic() > deadline:
                return "timeout", frozenset()
            block = os.read(fd, min(LAUNCHER_BLOCK, MAX_LAUNCHER_BYTES + 1 - total))
            if not block:
                break
            total += len(block)
            if total > MAX_LAUNCHER_BYTES:
                return "too_large", frozenset()
            text = decoder.decode(block)
            first = text.find("\n")
            if first >= 0:
                if not overlong:  # finish the line carried from earlier blocks
                    _labels(head + text[:min(first, MAX_LAUNCHER_LINE - len(head))], found)
                last = text.rfind("\n")
                _labels(text, found, first + 1, last + 1)
                head, overlong = text[last + 1:], False
            elif not overlong:
                head += text
            if len(head) > MAX_LAUNCHER_LINE:
                _labels(head[:MAX_LAUNCHER_LINE], found)
                head, overlong = "", True
        decoder.decode(b"", final=True)
    except (OSError, UnicodeDecodeError):
        return "unreadable", frozenset()
    if not overlong:
        _labels(head, found)
    return "observed", frozenset(found)


def _launcher(path, scan):
    """Classify the launcher file itself, then stream its dispatch table if asked."""
    record = {"status": "missing", "bytes": None, "dispatch_status": "not_inspected",
              "labels": frozenset()}
    try:
        fd = os.open(path, OPEN_FLAGS)
    except FileNotFoundError:
        return record
    except (OSError, ValueError):
        return dict(record, status="unreadable")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return dict(record, status="not_regular")
        record.update(status="observed", bytes=info.st_size)
        if scan:
            record["dispatch_status"], record["labels"] = _dispatch(fd, info.st_size)
        return record
    except OSError:
        return dict(record, status="unreadable")
    finally:
        os.close(fd)


def _component(root, command=None, expected=None, kind="kilix"):
    result = {"root": str(root) if root else None,
              "command": str(command) if command else None,
              "status": "missing", "launcher_bytes": None,
              "dispatch_status": "not_inspected", "commands_present": [],
              "version": None, "version_status": "not_inspected",
              "revision": None, "revision_status": "not_inspected",
              "expected_revision": expected, "revision_matches_expected": None}
    if root is None:
        return result
    # `status` describes the launcher file; `dispatch_status` its scan.
    launcher = _launcher(root / ("kilix" if kind == "kilix" else "kilix-needle"), kind == "kilix")
    result.update(status=launcher["status"], launcher_bytes=launcher["bytes"],
                  dispatch_status=launcher["dispatch_status"])
    # VERSION, the Git revision and action metadata never depend on launcher text.
    version, version_status = _read(root / "VERSION", 256)
    if version is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}\s*", version):
        result["version"] = version.strip()
    result["version_status"] = version_status if version is None else (
        "observed" if result["version"] else "malformed")
    result["revision"], result["revision_status"] = _git_revision(root)
    if expected and result["revision"]:
        result["revision_matches_expected"] = result["revision"] == expected
    if kind == "kilix":
        # Require both an observed wrapper dispatch and its implementation.
        # Labels exist only after a complete scan, so a refusal lists nothing.
        result["commands_present"] = [name for name, relative in COMMAND_FILES.items()
                                      if name in launcher["labels"] and (root / relative).is_file()]
        result["action_backend"] = _literal_metadata(root / "config/agent_actions.py")
    else:
        result["cli_module_present"] = (root / "needle_cli.py").is_file()
        cli, _ = _read(root / "needle_cli.py")
        mcp, _ = _read(root / "mcp_server.py")
        adapter_files = all((root / file).is_file() for file in ("action_cli.py", "action_backend.py"))
        result["action_adapter_present"] = bool(adapter_files and cli and "from action_cli import" in cli)
        result["action_mcp_tools_present"] = bool(adapter_files and mcp and "kilix_action_" in mcp)
        # Presence is evidence of source files, not model/provider readiness.
        result["version_status"] = "unresolved" if version_status == "missing" else result["version_status"]
    return result


def _bounded(value, depth=0):
    if depth > 12:
        return "[depth limit]"
    if isinstance(value, str):
        # Bound the actual ASCII JSON encoding, including escaped Unicode.
        if len(json.dumps(value, ensure_ascii=True).encode()) <= MAX_STRING:
            return value
        end = min(len(value), MAX_STRING)
        while len(json.dumps(value[:end] + "...", ensure_ascii=True).encode()) > MAX_STRING:
            end //= 2
        return value[:end] + "..."
    if isinstance(value, dict):
        return {str(key)[:128]: _bounded(item, depth + 1)
                for key, item in list(value.items())[:64]}
    if isinstance(value, (tuple, list)):
        return [_bounded(item, depth + 1) for item in value[:64]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[unsupported metadata]"


def discover(source_root=None, *, environ=None, provenance_path=None):
    """Inspect fixed selected paths. No setup, imports of components, or CLI probes.

    ``source_root`` identifies this query's wrapper tree. PATH selection is
    reported separately. Optional arguments support isolated callers/tests.
    """
    env = os.environ if environ is None else environ
    source = _path(source_root or Path(__file__).resolve().parents[1])
    provenance = _provenance(Path(provenance_path or "/var/lib/plebian-os/versions.env"))
    expected = env.get("KILIX_REF") or provenance["recorded"].get("KILIX_REF")
    expected = expected if isinstance(expected, str) and REVISION.fullmatch(expected) else None
    installed_command = shutil.which("kilix", path=env.get("PATH", ""))
    installed = _path(installed_command) if installed_command else None
    invoked = _component(source, expected=expected)
    selected = (dict(invoked, command=installed_command)
                if source and installed and source == installed.parent
                else _component(installed.parent if installed else None, installed_command, expected))
    needle_command = shutil.which("kilix-needle", path=env.get("PATH", ""))
    needle = _path(needle_command) if needle_command else None
    needle_selection = env.get("KILIX_NEEDLE_KILIX")
    needle_kilix = shutil.which(needle_selection or "kilix", path=env.get("PATH", ""))
    if needle_selection and _path(needle_selection) and (_path(needle_selection) / "kilix").is_file():
        needle_kilix = str(_path(needle_selection) / "kilix")
    needle_kilix = _path(needle_kilix) if needle_kilix else None
    backend_override = env.get("KILIX_ACTION_MODULE_ROOT")
    override = (_path(backend_override) if backend_override
                and Path(backend_override).is_absolute() else None)
    result = {
        "schema": SCHEMA, "read_only": True,
        "kilix": {"invoked_source": invoked, "path_selected": selected,
                  "same_root": bool(source and installed and source == installed.parent)},
        "needle": _component(needle.parent if needle else None, needle_command, kind="needle"),
        "needle_kilix_selection": {"configured": needle_selection,
                                   "resolved_command": str(needle_kilix) if needle_kilix else None},
        "action_module_override": (_literal_metadata(override / "agent_actions.py")
                                   if override else {"status": "invalid" if backend_override else "unset"}),
        "os_provenance": provenance,
        "limits": {"response_bytes": MAX_RESPONSE_BYTES, "file_bytes": MAX_FILE_BYTES,
                   "launcher_bytes": MAX_LAUNCHER_BYTES, "launcher_line_chars": MAX_LAUNCHER_LINE,
                   "launcher_scan_seconds": LAUNCHER_TIMEOUT,
                   "git_timeout_seconds": GIT_TIMEOUT, "component_probes": False},
    }
    result = _bounded(result)
    if len(json.dumps(result, ensure_ascii=True).encode()) > MAX_RESPONSE_BYTES:
        for component in (result["kilix"]["invoked_source"], result["kilix"]["path_selected"]):
            if "action_backend" in component:
                component["action_backend"].update(operations={}, request={}, status="response_limit")
        result["action_module_override"].update(operations={}, request={}, status="response_limit")
        result["truncated"] = True
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kilix action capabilities", description=__doc__)
    parser.parse_args(argv)
    print(json.dumps(discover(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
