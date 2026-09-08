"""Continuing build ownership and compiler search/snapshot directory identity."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import stat
import struct


class BuildBoundaryChanged(ValueError):
    """Owned name/history changed; a partial live publication must be restored."""


class DirectoryEvents:
    """Retain mutations even when a same-tick restore leaves equal timestamps."""

    def __init__(self, name=None):
        self.name = name
        self.failed = False
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), 'cannot retain build directory events')

    def watch(self, path):
        # MODIFY, ATTRIB, CLOSE_WRITE, MOVED_FROM/TO, CREATE, DELETE,
        # DELETE_SELF, MOVE_SELF; ONLYDIR and DONT_FOLLOW.
        mask = 0x00000fce | 0x01000000 | 0x02000000
        if self.name is not None:
            # Closing another O_RDWR lock descriptor emits CLOSE_WRITE even
            # without a write. Actual writes still emit MODIFY and remain fatal.
            mask &= ~0x00000008
        if self.libc.inotify_add_watch(self.fd, os.fsencode(path), mask) < 0:
            raise OSError(ctypes.get_errno(), 'cannot watch build directory')

    def check(self):
        if self.failed:
            raise BuildBoundaryChanged('build directory mutation history is already invalid')
        while True:
            try:
                block = os.read(self.fd, 65536)
            except BlockingIOError:
                return
            if not block:
                raise BuildBoundaryChanged('build directory event channel ended')
            offset = 0
            while offset < len(block):
                _watch, mask, _cookie, size = struct.unpack_from('iIII', block, offset)
                name = block[offset + 16:offset + 16 + size].split(b'\0', 1)[0]
                # Empty names include self mutation, watch removal and queue
                # overflow. No missing history can authorize publication.
                if self.name is None or not name or name == self.name:
                    self.failed = True
                    raise BuildBoundaryChanged('build directory mutation event: ' + str(mask))
                offset += 16 + size

    def close(self):
        os.close(self.fd)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_ctime_ns)


class DirectoryHistory:
    """Hold only generated source directories, including their change history.

    A rename followed by restoration changes directory ctime even when every
    original leaf inode and byte is restored. Shared ancestors are not treated
    as immutable: only the private snapshot hierarchy gets this history check.
    """

    def __init__(self, root, check):
        self.entries = []
        self.events = DirectoryEvents()
        try:
            for path in [root, *sorted(p for p in root.rglob('*') if p.is_dir())]:
                check()
                fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                self.entries.append((path, fd, identity(os.fstat(fd))))
                self.events.watch(path)
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        try:
            self.events.check()
            for path, fd, original in self.entries:
                if identity(os.fstat(fd)) != original or identity(path.lstat()) != original:
                    raise BuildBoundaryChanged('private source snapshot directory changed during compilation')
        except (OSError, BuildBoundaryChanged) as exc:
            raise BuildBoundaryChanged('private source snapshot directory changed during compilation') from exc

    def close(self):
        for _path, fd, _original in self.entries:
            os.close(fd)
        self.entries.clear()
        self.events.close()


class NamedLock:
    """Bind a held flock to its visible name and the owned directory FD.

    The directory itself is also flocked by the caller. Replacing the lock
    entry cannot create a second concurrent publisher in that directory.
    Descendants inherit both guards until the dedicated owner has reaped them.
    """

    def __init__(self, base, directory, lock):
        self.base, self.directory, self.lock = base, directory, lock
        self.base_identity = identity(os.fstat(directory))[:4]
        if self.base_identity[2] & 0o077 or self.base_identity[3] != os.geteuid():
            raise ValueError('build lock directory must be private and owned')
        self.lock_identity = identity(os.fstat(lock))
        self.events = DirectoryEvents(b'.build.lock')
        try:
            self.events.watch(base)
            self.check()
        except BaseException:
            self.events.close()
            raise

    def check(self):
        try:
            self.events.check()
            self.check_names()
        except (OSError, BuildBoundaryChanged) as exc:
            raise BuildBoundaryChanged('build lock name or directory changed') from exc

    def close(self):
        self.events.close()

    def check_names(self):
        visible = self.base.lstat()
        if (identity(visible)[:4] != self.base_identity or
                identity(os.fstat(self.directory))[:4] != self.base_identity or
                self.base.resolve(strict=True) != self.base):
            raise BuildBoundaryChanged('build lock directory changed')
        found = os.stat('.build.lock', dir_fd=self.directory, follow_symlinks=False)
        if identity(found) != self.lock_identity or identity(os.fstat(self.lock)) != self.lock_identity:
            raise BuildBoundaryChanged('build lock name changed')


def search_roots(trace, snapshot, *, include_private=False):
    """Read GCC's actual preprocessor search, including ignored absent roots.

    -Wp,-v prints the search after driver, make, explicit flags and pkg-config
    processing. No Makefile/flag approximation or ambient CPATH is substituted.
    Sets are sufficient here: flag/source/tool identity already binds ordering.
    """
    roots = set()
    starts = ends = 0
    for line in trace.decode().splitlines():
        if line.startswith('#include ') and line.endswith(' search starts here:'):
            starts += 1
        elif line == 'End of search list.':
            ends += 1
        elif line.startswith(('ignoring nonexistent directory "', 'ignoring duplicate directory "')):
            roots.add(line.split('"', 1)[1].rsplit('"', 1)[0])
        elif line.startswith(' '):
            # GCC's search entries are the indented directory-only lines.
            value = line.strip()
            path = Path(value)
            if path.is_dir() or (snapshot / path).is_dir():
                roots.add(value)
    if not starts or not ends:
        raise ValueError('compiler did not report its include search')
    return sorted({os.path.abspath(snapshot / root) for root in roots
                   if include_private or not Path(os.path.abspath(snapshot / root)).is_relative_to(snapshot.parent)})


def search_identity(roots, check):
    """Bind negative lookup history throughout each actual include directory.

    File contents still use the compiler dependency records. Directory entry
    history additionally detects a new earlier header (including nested names
    and __has_include), even though it was not a consumed dependency last time.
    Only these search subtrees are traversed; no shared ancestor ctime is used.
    """
    result = {}
    seen = set()

    def visit(path, depth=0):
        check()
        if len(result) >= 8192 or depth > 64:
            raise ValueError('compiler include search exceeds directory bound')
        key = str(path)
        if key in result:
            return
        try:
            entry = path.lstat()
        except FileNotFoundError:
            result[key] = None
            return
        found = path.stat()
        if not stat.S_ISDIR(found.st_mode):
            result[key] = dict(entry=list(identity(entry)), directory=False)
            return
        result[key] = dict(entry=list(identity(entry)), target=list(identity(found)),
                           resolved=str(path.resolve(strict=True)))
        token = (found.st_dev, found.st_ino)
        if token in seen:
            return
        seen.add(token)
        with os.scandir(path) as children:
            for child in sorted(children, key=lambda item: item.name):
                check()
                if child.is_dir():
                    visit(path / child.name, depth + 1)
        if identity(path.lstat()) != identity(entry) or identity(path.stat()) != identity(found):
            raise ValueError('compiler include search changed during inspection')

    for root in roots:
        visit(Path(root))
    return result
