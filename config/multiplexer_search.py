"""Retain actual compiler search history from admission through publication."""
from __future__ import annotations

import ctypes
import array
import os
from pathlib import Path
import re
import socket
import stat
import struct

from multiplexer_build_guards import BuildBoundaryChanged, search_identity, search_roots

MAX_PACKET = 65536


def receive(peer, size):
    raw, controls, flags, _address = peer.recvmsg(size, 256, socket.MSG_CMSG_CLOEXEC)
    credentials = []
    invalid = bool(flags & ~socket.MSG_CMSG_CLOEXEC)
    for level, kind, value in controls:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            descriptors = array.array('i')
            descriptors.frombytes(value[:len(value) - len(value) % descriptors.itemsize])
            for fd in descriptors:
                os.close(fd)
            invalid = True
        elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
            credentials.append(value)
        else:
            invalid = True
    if invalid:
        raise BuildBoundaryChanged('invalid compiler search request controls')
    return raw, credentials


class SearchHistory:
    """One continuing inotify population for all admitted GCC invocations.

    Relevant directories watch every name. Ancestors watch only the next path
    component, including the first absent component. This does not confuse an
    unrelated sibling write with changing the compiler's search population.
    """

    def __init__(self):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), 'cannot retain compiler search history')
        self.watches = {}
        self.paths = set()
        self.ancestor_paths = set()
        self.roots = set()
        self.state = {}
        self.failed = False

    def watch(self, path, name=None):
        token = (str(path), name)
        if token in self.paths:
            return
        if len(self.paths) >= 32768:
            raise BuildBoundaryChanged('compiler search watch population exceeds bound')
        # Actual writes/attributes/name changes plus self/watch loss; opening
        # and merely closing a writable FD without modification is harmless.
        mask = 0x00000fc6 | 0x01000000 | 0x02000000
        wd = self.libc.inotify_add_watch(self.fd, os.fsencode(path), mask)
        if wd < 0:
            raise OSError(ctypes.get_errno(), 'compiler search watch unavailable')
        if wd not in self.watches:
            self.watches[wd] = set()
        if name is None:
            self.watches[wd] = None
        elif self.watches[wd] is not None:
            self.watches[wd].add(os.fsencode(name))
        self.paths.add(token)

    def ancestors(self, path):
        # Both the requested name and resolved target chain need history.
        for child in (path, *path.parents):
            if str(child) in self.ancestor_paths:
                break
            parent = child.parent
            if parent == child:
                break
            try:
                target = parent.resolve(strict=True)
            except FileNotFoundError:
                continue
            if not target.is_dir():
                raise BuildBoundaryChanged('compiler search ancestor is not a directory')
            self.watch(target, child.name)
            self.ancestor_paths.add(str(child))

    def add(self, roots, check):
        self.check()
        if set(roots) <= self.roots:
            # These paths already have uninterrupted owner-held history.
            # Do not traverse the same system tree for every translation unit.
            check()
            return
        snapshot = search_identity(roots, check)
        for name, row in snapshot.items():
            check()
            path = Path(name)
            self.ancestors(path)
            if row is not None and row.get('directory', True):
                target = Path(row['resolved'])
                self.ancestors(target)
                self.watch(target)
            if name in self.state and self.state[name] != row:
                raise BuildBoundaryChanged('compiler search changed between invocations')
        self.check()
        if search_identity(roots, check) != snapshot:
            raise BuildBoundaryChanged('compiler search changed during admission')
        self.state.update(snapshot)
        self.roots.update(roots)

    def check(self):
        if self.failed:
            raise BuildBoundaryChanged('compiler search history is already invalid')
        while True:
            try:
                block = os.read(self.fd, 65536)
            except BlockingIOError:
                return
            if not block:
                self.failed = True
                raise BuildBoundaryChanged('compiler search history channel ended')
            offset = 0
            while offset < len(block):
                if len(block) - offset < 16:
                    self.failed = True
                    raise BuildBoundaryChanged('compiler search history is incomplete')
                wd, _mask, _cookie, size = struct.unpack_from('iIII', block, offset)
                if offset + 16 + size > len(block):
                    self.failed = True
                    raise BuildBoundaryChanged('compiler search history is truncated')
                name = block[offset + 16:offset + 16 + size].split(b'\0', 1)[0]
                selected = self.watches.get(wd)
                if wd not in self.watches or not name or selected is None or name in selected:
                    self.failed = True
                    raise BuildBoundaryChanged('compiler include search changed during compilation')
                offset += 16 + size

    def close(self):
        os.close(self.fd)


