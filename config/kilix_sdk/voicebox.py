"""Offline Voicebox generations and their bounded private-tab product lifetime.

This module is also packaged as launch.py. It is the single lifecycle
implementation used by the host and the optional OS provision/update hooks.
Model installation and shared provider ownership remain outside this module.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

SCHEMA = 'kilix.voicebox.web-delivery/v1'
SELECTION = 'kilix.voicebox.selection/v1'
HEX = re.compile(r'[0-9a-f]{64}')
MAX_BYTES = 1024**3
MAX_MEMBERS = 40000
LAUNCH_LOG_TAIL_BYTES = 262144


class Refused(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def private(path: Path, create=False):
    require(path.is_absolute() and '..' not in path.parts, 'absolute canonical path required')
    for part in (*reversed(path.parents), path):
        if create:
            part.mkdir(mode=0o700, exist_ok=True)
        info = part.lstat()
        sticky = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()}
                and (not info.st_mode & 0o022 or sticky), 'unsafe directory chain')
    info = path.lstat()
    require(info.st_uid == os.geteuid() and not info.st_mode & 0o077,
            'directory must be private and owned')


def read_file(path: Path, maximum=MAX_BYTES):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and 0 <= info.st_size <= maximum,
                'invalid regular file or size')
        result = bytearray()
        while len(result) <= info.st_size:
            data = os.read(fd, min(1048576, info.st_size + 1 - len(result)))
            if not data:
                break
            result.extend(data)
        after = os.fstat(fd)
        require(len(result) == info.st_size and (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'file changed while reading')
        return bytes(result)
    finally:
        os.close(fd)


def read_log_tail(path: Path, token: str):
    """One bounded read of an owned regular log, never a whole-file read.

    Read extra token overlap before redaction and trimming so a credential
    crossing the retained tail boundary cannot leave a token suffix. A growing
    or truncated log is a best-effort snapshot, not a reason to keep a profile.
    """
    marker = token.encode('ascii')
    require(bool(HEX.fullmatch(token)), 'invalid launch credential')
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and info.st_nlink == 1 and info.st_size >= 0, 'invalid owned regular log')
        count = min(info.st_size, LAUNCH_LOG_TAIL_BYTES + len(marker) - 1)
        data = os.pread(fd, count, info.st_size - count)
        require(len(data) == count, 'log truncated while reading')
        # Preserve byte positions while redacting: shortening replacements
        # could bring the otherwise discarded overlap back into the tail.
        replacement = b'[redacted]'.ljust(len(marker), b' ')
        return data.replace(marker, replacement)[-LAUNCH_LOG_TAIL_BYTES:]
    finally:
        os.close(fd)


def retain_launch_logs(root: Path, owned: Path, token: str):
    """Keep only bounded redacted logs; never copy disposable browser state."""
    chunks = []
    for name in ('backend.log', 'browser.log'):
        try:
            data = read_log_tail(owned / name, token)
        except (OSError, Refused) as error:
            data = ('unavailable (' + type(error).__name__ + ')').encode('ascii')
        chunks.append(name.encode('ascii') + b':\n' + data)
    diagnostics = root / 'launch-logs'
    private(diagnostics, create=True)
    directory = os.open(diagnostics, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fd = os.open(owned.name + '.log', os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            os.fchmod(fd, 0o600)
            data = memoryview(b'\n'.join(chunks))
            while data:
                written = os.write(fd, data)
                require(written > 0, 'launch diagnostic write made no progress')
                data = data[written:]
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def sha(path):
    return hashlib.sha256(read_file(path)).hexdigest()


def root_path():
    base = os.environ.get('KILIX_DATA_HOME')
    if not base:
        base = str(Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'kilix')
    return Path(base) / 'voicebox'


@contextmanager
def locked(root, exclusive=True):
    private(root, create=True)
    inherited = os.environ.get('KILIX_VOICEBOX_TRANSACTION_FD')
    fd = os.dup(int(inherited)) if inherited else os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                and not info.st_mode & 0o077 and os.path.samestat(info, root.lstat()), 'unsafe lifecycle lock')
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('Voicebox is in use or another lifecycle operation is running') from None
        yield
    finally:
        os.close(fd)


def selection(root):
    path = root / 'selection.json'
    if not path.exists() and not path.is_symlink():
        return {'schema': SELECTION, 'current': None, 'previous': None}
    return selection_bytes(read_file(path, 4096))


def selection_bytes(raw):
    require(len(raw) <= 4096, 'oversized selection record')
    value = json.loads(raw)
    require(set(value) == {'schema', 'current', 'previous'} and value['schema'] == SELECTION,
            'invalid selection record')
    require(all(v is None or type(v) is str and HEX.fullmatch(v)
                for v in (value['current'], value['previous'])), 'invalid generation identity')
    return value


def selection_snapshot(root):
    path = root / 'selection.json'
    if not path.exists() and not path.is_symlink():
        return {'present': False, 'bytes_base64': None}
    raw = read_file(path, 4096)
    selection_bytes(raw)
    return {'present': True, 'bytes_base64': base64.b64encode(raw).decode('ascii')}


def restore_selection(root, record):
    require(type(record) is dict and set(record) == {'present', 'bytes_base64'}
            and type(record['present']) is bool, 'invalid selection snapshot')
    if not record['present']:
        require(record['bytes_base64'] is None, 'invalid absent selection snapshot')
        (root / 'selection.json').unlink(missing_ok=True)
    else:
        require(type(record['bytes_base64']) is str and len(record['bytes_base64']) <= 5464,
                'invalid selection snapshot bytes')
        raw = base64.b64decode(record['bytes_base64'], validate=True)
        selection_bytes(raw)
        fd, name = tempfile.mkstemp(prefix='.selection-restore-', dir=root)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, root / 'selection.json')
        finally:
            Path(name).unlink(missing_ok=True)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def select(root, current, previous):
    value = {'schema': SELECTION, 'current': current, 'previous': previous}
    fd, name = tempfile.mkstemp(prefix='.selection-', dir=root)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, sort_keys=True)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, root / 'selection.json')
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def safe_name(name):
    p = PurePosixPath(name)
    require(bool(name) and str(p) == name and not p.is_absolute()
            and all(x not in {'.', '..', ''} for x in p.parts) and '\\' not in name,
            'unsafe package member')
    return p


def manifest(raw):
    require(len(raw) <= 16 * 1024**2, 'oversized delivery manifest')
    value = json.loads(raw)
    require(value.get('schema') == SCHEMA and type(value.get('files')) is list
            and 1 <= len(value['files']) <= MAX_MEMBERS, 'invalid delivery manifest')
    names = set()
    total = 0
    for row in value['files']:
        require(set(row) == {'path', 'sha256', 'bytes', 'mode'}, 'invalid payload row')
        safe_name(row['path'])
        require(row['path'] != 'DELIVERY.json' and row['path'] not in names
                and HEX.fullmatch(row['sha256']) and type(row['bytes']) is int
                and 0 <= row['bytes'] <= MAX_BYTES and row['mode'] in {0o600, 0o700}, 'invalid payload binding')
        names.add(row['path'])
        total += row['bytes']
    require(total <= MAX_BYTES and {'launch.py', 'lib/kilix_voicebox_backend/server.py',
            'python/bin/python3.12', 'ui/index.html'} <= names, 'incomplete package')
    return value


def verify(directory):
    private(directory)
    value = manifest(read_file(directory / 'DELIVERY.json', 16 * 1024**2))
    expected = {row['path']: row for row in value['files']}
    found = set()
    for parent, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs:
            path = Path(parent) / name
            require(not path.is_symlink(), 'package directory link refused')
        for name in files:
            path = Path(parent) / name
            relative = path.relative_to(directory).as_posix()
            if relative == 'DELIVERY.json':
                continue
            require(relative in expected, 'undeclared installed payload')
            info = path.lstat()
            row = expected[relative]
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == row['mode']
                    and info.st_size == row['bytes'] and sha(path) == row['sha256'], 'installed payload changed')
            found.add(relative)
    require(found == set(expected), 'installed package population differs')
    return value


def verify_generation(root, identity):
    require(type(identity) is str and HEX.fullmatch(identity), 'Voicebox is not installed')
    directory = root / 'generations' / identity
    value = verify(directory)
    # The retained receipt is outside executable bytes and binds the manifest
    # selected by the digest-checked archive. A changed manifest also refuses.
    receipt = json.loads(read_file(root / 'receipts' / (identity + '.json'), 4096))
    require(receipt == {'archive_sha256': identity, 'manifest_sha256': sha(directory / 'DELIVERY.json')},
            'installed manifest changed')
    return directory, value


def install(root, package, digest):
    require(type(digest) is str and HEX.fullmatch(digest), 'exact package SHA256 required')
    with locked(root):
        old = selection(root)
        # Hold a private bounded copy, never extract from a source that can
        # change between hashing and extraction.
        raw = read_file(package)
        require(hashlib.sha256(raw).hexdigest() == digest, 'package SHA256 mismatch')
        import io
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
            members = archive.getmembers()
            require(1 <= len(members) <= MAX_MEMBERS + 1, 'package population exceeds bound')
            require(all(m.isfile() and safe_name(m.name) for m in members), 'links or special package members refused')
            require(len({m.name for m in members}) == len(members), 'duplicate package member')
            declared = next((m for m in members if m.name == 'DELIVERY.json'), None)
            require(declared is not None and declared.size <= 16 * 1024**2, 'missing delivery manifest')
            document = archive.extractfile(declared).read()
            value = manifest(document)
            rows = {row['path']: row for row in value['files']}
            require(set(rows) | {'DELIVERY.json'} == {m.name for m in members}, 'archive population differs')
            for item in members:
                if item.name != 'DELIVERY.json':
                    require(item.size == rows[item.name]['bytes'] and item.mode == rows[item.name]['mode'],
                            'archive member binding differs')
            generations = root / 'generations'
            receipts = root / 'receipts'
            private(generations, create=True)
            private(receipts, create=True)
            if (generations / digest).exists():
                verify_generation(root, digest)
            else:
                stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=generations))
                try:
                    for item in members:
                        target = stage / item.name
                        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                        with target.open('xb') as out, archive.extractfile(item) as src:
                            shutil.copyfileobj(src, out, 1048576)
                        target.chmod(0o600 if item.name == 'DELIVERY.json' else rows[item.name]['mode'])
                    verify(stage)
                    receipt = {'archive_sha256': digest, 'manifest_sha256': hashlib.sha256(document).hexdigest()}
                    receipt_path = receipts / (digest + '.json')
                    if receipt_path.exists():
                        require(json.loads(read_file(receipt_path, 4096)) == receipt,
                                'partial installation receipt differs')
                    else:
                        with receipt_path.open('x') as out:
                            json.dump(receipt, out)
                            out.flush()
                            os.fsync(out.fileno())
                        receipt_path.chmod(0o600)
                    os.rename(stage, generations / digest)
                except BaseException:
                    # Keep partial payload and its diagnostics for inspection;
                    # it is never selected or executable through the host.
                    raise
            select(root, digest, old['current'] if old['current'] != digest else old['previous'])
            return digest


def rollback(root):
    with locked(root):
        old = selection(root)
        require(old['previous'] is not None, 'no previous Voicebox generation')
        verify_generation(root, old['previous'])
        select(root, old['previous'], old['current'])


def uninstall(root):
    with locked(root):
        old = selection(root)
        select(root, None, old['current'] or old['previous'])


def clean_environment():
    # Provider sockets and XDG data roots are inherited intentionally. Startup
    # injection, user package settings, remote debugging and external URLs are
    # never forwarded to the installed backend or browser.
    keep = {'HOME', 'PATH', 'LANG', 'LC_ALL', 'DISPLAY', 'XAUTHORITY',
        'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR', 'XDG_DATA_HOME',
        'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'XDG_SESSION_ID',
        'KILIX_DATA_HOME', 'GPU_TERMINAL_HOME', 'KILIX_LICENSE_RECEIPTS',
        'PULSE_SERVER', 'PULSE_SINK', 'TMPDIR'}
    return {k: v for k, v in os.environ.items() if k in keep}


def stop(process):
    if process is None:
        return
    descendants = []
    # The new session belongs to this exact Popen child. Record each surviving
    # member's kernel start time before teardown, then signal only that identity.
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():
            continue
        try:
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[3]) == process.pid and int(fields[19]) >= process._voicebox_start:
                descendants.append((int(path.name), int(fields[19])))
        except (OSError, ValueError, AttributeError, IndexError):
            continue
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=7)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    for signum in (signal.SIGTERM, signal.SIGKILL):
        for pid, started in descendants:
            if pid == process.pid:
                continue
            try:
                fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                if int(fields[19]) == started and int(fields[3]) == process.pid:
                    os.kill(pid, signum)
            except (OSError, ValueError, IndexError):
                pass
        if signum == signal.SIGTERM:
            time.sleep(0.1)


def record_child(process):
    try:
        fields = Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()
        process._voicebox_start = int(fields[19])
    except (OSError, ValueError, IndexError):
        process._voicebox_start = 0
    return process


def stop_orphans():
    """Reap descendants adopted by this launch's kernel child-subreaper."""
    for signum in (signal.SIGTERM, signal.SIGKILL):
        parents = {}
        for path in Path('/proc').iterdir():
            if not path.name.isdecimal():
                continue
            try:
                fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
                parents[int(path.name)] = (int(fields[1]), int(fields[19]))
            except (OSError, ValueError, IndexError):
                continue
        owned = {os.getpid()}
        while True:
            children = {pid for pid, (parent, _) in parents.items() if parent in owned}
            if children <= owned:
                break
            owned |= children
        for pid in sorted(owned - {os.getpid()}, reverse=True):
            try:
                fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                if int(fields[19]) == parents[pid][1]:
                    os.kill(pid, signum)
            except (OSError, ValueError, IndexError):
                pass
        time.sleep(0.1)
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                break
        except ChildProcessError:
            break


