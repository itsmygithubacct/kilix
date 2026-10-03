#!/usr/bin/python3 -I
"""Launch a bound installed-model transcription generation."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time


def trusted_chain(path, *, private=True):
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('managed path must be absolute and canonical')
    for directory in (*reversed(path.parents), path):
        info = directory.lstat()
        sticky_root = directory != path and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                or (info.st_mode & 0o022 and not sticky_root)):
            raise ValueError('managed directory chain is unsafe')
    if info.st_uid != os.geteuid() or (private and info.st_mode & 0o077):
        raise ValueError('managed root must be owned and private')


def file_digest(path, *, deadline, check):
    check()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('managed file is unsafe')
        digest = hashlib.sha256()
        remaining = info.st_size
        while remaining:
            check()
            if time.monotonic() > deadline:
                raise ValueError('managed file verification deadline expired')
            block = source.read(min(remaining, 1024**2))
            if not block:
                raise ValueError('managed file ended early')
            digest.update(block)
            remaining -= len(block)
        after = os.fstat(source.fileno())
        named = path.lstat()
        for observed in (after, named):
            if any(getattr(observed, field) != getattr(info, field) for field in
                   ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
                raise ValueError('managed file changed during verification')
    return digest.digest()


def bounded_paths(root, *, maximum, deadline, check):
    paths = []
    for path in root.rglob('*'):
        check()
        if len(paths) >= maximum or time.monotonic() > deadline:
            raise ValueError('managed population exceeds its bound')
        paths.append(path)
    return sorted(paths)


def tree_digest(root, check=lambda: None):
    """Bind managed Python files and link targets; omit ordinary bytecode caches."""
    trusted_chain(root, private=False)
    digest = hashlib.sha256()
    deadline = time.monotonic() + 60
    count = total = 0
    for path in bounded_paths(root, maximum=20000, deadline=deadline, check=check):
        check()
        if '__pycache__' in path.parts or path.suffix in {'.pyc', '.pyo'}:
            continue
        count += 1
        if count > 20000 or time.monotonic() > deadline:
            raise ValueError('managed Python population exceeds its bound')
        info = path.lstat()
        if info.st_uid != os.geteuid() or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
            raise ValueError('managed Python contains unsafe entries')
        if path.is_symlink():
            target = path.resolve(strict=True)
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError('managed Python has an external or directory link')
            trusted_chain(target.parent, private=False)
            target_info = target.lstat()
            total += target_info.st_size
            if total > 1024**3:
                raise ValueError('managed Python bytes exceed their bound')
            digest.update(str(path.relative_to(root)).encode() + b'\0link\0' + os.readlink(path).encode() + b'\0')
            digest.update(str(stat.S_IMODE(target_info.st_mode)).encode() + b'\0')
            digest.update(file_digest(target, deadline=deadline, check=check))
            continue
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('managed Python contains a special file')
        total += info.st_size
        if total > 1024**3:
            raise ValueError('managed Python bytes exceed their bound')
        digest.update(str(path.relative_to(root)).encode() + b'\0')
        digest.update(str(stat.S_IMODE(info.st_mode)).encode() + b'\0')
        digest.update(file_digest(path, deadline=deadline, check=check))
    trusted_chain(root, private=False)
    return digest.hexdigest()


def file_population(root, check=lambda: None):
    trusted_chain(root)
    result = {}
    deadline = time.monotonic() + 60
    count = total = 0
    for path in bounded_paths(root, maximum=4096, deadline=deadline, check=check):
        check()
        count += 1
        if count > 4096 or time.monotonic() > deadline:
            raise ValueError('transcription generation population exceeds its bound')
        info = path.lstat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('transcription generation contains unsafe entries')
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > 32 * 1024**2:
            raise ValueError('transcription generation contains an unbounded or non-regular file')
        total += info.st_size
        if total > 256 * 1024**2:
            raise ValueError('transcription generation bytes exceed their bound')
        result[str(path.relative_to(root))] = file_digest(path, deadline=deadline, check=check).hex()
    trusted_chain(root)
    return result


def bound_command(value, generation, arguments):
    if not arguments or arguments[0] not in {'serve', 'status', 'models', 'file', 'record', 'unload', 'cancel'}:
        raise ValueError('usage: kilix transcribe serve|status|models|file|record|unload|cancel')
    if arguments[0] == 'serve':
        if len(arguments) != 1:
            raise ValueError('serve uses the prepared installed-model selection')
        return ['serve', '--runtime-root', str(generation / 'runtime'), '--installed-asset', value['model'],
                '--content-root', value['content_root'], '--model-snapshot-bytes', str(value['budget'])]
    return arguments


def main():
    generation = Path(__file__).resolve().parent.parent
    trusted_chain(generation)
    path = generation / 'binding.json'
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077 or not 0 < info.st_size <= 65536):
        raise ValueError('unsafe transcription binding')
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != '@BINDING_SHA256@':
        raise ValueError('transcription binding changed; prepare a new generation')
    value = json.loads(payload)
    if (file_population(generation / 'lib') != value['files']
            or file_population(generation / 'runtime') != value['runtime_files']):
        raise ValueError('transcription generation bytes changed; prepare a new generation')
    python = Path(value['python'])
    python_root = Path(value['python_root'])
    if (not python.is_absolute() or not python.resolve(strict=True).is_relative_to(python_root)
            or tree_digest(python_root) != value['python_root_sha256']):
        raise ValueError('managed transcription Python changed; prepare a new generation')
    command = bound_command(value, generation, sys.argv[1:])
    environment = {key: entry for key, entry in os.environ.items()
                   if not key.startswith(('PYTHON', 'UV_', 'PIP_'))
                   and key not in {'VIRTUAL_ENV', 'CONDA_PREFIX', 'KILIX_CONTENT_ROOT',
                                   'GPU_TERMINAL_HOME', 'KILIX_LICENSE_RECEIPTS', 'XDG_RUNTIME_DIR', 'XDG_STATE_HOME'}}
    environment.update(value['environment'])
    environment['KILIX_CONTENT_ROOT'] = value['content_root']
    bootstrap = ('import sys;sys.path.insert(0,sys.argv[1]);'
                 'from kilix_transcribe.cli import main;raise SystemExit(main(sys.argv[2:]))')
    os.execve(str(python), [str(python), '-I', '-S', '-B', '-X', 'pycache_prefix=/dev/null',
                          '-c', bootstrap, str(generation / 'lib'), *command], environment)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('kilix transcribe: ' + str(error))
