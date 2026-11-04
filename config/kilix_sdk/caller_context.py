"""Reject pane routes inherited through a shared Codex app-server.

A server's environment describes its startup pane, not the frontend of the
current request. Neither a live socket nor a matching broker attach helper can
repair that missing association. Inspect only our bounded process ancestry;
never search other frontends, their environments, or their credentials.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

_PROC = Path('/proc')
_MAX_ANCESTORS = 64


class CallerContextError(ValueError):
    """The inherited route cannot be attributed to the calling frontend."""


def _read(path: Path, limit: int) -> bytes:
    with path.open('rb') as source:
        value = source.read(limit + 1)
    if len(value) > limit:
        raise ValueError('process field exceeds inspection limit')
    return value


def _identity(path: Path) -> tuple[int, int, int]:
    owner = path.stat().st_uid
    fields = _read(path / 'stat', 4096).rsplit(b') ', 1)[1].split()
    if fields[0] == b'Z':
        raise ValueError('ancestor exited')
    return owner, int(fields[1]), int(fields[19])


def require_pane_local() -> None:
    """Refuse shared-server ancestry before using any inherited pane context.

    This is an accidental cross-pane routing guard, not authentication against
    a malicious same-user process. Linux ancestry that cannot be checked is
    refused too. Non-Linux platforms retain their existing transport behavior.
    """
    if not sys.platform.startswith('linux'):
        return
    pid = os.getpid()
    seen = set()
    try:
        for _ in range(_MAX_ANCESTORS):
            if pid <= 1:
                return
            if pid in seen:
                break
            seen.add(pid)
            path = _PROC / str(pid)
            first = _identity(path)
            if first[0] != os.geteuid():
                return  # end of this user's process ancestry
            name = _read(path / 'comm', 256).strip()
            if name in {b'codex', b'codex.exe'}:
                args = _read(path / 'cmdline', 65536).split(b'\0')
                # Conservative even with global options preceding the command.
                # A prompt mentioning app-server is one whole argv item.
                if b'app-server' in args[1:]:
                    raise CallerContextError(
                        'shared Codex app-server has no verified caller pane; '
                        'run codex --no-daemon from the intended pane')
            if _identity(path) != first:
                break
            pid = first[1]
        else:
            raise ValueError('ancestor limit exceeded')
    except CallerContextError:
        raise
    except (OSError, ValueError, IndexError):
        pass
    raise CallerContextError('cannot verify inherited pane context from process ancestry')
