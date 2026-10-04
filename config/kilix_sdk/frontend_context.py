"""Refresh an orphaned broker child's route from its live attach process.

The attach helper is a child of the replacement engine and carries that
engine's authenticated context. Only a same-user helper for this exact broker
runtime/session may supply the five route fields; no credentials are logged.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
from collections.abc import MutableMapping

_PROC = Path('/proc')
_SESSION = re.compile(r'[A-Za-z0-9._-]{1,64}')
_ROUTE = ('KITTY_PID', 'KITTY_WINDOW_ID', 'KITTY_LISTEN_ON',
          'KITTY_PUBLIC_KEY', 'KILIX_RC_PASSWORD_FILE')
_BROKER = ('KITTY_PTY_BROKER_SESSION', 'KITTY_PTY_BROKER_RUNTIME')


def _identity(path: Path) -> tuple[int, int] | None:
    if path.stat().st_uid != os.geteuid():
        return None
    fields = (path / 'stat').read_text().rsplit(') ', 1)[1].split()
    if fields[0] == 'Z':
        return None
    return int(fields[1]), int(fields[19])


def _kitty_alive(pid: str) -> bool:
    if not pid.isdecimal() or int(pid) <= 0:
        return False
    try:
        path = _PROC / pid
        return bool(_identity(path) and (path / 'exe').resolve(strict=True).name == 'kitty')
    except (OSError, ValueError, IndexError):
        return False


def _limited(path: Path, limit: int) -> bytes:
    with path.open('rb') as source:
        value = source.read(limit + 1)
    if len(value) > limit:
        raise ValueError('process field exceeds route inspection limit')
    return value


def _candidate(path: Path, executable: str, runtime: str,
               session: str) -> dict[str, str] | None:
    first = _identity(path)
    if first is None or not os.path.samefile(path / 'exe', executable):
        return None
    args = _limited(path / 'cmdline', 65536).split(b'\0')
    if args and args[-1] == b'':
        args.pop()
    if args[1:] != [b'--runtime-dir', os.fsencode(runtime), b'attach', session.encode()]:
        return None
    wanted = {key.encode(): key for key in (*_ROUTE, *_BROKER)}
    values = {}
    for item in _limited(path / 'environ', 262144).split(b'\0'):
        key, separator, value = item.partition(b'=')
        if separator and key in wanted:
            values[wanted[key]] = value.decode('utf-8')
    if (values.get(_BROKER[0]) != session or values.get(_BROKER[1]) != runtime
            or values.get('KITTY_PID') != str(first[0])):
        return None
    parent = _PROC / str(first[0])
    parent_first = _identity(parent)
    if not parent_first or (parent / 'exe').resolve(strict=True).name != 'kitty':
        return None
    window = values.get('KITTY_WINDOW_ID', '')
    socket = values.get('KITTY_LISTEN_ON', '')
    if (not window.isdecimal() or int(window) <= 0
            or not socket.startswith('unix:') or len(socket) <= 5
            or not values.get('KITTY_PUBLIC_KEY')
            or any(len(value) > 4096 or '\n' in value or '\r' in value
                   for value in values.values())):
        return None
    if _identity(path) != first or _identity(parent) != parent_first:
        return None
    return {key: values[key] for key in _ROUTE if key in values}


def refresh(environment: MutableMapping[str, str] | None = None) -> bool:
    """Rebind a surviving broker child after its original engine has exited.

    A live original engine takes the fast path. Ambiguous, foreign, malformed,
    or vanished peers leave the caller's environment unchanged. The ordinary
    remote-control password and public-key authentication remain in use.
    """
    env = os.environ if environment is None else environment
    session = env.get(_BROKER[0], '')
    if not _SESSION.fullmatch(session) or session in {'.', '..'}:
        return False
    if _kitty_alive(env.get('KITTY_PID', '')):
        return False
    executable = env.get('KITTY_PTY_BROKER_EXECUTABLE', '')
    runtime = env.get(_BROKER[1], '')
    if not os.path.isabs(executable) or not os.path.isabs(runtime):
        return False
    try:
        info = os.lstat(runtime)
        binary = os.stat(executable)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or not stat.S_ISREG(binary.st_mode)
                or binary.st_uid not in {0, os.geteuid()} or binary.st_mode & 0o022):
            return False
        matches = []
        for path in _PROC.iterdir():
            if not path.name.isdecimal():
                continue
            try:
                value = _candidate(path, executable, runtime, session)
                if value:
                    matches.append(value)
                    if len(matches) > 1:
                        return False
            except (OSError, ValueError, IndexError, UnicodeError):
                continue
        if len(matches) != 1:
            return False
        env.update(matches[0])
        return True
    except (OSError, ValueError):
        return False
