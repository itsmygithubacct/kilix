"""Bounded I/O for the dedicated multiplexer build command."""
from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time


def read_file(path, maximum=32 * 1024**2, check=lambda: None):
    check()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid not in (0, os.geteuid())
                or before.st_mode & 0o022 or not 0 <= before.st_size <= maximum):
            raise ValueError('build input must be an owned bounded regular file')
        result = bytearray()
        while len(result) < before.st_size:
            check()
            block = os.read(fd, min(1024**2, before.st_size - len(result)))
            if not block:
                raise ValueError('build input ended early')
            result.extend(block)
        after = os.fstat(fd)
        visible = os.stat(path, follow_symlinks=False)
        if (os.read(fd, 1) or (visible.st_dev, visible.st_ino) != (before.st_dev, before.st_ino)
                or any(getattr(before, name) != getattr(after, name) for name in
                       ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_uid'))):
            raise ValueError('build input changed during inspection')
        check()
        return bytes(result)
    finally:
        os.close(fd)


class BuildIO:
    """Only the CLI owns this subreaper; never reap an embedding caller's children."""

    def __init__(self, timeout):
        self.deadline = time.monotonic() + timeout
        self.stopped = False
        self.guard = ()
        self.boundary_check = lambda: None

    def stop(self, _signal, _frame):
        self.stopped = True

    def check(self):
        if self.stopped:
            raise InterruptedError('multiplexer build interrupted')
        if time.monotonic() >= self.deadline:
            raise TimeoutError('multiplexer build deadline exceeded')
        self.boundary_check()

    @staticmethod
    def reap():
        children = Path(f'/proc/self/task/{os.getpid()}/children')
        while True:
            for value in children.read_text().split():
                try:
                    os.kill(int(value), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            while True:
                try:
                    pid, _status = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    return
                if not pid:
                    break
            time.sleep(.005)

    def run(self, argv, env, *, cwd=None, maximum=4 * 1024**2, allowed=(0,)):
        self.check()
        child = None
        output = bytearray()
        try:
            child = subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     start_new_session=True, pass_fds=self.guard)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    self.check()
                    if child.poll() is not None:
                        self.reap()
                    for key, _events in selector.select(.025):
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                        else:
                            output.extend(block)
                            if len(output) > maximum:
                                raise ValueError('build command output exceeds its bound')
                while child.poll() is None:
                    self.check()
                    time.sleep(.01)
            self.reap()
            self.check()
            if child.returncode not in allowed:
                raise ValueError('build command failed: ' + output[-4000:].decode('utf-8', 'replace'))
            return child.returncode, bytes(output)
        finally:
            if child is not None:
                if child.poll() is None:
                    child.kill()
                self.reap()
                child.stdout.close()

    def supervise(self):
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0):
            raise OSError('cannot supervise multiplexer build')
        for number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(number, self.stop)


def source_files(source, expected, io, environment):
    """Read and hash raw objects, independent of index flags and Git attributes."""
    def obj(kind, oid, maximum):
        _code, payload = io.run(['/usr/bin/git', '--no-replace-objects', '-C', str(source),
                                 'cat-file', kind, oid], environment, maximum=maximum)
        identity = kind.encode() + b' ' + str(len(payload)).encode() + b'\0' + payload
        if hashlib.sha1(identity).hexdigest() != oid:
            raise ValueError('multiplexer raw Git object identity differs')
        return payload

    commit = obj('commit', expected, 65536)
    first = commit.split(b'\n', 1)[0]
    if not first.startswith(b'tree ') or len(first) != 45:
        raise ValueError('multiplexer commit has no canonical tree')
    tree = first[5:].decode('ascii')
    files = {}
    total = 0

    def visit(oid, prefix='', depth=0):
        nonlocal total
        if depth > 12:
            raise ValueError('multiplexer source nesting exceeds bound')
        payload = obj('tree', oid, 2 * 1024**2)
        while payload:
            meta, zero, tail = payload.partition(b'\0')
            if not zero or len(tail) < 20:
                raise ValueError('invalid multiplexer source tree')
            mode, space, raw = meta.partition(b' ')
            name = raw.decode('utf-8')
            if (not space or not name or name in ('.', '..') or '/' in name
                    or any(ord(ch) < 32 for ch in name)):
                raise ValueError('unsafe multiplexer source name')
            child, payload = tail[:20].hex(), tail[20:]
            path = prefix + name
            if mode == b'40000':
                visit(child, path + '/', depth + 1)
            else:
                if mode not in (b'100644', b'100755') or path in files or len(files) >= 512:
                    raise ValueError('unsupported multiplexer source population')
                value = obj('blob', child, 2 * 1024**2)
                total += len(value)
                if total > 16 * 1024**2:
                    raise ValueError('multiplexer source exceeds byte bound')
                files[path] = (int(mode, 8) & 0o777, value)
    visit(tree)
    if not {'Makefile', 'include/kilix_mux.h'} <= files.keys():
        raise ValueError('multiplexer source is incomplete')
    check_source(source, expected, files, io, environment)
    return tree, files


def check_source(source, expected, files, io, environment):
    for args in (['rev-parse', 'HEAD'], ['status', '--porcelain', '--untracked-files=all']):
        _code, value = io.run(['/usr/bin/git', '--no-replace-objects', '-C', str(source), *args], environment)
        if value.decode().strip() != (expected if args[0] == 'rev-parse' else ''):
            raise ValueError('multiplexer source must be clean at its selected commit')
    for name, (mode, value) in files.items():
        path = source / name
        if (path.parent != path.parent.resolve(strict=True)
                or read_file(path, 2 * 1024**2, io.check) != value
                or bool(path.stat().st_mode & 0o100) != bool(mode & 0o100)):
            raise ValueError('multiplexer source must be clean and match every raw committed file')
