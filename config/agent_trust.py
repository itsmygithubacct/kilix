"""Trust one launch directory the way each coding client records it.

A launch asked to trust its folder answers the client's first-start "trust
this folder?" question before the client starts, by writing the client's own
record for exactly that directory. Nothing is typed into a dialog and no
other setting changes. Paths with quotes, backslashes or control characters
are refused rather than escaped.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import time
import tomllib

UNSAFE = re.compile(r'["\\\x00-\x1f\x7f]')


class TrustError(RuntimeError):
    pass


def _replace(path: Path, text: str) -> None:
    """Write text to path atomically, keeping the file's mode."""
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _claude(directory: str, home: Path) -> str:
    path = home / ".claude.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    project = data.setdefault("projects", {}).setdefault(directory, {})
    if project.get("hasTrustDialogAccepted") is True:
        return "already trusted"
    project["hasTrustDialogAccepted"] = True
    _replace(path, json.dumps(data, indent=2) + "\n")
    return "trusted"


def _toml_section(path: Path, table: str, directory: str, key: str, good, lines: list[str]):
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    entry = (tomllib.loads(text).get(table) or {}).get(directory)
    if entry is not None:
        if entry.get(key) == good:
            return "already trusted", None
        # The user decided otherwise for this folder: leave their decision.
        raise TrustError(f"{directory} has its own {key} setting; left as it is")
    block = f'\n[{table}."{directory}"]\n' + "".join(line + "\n" for line in lines)
    return "trusted", (text if text.endswith("\n") or not text else text + "\n") + block


def _codex(directory: str, home: Path) -> str:
    path = home / ".codex" / "config.toml"
    result, text = _toml_section(path, "projects", directory, "trust_level", "trusted",
                                 ['trust_level = "trusted"'])
    if text is not None:
        _replace(path, text)
    return result


def _grok(directory: str, home: Path) -> str:
    path = home / ".grok" / "trusted_folders.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result, text = _toml_section(path, "folders", directory, "trusted", True,
                                     ["trusted = true", f"decided_at = {int(time.time())}"])
        if text is not None:
            _replace(path, text)
    return result


CLIENTS = {"claude": _claude, "codex": _codex, "grok": _grok}


def trust(agent: str, directory: str, home: Path | None = None) -> str:
    """Record trust for exactly `directory`; return what was done."""
    home = home or Path.home()
    path = Path(directory)
    if not path.is_absolute() or not path.is_dir() or path.resolve() != path:
        raise TrustError("only an existing absolute directory, as resolved, can be trusted")
    if UNSAFE.search(directory):
        raise TrustError("the directory name has quotes, backslashes or control characters")
    if path.stat().st_uid != os.getuid():
        raise TrustError("only a directory you own can be trusted")
    if path == home or path == Path("/"):
        raise TrustError("the home directory and / are never trusted wholesale")
    handler = CLIENTS.get(agent)
    if handler is None:
        return "no trust prompt"          # kimi, qwen-omp: nothing to record
    return handler(directory, home)
