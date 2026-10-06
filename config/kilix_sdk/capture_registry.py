"""Publish live local application panes for a consent-based capture picker.

The records identify sources, never grant capture permission. No X cookies or
remote-control credentials are stored. Pleb independently verifies each live
owner, application, X server and authority-file identity before offering it.
"""
from __future__ import annotations

import json
import itertools
import os
from pathlib import Path
import re
import secrets
import stat
import time

_PROC = Path('/proc')
_PENDING_STALE_SECONDS = 60
_RECORD_KEYS = frozenset((
    'version', 'id', 'label', 'owner_pid', 'owner_start', 'server_pid', 'server_start',
    'app_pid', 'app_start', 'display', 'desktop_display', 'authority',
    'authority_device', 'authority_inode', 'xid', 'width', 'height'))


def _prune_exited_owners(directory: Path) -> None:
    # Hard provider deaths cannot unregister. Remove only bounded, recognized
    # records whose owning process identity is gone; unknown files are retained.
    for path in itertools.islice(directory.glob('*.json'), 4096):
        try:
            if not re.fullmatch(r'[0-9a-f]{32}', path.stem):
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 8192):
                    continue
                value = json.loads(stream.read(8193))
                if (not isinstance(value, dict) or set(value) != _RECORD_KEYS
                        or type(value['version']) is not int or value['version'] != 1
                        or value['id'] != path.stem
                        or any(type(value[role + '_pid']) is not int or value[role + '_pid'] <= 0
                               or not isinstance(value[role + '_start'], str)
                               or not value[role + '_start'].isdecimal()
                               for role in ('owner', 'server', 'app'))):
                    continue
                try:
                    alive = _identity(value['owner_pid'])[1] == value['owner_start']
                except (OSError,ValueError,IndexError):
                    alive = False
                current = path.lstat()
                if not alive and (current.st_dev,current.st_ino) == (info.st_dev,info.st_ino):
                    path.unlink()
        except (OSError,ValueError,TypeError):
            continue
    # publish() removes its staging file in the same call, so a .pending name
    # that has aged is from a provider killed between create and unlink. Its
    # content may be partial, so age (not the recorded owner) identifies it.
    now = time.time()
    for path in itertools.islice(directory.glob('*.pending'), 4096):
        try:
            if not re.fullmatch(r'[0-9a-f]{32}', path.stem):
                continue
            info = path.lstat()
            if (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and now - info.st_mtime > _PENDING_STALE_SECONDS):
                path.unlink()
        except OSError:
            continue


def _private_directory(path: Path, *, create=False) -> None:
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if (not path.is_absolute() or path.resolve() != path or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError('Application capture needs a private session runtime')


def _identity(pid: int) -> tuple[int, str]:
    path = _PROC / str(pid)
    if type(pid) is not int or pid <= 0 or path.stat().st_uid != os.getuid():
        raise ValueError('Application capture owner is unavailable')
    fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] in {'Z', 'X'}:
        raise ValueError('Application capture owner exited')
    return int(fields[1]), fields[19]


class Publication:
    def __init__(self, path: Path, identity: tuple[int, int]):
        self.path = path
        self.identity = identity

    def close(self) -> None:
        try:
            info = self.path.lstat()
            if (info.st_dev, info.st_ino) == self.identity and info.st_uid == os.getuid():
                self.path.unlink()
        except OSError:
            pass


def publish(session, label: str) -> Publication | None:
    """Registration is optional on hosts without a private XDG runtime."""
    try:
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR', ''))
        _private_directory(runtime)
        directory = runtime / 'kilix-capture-sources'
        desktop = os.environ.get('PLEB_DESKTOP_DISPLAY') or os.environ.get('DISPLAY', '')
        display = session.display
        if (not re.fullmatch(r':\d+(?:\.\d+)?', desktop)
                or not isinstance(display, str) or not re.fullmatch(r':\d+', display)
                or desktop.removesuffix('.0') == display):
            return None
        owner = os.getpid()
        _, owner_tick = _identity(owner)
        server, app = getattr(session.server, 'pid', 0), getattr(session.app, 'pid', 0)
        server_parent, server_tick = _identity(server)
        app_parent, app_tick = _identity(app)
        if server_parent != owner or app_parent != owner:
            return None
        authority = Path(session.xauthority)
        info = authority.lstat()
        if (not authority.is_absolute() or authority.resolve() != authority
                or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            return None
        root = session.connect().screen().root
        geometry = root.get_geometry()
        if (type(root.id) is not int or not 1 <= root.id <= 0xffffffff
                or not 1 <= geometry.width <= 16384 or not 1 <= geometry.height <= 16384
                or geometry.width * geometry.height > 67108864):
            return None
        token = secrets.token_hex(16)
        record = {'version': 1, 'id': token, 'label': ' '.join(str(label).split())[:200] or 'Application',
                  'owner_pid': owner, 'owner_start': owner_tick, 'server_pid': server, 'server_start': server_tick,
                  'app_pid': app, 'app_start': app_tick, 'display': display,
                  'xid': root.id, 'width': geometry.width, 'height': geometry.height,
                  'desktop_display': desktop.removesuffix('.0'), 'authority': str(authority),
                  'authority_device': info.st_dev, 'authority_inode': info.st_ino}
        _private_directory(directory, create=True)
        _prune_exited_owners(directory)
        path = directory / (token + '.json')
        pending = directory / (token + '.pending')
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump(record, output)
            os.link(pending, path)
        finally:
            pending.unlink(missing_ok=True)
        info = path.lstat()
        return Publication(path, (info.st_dev, info.st_ino))
    except Exception:
        return None


__all__ = ['Publication', 'publish']