class SearchAdmission:
    """The build owner acknowledges search registration before GCC starts.

    The captured helper discovers GCC's actual search with the same argv and
    a preprocessing-only pass. It waits for the owner to retain the watches,
    then execs the original compile. Only actual compiler logs contribute to
    the final roots/dependencies; discovery output cannot stand in for them.
    """

    def __init__(self, endpoint, snapshot, diagnostics, check):
        self.snapshot, self.diagnostics, self.operation_check = snapshot, diagnostics, check
        self.history = SearchHistory()
        self.pending = []
        self.received = {}
        self.logs = {}
        self.endpoint = Path(endpoint)
        self.listener = None
        try:
            self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            self.listener.bind(str(endpoint))
            info = self.endpoint.lstat()
            self.socket_identity = (info.st_dev, info.st_ino)
            self.listener.listen(128)
            self.listener.setblocking(False)
        except BaseException:
            if self.listener is not None:
                self.listener.close()
            self.history.close()
            raise

    def check(self):
        self.history.check()
        while True:
            try:
                peer, _address = self.listener.accept()
            except BlockingIOError:
                break
            try:
                peer.setblocking(False)
                self.pending.append(peer)
            except BaseException:
                peer.close()
                raise
            if len(self.pending) > 128:
                raise BuildBoundaryChanged('compiler search pending population exceeds bound')
        for peer in self.pending[:]:
            if peer not in self.received:
                try:
                    raw, credentials = receive(peer, MAX_PACKET)
                except BlockingIOError:
                    continue
                if not raw or credentials != [peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)]:
                    raise BuildBoundaryChanged('invalid compiler search request framing')
                self.received[peer] = raw
            try:
                extra, credentials = receive(peer, 1)
            except BlockingIOError:
                continue
            # An empty packet carries credentials; only an actual half-close
            # ends the one-record request. Refuse extra records before READY.
            if extra or credentials:
                raise BuildBoundaryChanged('extra compiler search request record')
            raw = self.received[peer]
            pid, uid, _gid = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if pid <= 0 or uid != os.geteuid():
                raise BuildBoundaryChanged('compiler search requester identity differs')
            ancestor = pid
            for _ in range(128):
                if ancestor == os.getpid():
                    break
                if ancestor <= 1:
                    raise BuildBoundaryChanged('compiler search requester is not owned')
                fields = Path('/proc', str(ancestor), 'stat').read_text().rsplit(') ', 1)[1].split()
                ancestor = int(fields[1])
            else:
                raise BuildBoundaryChanged('compiler search requester ancestry exceeds bound')
            name, separator, trace = raw.partition(b'\0')
            if not separator or not re.fullmatch(rb'gcc-[a-zA-Z0-9_-]+\.log', name):
                raise BuildBoundaryChanged('invalid compiler search request')
            name = name.decode('ascii')
            if name in self.logs:
                raise BuildBoundaryChanged('duplicate compiler search invocation')
            path = self.diagnostics / name
            found = path.lstat()
            if not stat.S_ISREG(found.st_mode) or found.st_uid != uid or found.st_mode & 0o077 or found.st_nlink != 1 or found.st_size:
                raise BuildBoundaryChanged('compiler diagnostic identity differs at admission')
            # Link-only GCC invocations have no preprocessing search. The
            # actual complete build must still report its consumed headers.
            roots = search_roots(trace, self.snapshot, include_private=True) if b'#include ' in trace else []
            self.history.add(roots, self.operation_check)
            self.logs[name] = (found.st_dev, found.st_ino)
            if len(self.logs) > 512:
                raise BuildBoundaryChanged('compiler search invocation population exceeds bound')
            self.history.check()
            peer.sendall(b'READY')
            peer.close()
            self.pending.remove(peer)
            del self.received[peer]

    def finish(self, actual_trace):
        self.check()
        if self.pending or not self.logs:
            raise BuildBoundaryChanged('compiler search admission did not complete')
        actual = search_roots(actual_trace, self.snapshot, include_private=True)
        if set(actual) != self.history.roots:
            raise BuildBoundaryChanged('actual compiler search differs from admitted history')
        found = {}
        for path in self.diagnostics.iterdir():
            info = path.lstat()
            found[path.name] = (info.st_dev, info.st_ino)
        if found != self.logs:
            raise BuildBoundaryChanged('actual compiler diagnostics differ from admitted invocations')
        if search_identity(sorted(self.history.roots), self.operation_check) != self.history.state:
            raise BuildBoundaryChanged('compiler search identity changed before publication')
        self.history.check()

    def close(self):
        for peer in self.pending:
            peer.close()
        self.pending.clear()
        self.received.clear()
        self.listener.close()
        self.history.close()
        try:
            info = self.endpoint.lstat()
        except FileNotFoundError:
            return
        if (info.st_dev, info.st_ino) == self.socket_identity:
            self.endpoint.unlink()


def request_search(endpoint, name, trace):
    packet = name.encode('ascii') + b'\0' + trace
    if len(packet) > MAX_PACKET:
        raise ValueError('compiler search request exceeds bound')
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC) as peer:
        peer.connect(endpoint)
        peer.sendall(packet)
        peer.shutdown(socket.SHUT_WR)
        response, credentials = receive(peer, 16)
        if response != b'READY' or credentials:
            raise ValueError('compiler search admission unavailable')
