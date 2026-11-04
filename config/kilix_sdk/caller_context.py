"""Verify caller broker ownership and reject shared Codex app-server routes.

A server's environment describes its startup pane, not the frontend of the
current request. Neither a live socket nor a matching broker attach helper can
repair that missing association. Inspect only our bounded process ancestry;
never search other frontends, their environments, or their credentials.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from collections.abc import Mapping

_PROC = Path('/proc')
_MAX_ANCESTORS = 64


class CallerContextError(ValueError):
    """The inherited route cannot be attributed to the calling frontend."""


class BrokerOwnershipUnavailable(CallerContextError):
    """Raw metadata may be inspected, but cannot authorize a terminal route."""


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


def _broker_startup(path: Path, executable: str, name: bytes) -> tuple[str, str] | None:
    """Verify a broker run identity or refuse an unverifiable owner barrier.

    The native wrapper supplies --runtime-dir and run --id. Command payload
    arguments after -- never select identity, and no environment/credentials
    are copied from the ancestor. Global --timeout and run logging options
    use the pinned broker CLI's two-item option format.
    """
    try:
        same_image = os.path.samefile(path / 'exe', executable)
    except OSError:
        same_image = False
    args = _read(path / 'cmdline', 65536).split(b'\0')
    if args[-1:] == [b'']:
        args.pop()
    index, runtime = 1, None
    while index < len(args) and args[index] in (b'--runtime-dir', b'--timeout'):
        if index + 1 >= len(args):
            raise BrokerOwnershipUnavailable('cannot verify caller broker ownership')
        if args[index] == b'--runtime-dir':
            runtime = os.fsdecode(args[index + 1])
        index += 2
    command = args[index:index + 1]
    # An old image is a refusal barrier, never authority: do not skip the
    # nearest surviving owner and accidentally verify an outer/sibling owner.
    possible_owner = (same_image or args[:1] == [os.fsencode(executable)]
                      or name == os.fsencode(os.path.basename(executable))[:15]
                      or (runtime is not None and command == [b'run']))
    if not possible_owner:
        return None
    if not same_image or command != [b'run']:
        raise BrokerOwnershipUnavailable('cannot verify caller broker ownership')
    index += 1
    session = None
    options = {b'--id', b'--journal-limit', b'--transcript',
               b'--transcript-limit', b'--transcript-graphics'}
    while index < len(args) and args[index] != b'--':
        if args[index] not in options or index + 1 >= len(args):
            raise BrokerOwnershipUnavailable('cannot verify caller broker ownership')
        if args[index] == b'--id':
            session = os.fsdecode(args[index + 1])
        index += 2
    if not runtime or not session or args[index:index + 1] != [b'--']:
        raise BrokerOwnershipUnavailable('cannot verify caller broker ownership')
    return runtime, session


def require_pane_local(environment: Mapping[str, str] | None = None) -> None:
    """Require caller broker proof and refuse shared-server ancestry.

    This is an accidental cross-pane routing guard, not authentication against
    a malicious same-user process. Linux ancestry that cannot be checked is
    refused too. Non-Linux platforms retain their existing transport behavior.
    """
    if not sys.platform.startswith('linux'):
        return
    env = os.environ if environment is None else environment
    executable = env.get('KITTY_PTY_BROKER_EXECUTABLE', '')
    claimed = (env.get('KITTY_PTY_BROKER_RUNTIME', ''), env.get('KITTY_PTY_BROKER_SESSION', ''))
    check_broker = os.path.isabs(executable) and all(claimed)
    needs_broker_proof = bool(claimed[1])
    broker_verified = False
    pid = os.getpid()
    seen = set()
    try:
        for _ in range(_MAX_ANCESTORS):
            if pid <= 1:
                if needs_broker_proof and not broker_verified:
                    raise BrokerOwnershipUnavailable('cannot verify caller broker ownership')
                return
            if pid in seen:
                break
            seen.add(pid)
            path = _PROC / str(pid)
            first = _identity(path)
            if first[0] != os.geteuid():
                if needs_broker_proof and not broker_verified:
                    raise BrokerOwnershipUnavailable('cannot verify caller broker ownership')
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
            if check_broker and not broker_verified:
                binding = _broker_startup(path, executable, name)
                if binding is not None:
                    if binding != claimed:
                        raise CallerContextError(
                            'inherited broker context conflicts with the caller broker; '
                            'no route or credential recovery attempted')
                    # Nested terminals may use the same binary. Only the
                    # nearest broker owns this caller; keep checking higher
                    # ancestors for the separate shared-server boundary.
                    broker_verified = True
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