def launch(root, identity, browser):
    require(os.environ.get('DISPLAY') and os.environ.get('XAUTHORITY'),
            'launch requires the Kilix private authenticated display; use kilix voicebox run')
    require(os.environ.get('DBUS_SESSION_BUS_ADDRESS'), 'launch requires a private desktop bus')
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', ''))
    private(runtime)
    with locked(root, exclusive=False):
        require(selection(root)['current'] == identity, 'selection changed before launch')
        directory, value = verify_generation(root, identity)
        browser = Path(browser)
        require(browser.is_absolute() and browser.is_file() and os.access(browser, os.X_OK),
                'Chromium prerequisite missing; no host-browser fallback')
        import ctypes
        require(ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) == 0, 'owned child-subreaper unavailable')
        # The inherited socket reserves the port until uvicorn owns it.
        token = secrets.token_hex(32)
        listener = socket.socket()
        owned = None
        backend = ui = None
        previous = {}
        def interrupted(_signal, _frame):
            raise KeyboardInterrupt
        try:
            listener.bind(('127.0.0.1', 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            owned = Path(tempfile.mkdtemp(prefix='voicebox-', dir=runtime))
            environment = clean_environment()
            environment.update(KILIX_VOICEBOX_SESSION_TOKEN=token, TMPDIR=str(owned))
            url = f'http://127.0.0.1:{port}'
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                previous[signum] = signal.signal(signum, interrupted)
            python = directory / 'python/bin/python3.12'
            bootstrap = 'import sys,runpy;sys.path[:0]=sys.argv[1:4];del sys.argv[1:4];runpy.run_module("kilix_voicebox_backend.server",run_name="__main__")'
            with (owned / 'backend.log').open('wb') as log:
                backend = record_child(subprocess.Popen([str(python), '-I', '-S', '-B', '-c', bootstrap,
                    str(directory / 'lib'), str(directory / 'env'), str(directory / 'shared'), '--port', str(port),
                    '--listen-fd', str(listener.fileno()), '--ui-dir', str(directory / 'ui')],
                    pass_fds=(listener.fileno(),), env=environment, cwd=owned,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True))
            listener.close()
            deadline = time.monotonic() + 20
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while True:
                require(backend.poll() is None, 'installed backend failed; see retained launch diagnostics')
                try:
                    request = urllib.request.Request(url + '/health', headers={'X-Kilix-Voicebox-Session': token})
                    with opener.open(request, timeout=0.25) as response:
                        require(response.status == 200, 'backend readiness refused')
                    break
                except OSError:
                    require(time.monotonic() < deadline, 'backend readiness deadline exceeded')
                    time.sleep(0.05)
            profile = owned / 'browser'
            profile.mkdir(mode=0o700)
            # Explicit exports survive disposable browser cleanup.
            exports = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'kilix-voicebox/exports'
            private(exports, create=True)
            (profile / 'Default').mkdir(mode=0o700)
            (profile / 'Default/Preferences').write_text(json.dumps({'download': {
                'default_directory': str(exports), 'prompt_for_download': False},
                'credentials_enable_service': False, 'profile': {'password_manager_enabled': False}}))
            browser_env = clean_environment()
            browser_env.pop('KILIX_LICENSE_RECEIPTS', None)
            with (owned / 'browser.log').open('wb') as log:
                ui = record_child(subprocess.Popen([str(browser), '--user-data-dir=' + str(profile),
                    '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
                    '--disable-component-update', '--disable-sync', '--disable-extensions',
                    '--disable-features=MediaRouter', '--password-store=basic',
                    '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                    '--app=' + url + '/ui/#session=' + token], env=browser_env, cwd=owned,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True))
            while ui.poll() is None:
                require(backend.poll() is None, 'backend disconnected; closing owned window')
                time.sleep(0.1)
            require(ui.returncode == 0, 'installed private window failed')
        finally:
            try:
                # Exact original owned-child shutdown, in its original order.
                # ExitStack attempts every callback even on failure/cancellation.
                with ExitStack() as cleanup:
                    if owned is not None:
                        cleanup.callback(retain_launch_logs, root, owned, token)
                    for signum, handler in reversed(tuple(previous.items())):
                        cleanup.callback(signal.signal, signum, handler)
                    cleanup.callback(listener.close)
                    if backend is not None:
                        cleanup.callback(stop_orphans)
                    cleanup.callback(stop, backend)
                    cleanup.callback(stop, ui)
            finally:
                # Diagnostics and child-shutdown failures cannot bypass disposal.
                # Explicit exports and every unrelated profile live outside owned.
                if owned is not None:
                    shutil.rmtree(owned)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('operation', choices=('prepare', 'install', 'update', 'status', 'run', 'launch', 'rollback', 'uninstall', 'verify', 'lockcheck', 'selection-snapshot', 'selection-restore'))
    parser.add_argument('--root', type=Path, default=root_path())
    parser.add_argument('--package', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--generation')
    parser.add_argument('--browser', type=Path, default=Path('/usr/bin/chromium'))
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.operation in {'prepare', 'install', 'update'}:
            require(args.package is not None, 'explicit offline package required')
            print(install(args.root, args.package, args.sha256))
        elif args.operation == 'rollback':
            rollback(args.root)
        elif args.operation == 'uninstall':
            uninstall(args.root)
        elif args.operation == 'launch':
            launch(args.root, args.generation, args.browser)
        else:
            with locked(args.root, exclusive=args.operation in {'lockcheck', 'selection-snapshot', 'selection-restore'}):
                if args.operation == 'selection-snapshot':
                    print(json.dumps(selection_snapshot(args.root), sort_keys=True))
                    return 0
                if args.operation == 'selection-restore':
                    raw = sys.stdin.buffer.read(8193)
                    require(len(raw) <= 8192, 'oversized selection snapshot')
                    restore_selection(args.root, json.loads(raw))
                    return 0
                selected = selection(args.root)
                if args.operation == 'lockcheck':
                    return 0
                if args.operation == 'status':
                    if selected['current']:
                        _, delivery = verify_generation(args.root, selected['current'])
                        selected['source'] = delivery['sources']
                    print(json.dumps(selected, sort_keys=True))
                    return 0
                directory, _ = verify_generation(args.root, selected['current'])
            if args.operation == 'verify':
                print(selected['current'])
            elif args.operation == 'run':
                host = Path(__file__).resolve().parents[2] / 'kilix'
                require(host.is_file(), 'installed host launcher unavailable')
                os.execv(str(host), [str(host), 'run', '--refit-windows', sys.executable,
                    '-I', '-B', str(directory / 'launch.py'), 'launch', '--root', str(args.root),
                    '--generation', selected['current'], '--browser', str(args.browser)])
        return 0
    except (Refused, OSError, ValueError, tarfile.TarError) as error:
        print('kilix voicebox: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
