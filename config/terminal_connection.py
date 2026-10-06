"""Refresh a restored pane's control connection through its PTY broker identity.

A persistent child keeps the environment of the terminal which first launched
it. The current broker attachment is a child of the replacement terminal and
has its complete, fresh environment. Never recover by pane number, title, or
the existence of just one running terminal. No remote operation is retried.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys


class ConnectionError(RuntimeError):
    pass


NAMES = (
    "KITTY_LISTEN_ON", "KITTY_PID", "KITTY_WINDOW_ID", "KITTY_PUBLIC_KEY",
    "KITTY_PTY_BROKER_SESSION", "KITTY_PTY_BROKER_RUNTIME", "KILIX_RC_PASSWORD_FILE",
    "KILIX_KITTEN", "KILIX_BUILD_DIRECTORY", "KILIX_PREBUILT_HOME",
)
REQUIRED = NAMES[:7]
BROKER = re.compile(r"[0-9a-f]{16,64}\Z")
SOCKET = re.compile(r"unix:@kilix-([1-9][0-9]*)\Z")
MAX_ENV = 1024 * 1024


def _read(path: Path, limit=MAX_ENV):
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("process metadata too large")
    return value


def _environment(proc):
    result = {}
    for entry in _read(proc / "environ").split(b"\0"):
        key, sep, value = entry.partition(b"=")
        name = os.fsdecode(key)
        if sep and name in NAMES:
            result[name] = os.fsdecode(value)
    return result


def _identity(proc, uid):
    if proc.stat().st_uid != uid:
        raise ValueError("process belongs to another user")
    # comm can contain spaces and parentheses. Fields start after its last ')'.
    fields = _read(proc / "stat", 8192).rsplit(b")", 1)[1].split()
    if fields[0] == b"Z":
        raise ValueError("process exited")
    return int(fields[1]), int(fields[19])  # parent and start time (PID reuse)


def inherited_values(environment=None, *, proc_root=Path("/proc"), parent=None, uid=None):
    """Recover missing connection fields without mixing terminal/broker contexts."""
    environment = os.environ if environment is None else environment
    values = {k: environment[k] for k in NAMES if environment.get(k)}
    pid, uid = os.getppid() if parent is None else parent, os.getuid() if uid is None else uid
    for _ in range(24):
        if all(values.get(k) for k in REQUIRED) or pid <= 1:
            break
        proc = proc_root / str(pid)
        try:
            next_pid, _ = _identity(proc, uid)
            parent_values = _environment(proc)
            if all(not values.get(k) or parent_values.get(k) == values[k]
                   for k in ("KITTY_LISTEN_ON", "KITTY_PTY_BROKER_SESSION")):
                for key, value in parent_values.items():
                    values.setdefault(key, value)
            pid = next_pid
        except (OSError, ValueError, IndexError):
            break
    return values


def _attachment(proc, values, root, uid):
    """Return a complete bundle only for a verified live broker frontend."""
    before = _identity(proc, uid)
    if Path(os.readlink(proc / "exe")).name != "kitty-pty-broker":
        return None
    fresh = _environment(proc)
    if any(fresh.get(k) != values.get(k) for k in (
            "KITTY_PTY_BROKER_SESSION", "KITTY_PTY_BROKER_RUNTIME")):
        return None
    if not all(fresh.get(k) for k in REQUIRED):
        return None
    argv = [os.fsdecode(a) for a in _read(proc / "cmdline", 65536).split(b"\0") if a]
    prefix = ["--runtime-dir", fresh["KITTY_PTY_BROKER_RUNTIME"]]
    token = fresh["KITTY_PTY_BROKER_SESSION"]
    if not (argv[1:] == [*prefix, "attach", token] or
            argv[1:6] == [*prefix, "run", "--id", token]):
        return None
    terminal_pid = fresh["KITTY_PID"]
    if not terminal_pid.isascii() or not terminal_pid.isdigit() or int(terminal_pid) != before[0]:
        return None
    if fresh["KITTY_LISTEN_ON"] != f"unix:@kilix-{terminal_pid}":
        return None
    window = fresh["KITTY_WINDOW_ID"]
    if not window.isascii() or not window.isdigit() or int(window) <= 0:
        return None
    if not fresh["KITTY_PUBLIC_KEY"].startswith("1:") or len(fresh["KITTY_PUBLIC_KEY"]) > 256:
        return None
    if not Path(fresh["KILIX_RC_PASSWORD_FILE"]).is_absolute():
        return None
    terminal = root / terminal_pid
    terminal_before = _identity(terminal, uid)
    if Path(os.readlink(terminal / "exe")).name != "kitty":
        return None
    # The executable adjacent to the active engine is the matching client.
    fresh["KILIX_KITTEN"] = str(Path(os.readlink(terminal / "exe")).with_name("kitten"))
    if _identity(proc, uid) != before or _identity(terminal, uid) != terminal_before:
        return None
    return fresh


def connection_values(environment=None, *, proc_root=Path("/proc"), parent=None, uid=None):
    """Resolve the same broker's current attachment or fail before sending input.

    Opaque/custom connections retain their existing behavior. Only the host's
    PID-named local sockets and broker frontends participate in auto-recovery.
    """
    uid = os.getuid() if uid is None else uid
    values = inherited_values(environment, proc_root=proc_root, parent=parent, uid=uid)
    address = values.get("KITTY_LISTEN_ON", "")
    host_socket = SOCKET.fullmatch(address)
    if address and not host_socket:
        return values
    token = values.get("KITTY_PTY_BROKER_SESSION", "")
    runtime = values.get("KITTY_PTY_BROKER_RUNTIME", "")
    if token:
        if not BROKER.fullmatch(token) or not Path(runtime).is_absolute():
            raise ConnectionError("invalid PTY broker connection identity; restart this tool from its Kilix pane")
        candidates = []
        try:
            processes = list(proc_root.iterdir())
        except OSError as error:
            raise ConnectionError("cannot inspect the current PTY attachment") from error
        for proc in processes:
            if not proc.name.isascii() or not proc.name.isdigit():
                continue
            try:
                # A cheap filter before reading environments; Linux truncates comm.
                if not _read(proc / "comm", 128).strip().startswith(b"kitty-pty-broke"):
                    continue
                fresh = _attachment(proc, values, proc_root, uid)
                if fresh is not None:
                    candidates.append(fresh)
            except (OSError, ValueError, IndexError):
                continue
        if len(candidates) == 1:
            return candidates[0]
        detail = "multiple live attachments" if candidates else "no live attachment"
        raise ConnectionError(f"stale or detached Kilix connection: {detail} for this PTY broker; "
                              "reattach the original session or restart this tool/MCP server from "
                              "the intended pane; changing KITTY_LISTEN_ON alone is insufficient")
    if host_socket:
        try:
            terminal = proc_root / host_socket[1]
            _identity(terminal, uid)
            if Path(os.readlink(terminal / "exe")).name != "kitty":
                raise ValueError("PID was reused")
        except (OSError, ValueError, IndexError) as error:
            raise ConnectionError("stale Kilix connection without a PTY broker identity; "
                                  "restart this tool/MCP server from the intended pane") from error
    return values


def refreshed_environment(environment=None, **kwargs):
    source = dict(os.environ if environment is None else environment)
    values = connection_values(source, **kwargs)
    for name in NAMES:
        source.pop(name, None)
    source.update(values)
    return source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--exec", dest="command", nargs=argparse.REMAINDER,
                        help="run a command once with the refreshed connection")
    group.add_argument("--launch", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        env = refreshed_environment()
        env.pop("_KILIX_CONNECTION_CHECKED", None)
        command = args.launch or args.command
        if command:
            # The launcher consumes and removes this before dispatching children.
            if args.launch:
                env["_KILIX_CONNECTION_CHECKED"] = "1"
            os.execvpe(command[0], command, env)
        print(json.dumps({"socket": env.get("KITTY_LISTEN_ON"),
                          "terminal_pid": env.get("KITTY_PID"),
                          "pane_id": env.get("KITTY_WINDOW_ID"),
                          "broker": env.get("KITTY_PTY_BROKER_SESSION"),
                          "public_key_available": bool(env.get("KITTY_PUBLIC_KEY"))}))
        return 0
    except (ConnectionError, OSError) as error:
        print(f"kilix connection: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
