#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""WSL session recovery using the same PTY broker as Linux Kilix.

The broker owns the PTY and processes. This module only manages its CLI clients
and the native control context that changes when a client reconnects.
"""
import argparse
from contextlib import contextmanager
import errno
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile


CONTROL_KEYS = ('KITTY_LISTEN_ON', 'KITTY_RC_PASSWORD', 'KITTY_PUBLIC_KEY',
                'KITTY_WINDOW_ID', 'KITTY_PID', 'WSL_INTEROP')
SESSION_KEY = 'KILIX_WSL_SESSION'
TOKEN_KEY = 'KILIX_WSL_SESSION_TOKEN'
RUNTIME_KEY = 'KILIX_WSL_SESSION_RUNTIME'
JOURNAL_LIMIT = 64 * 1024 * 1024


def session_name(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', value):
        raise ValueError('Session names need 1-32 letters, digits, underscores or hyphens; start with a letter or digit.')
    return value


def private_directory(path):
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f'Session directory must be a real, private directory owned by this user: {path}')
    return path


def runtime_directory():
    path = Path(os.environ.get(RUNTIME_KEY, f'/tmp/kilix-wsl-{os.getuid()}'))
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(f'{RUNTIME_KEY} must be an absolute path without ..')
    # sockaddr_un.sun_path includes the terminating NUL. Keep every allowed name usable.
    if len(os.fsencode(str(path / 'sessions' / ('x' * 32) / 'control.sock'))) >= 108:
        raise ValueError(f'{RUNTIME_KEY} is too long for the broker socket; use a short Linux path.')
    return private_directory(path)


def process_start(pid):
    """A PID plus this Linux start tick identifies one live same-user process."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        path = Path(f'/proc/{pid}')
        if path.stat().st_uid != os.getuid():
            return None
        fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] not in ('Z', 'X') else None
    except (OSError, IndexError):
        return None


def attachment_path(runtime, name):
    return private_directory(runtime / 'attachments') / (session_name(name) + '.json')


def read_private_json(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return {}
            payload = stream.read(16385)
            if len(payload) > 16384:
                return {}
            data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_attachment(runtime, name):
    data = read_private_json(attachment_path(runtime, name))
    if (data.get('name') != name or not isinstance(data.get('start'), str)
            or process_start(data.get('pid')) != data['start']):
        return {}
    return data


def refresh_environment():
    """Refresh only a managed shell's native context, never an unrelated shell.

    Live shells cannot have their inherited environment replaced. Every companion
    invocation therefore reads the current attachment, or clears stale native
    credentials when detached. WSL_INTEROP must also point at the new wsl.exe's
    interop server. Nothing sensitive is included in session listings.
    """
    name = os.environ.get(SESSION_KEY)
    if name is None:
        return
    context = {}
    try:
        record = read_attachment(runtime_directory(), session_name(name))
        saved = record.get('env', {})
        token = os.environ.get(TOKEN_KEY, '')
        if (re.fullmatch('[0-9a-f]{32}', token) and record.get('token') == token
                and isinstance(saved, dict)
                and all(isinstance(k, str) and isinstance(v, str) for k, v in saved.items())):
            context = saved
    except (OSError, ValueError):
        pass
    for key in CONTROL_KEYS:
        os.environ.pop(key, None)
        if key in context:
            os.environ[key] = context[key]


def write_private_json(destination, data):
    fd, temporary = tempfile.mkstemp(prefix='.' + destination.name + '-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(data, output)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_attachment(runtime, name, env):
    write_private_json(attachment_path(runtime, name), {
        'name': name, 'pid': os.getpid(), 'start': process_start(os.getpid()),
        'token': env.get(TOKEN_KEY), 'env': {key: env[key] for key in CONTROL_KEYS if key in env}})


@contextmanager
def attachment_lock(runtime, name):
    directory = private_directory(runtime / 'locks')
    fd = os.open(directory / (name + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe session lock file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f'Session {name} already has an attachment; detach it first.') from None
        yield
    finally:
        os.close(fd)


def build_backend(generation):
    """Build the host-pinned broker sources exported into a companion generation."""
    generation = Path(generation).resolve()
    source = generation / 'kitty-pty-broker'
    if not (source / 'Makefile').is_file():
        raise ValueError('Companion bundle lacks kitty-pty-broker; use a current source bundle.')
    subprocess.run(['make', '-C', str(source), '-j2', 'all'], check=True)
    binary = source / 'build/kitty-pty-broker'
    subprocess.run([str(binary), 'version'], check=True, stdout=subprocess.DEVNULL, timeout=10)
    (generation / 'bin').mkdir(exist_ok=True)
    # Keep the shared library beside the real executable for its $ORIGIN rpath.
    (generation / 'bin/kitty-pty-broker').symlink_to(binary)


class Sessions:
    def __init__(self, install, env):
        self.runtime = runtime_directory()
        self.env = dict(env)
        self.binary = Path(os.environ.get('KILIX_WSL_BROKER', str(install / 'bin/kitty-pty-broker')))
        if not self.binary.is_absolute() or not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise ValueError('WSL session broker is not installed; run companion setup with a current source bundle.')
        self.prefix = [str(self.binary), '--runtime-dir', str(self.runtime)]

    def query(self, *args):
        result = subprocess.run([*self.prefix, *args], env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=5)
        if result.returncode:
            raise ValueError(result.stderr.strip() or 'Session broker request failed')
        return json.loads(result.stdout)

    def listing(self):
        data = self.query('list', '--json')
        if not isinstance(data, list):
            raise ValueError('Invalid session broker listing')
        # Only our conservative session names can be addressed by this interface.
        return sorted((item for item in data if isinstance(item, dict)
                       and isinstance(item.get('id'), str)
                       and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', item.get('id', ''))),
                      key=lambda item: item['id'])

    def connect(self, name, command=None):
        session_name(name)
        if self.env.get(SESSION_KEY):
            raise ValueError('Use a fresh WSL pane to create or attach a session; nested recovery is not supported.')
        if not os.isatty(0) or not os.isatty(1):
            raise ValueError('Creating or attaching a session needs an interactive terminal.')
        with attachment_lock(self.runtime, name):
            existing = next((item for item in self.listing() if item['id'] == name), None)
            if command is not None and existing:
                raise ValueError(f'Session {name} already exists; use session attach {name}.')
            if command is None:
                if not existing:
                    raise ValueError(f'No live session named {name}; attach never reruns an exited command.')
                if existing['attached']:
                    raise ValueError(f'Session {name} is attached; detach it first.')
                if not existing.get('replay_complete', True):
                    print('kilix: replay history was truncated; the live process is unchanged.', file=sys.stderr)
            identity_path = private_directory(self.runtime / 'identities') / (name + '.json')
            if command is not None:
                token = secrets.token_hex(16)
                write_private_json(identity_path, {'token': token})
            else:
                token = read_private_json(identity_path).get('token', '')
                if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token):
                    raise ValueError('Missing recovery identity; use the original broker client for this session.')
            # A shell from an ended session must not gain the context of a later
            # session that happens to reuse its human-readable name.
            child_env = dict(self.env, **{SESSION_KEY: name, TOKEN_KEY: token, RUNTIME_KEY: str(self.runtime)})
            argv = ([*self.prefix, 'run', '--id', name, '--journal-limit', str(JOURNAL_LIMIT), '--', *command]
                    if command is not None else [*self.prefix, 'attach', name])
            child = None
            stopped = False

            def detach(signum, frame):
                nonlocal stopped
                stopped = True
                if child is not None and child.poll() is None:
                    child.send_signal(signal.SIGTERM)

            previous = {sig: signal.signal(sig, detach) for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
            try:
                # Publish before spawning: even a shell's first command sees its context.
                write_attachment(self.runtime, name, child_env)
                # A replay starts at the original PTY's origin, not at the outer
                # shell's cursor or in a previous program's terminal modes.
                os.write(1, b'\x1bc')
                child = subprocess.Popen(argv, env=child_env)
                if stopped:
                    child.terminate()
                return child.wait()
            finally:
                # Detaching stops only the CLI client. The daemon and PTY remain alive.
                if child is not None and child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()  # Only the attachment client, never its broker.
                        child.wait()
                try:
                    attachment_path(self.runtime, name).unlink(missing_ok=True)
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)
                # Return to the caller's main screen and reset input modes while
                # retaining text/scrollback. The PTY may already have disappeared.
                try:
                    os.write(1, b'\x1b[?1049l\x1b[!p')
                except OSError:
                    pass

    def detach(self, name):
        record = read_attachment(self.runtime, name)
        if not record:
            state = next((item for item in self.listing() if item['id'] == name), None)
            if state and not state['attached']:
                return 0
            raise ValueError(f'No managed attachment for {name}; close its attached pane to disconnect.')
        # Pin the process before checking its birth time again. A reused PID must
        # never make `detach` signal an unrelated process.
        if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
            raise ValueError('Detach requires Linux pidfd support; close the attached pane instead.')
        try:
            fd = os.pidfd_open(record['pid'])
        except ProcessLookupError:
            return 0
        except OSError as error:
            if error.errno == errno.ENOSYS:
                raise ValueError('Detach requires Linux pidfd support; close the attached pane instead.') from None
            raise
        try:
            if process_start(record['pid']) != record['start']:
                raise ValueError('The attachment has ended; refresh the session list.')
            try:
                signal.pidfd_send_signal(fd, signal.SIGTERM)
            except ProcessLookupError:
                pass
        finally:
            os.close(fd)
        return 0

    def kill(self, name):
        return subprocess.call([*self.prefix, 'kill', name], env=self.env)


def parser():
    result = argparse.ArgumentParser(prog='kilix session', description=(
        'Keep opt-in WSL processes alive across Kilix window closure. Use kilix native session '
        'for native Windows sessions. Reboot, WSL shutdown or broker termination ends live work.'))
    commands = result.add_subparsers(dest='action')
    new = commands.add_parser('new', help='Create and attach a named live WSL session')
    new.add_argument('name', type=session_name)
    new.add_argument('command', nargs=argparse.REMAINDER, help='[-- PROGRAM ARGS]; default: login shell')
    show = commands.add_parser('list', help='List live sessions for this distribution and Linux user')
    show.add_argument('--json', action='store_true')
    for action in ('attach', 'kill'):
        target = commands.add_parser(action, help=('Reconnect a live session' if action == 'attach'
                                                 else 'Terminate the session and its processes'))
        target.add_argument('name', type=session_name)
    detach = commands.add_parser('detach', help='Disconnect the current session or a named attachment')
    detach.add_argument('name', nargs='?', type=session_name)
    commands.add_parser('restore', help='Reconnect detached live sessions in new native tabs')
    return result


def main(args, *, install, env, remote=None):
    options = parser().parse_args(['--help'] if args == ['help'] else args or ['list'])
    sessions = Sessions(install, env)
    if options.action == 'new':
        command = options.command
        if command[:1] == ['--']:
            command = command[1:]
        return sessions.connect(options.name, command or [pwd.getpwuid(os.getuid()).pw_shell or '/bin/sh', '-l'])
    if options.action == 'attach':
        return sessions.connect(options.name)
    if options.action == 'detach':
        name = options.name or os.environ.get(SESSION_KEY, '')
        if not name:
            raise ValueError('Use session detach NAME outside a managed session.')
        return sessions.detach(session_name(name))
    if options.action == 'kill':
        return sessions.kill(options.name)
    records = sessions.listing()
    if options.action == 'list':
        if options.json:
            print(json.dumps(records))
        else:
            for item in records:
                print(f"{item['id']}\t{'attached' if item['attached'] else 'detached'}\tpid={item['child_pid']}")
            if not records:
                print('No live WSL sessions. Start one with kilix session new NAME.')
        return 0
    if options.action == 'restore':
        detached = [item for item in records if not item['attached']]
        if detached and remote is None:
            raise ValueError('Restore needs native Kilix control; use session attach NAME in this terminal.')
        count = 0
        for item in detached:
            status = remote(['launch', '--type=tab', '--tab-title=' + item['id'], '--',
                             '/usr/bin/env', RUNTIME_KEY + '=' + str(sessions.runtime),
                             'python3', str(Path(__file__).with_name('companion.py')),
                             'session', 'attach', item['id']])
            if status:
                return status
            count += 1
        print(f'Requested attachment of {count} live WSL session(s).')
        return 0
    return 2


if __name__ == '__main__':
    try:
        state = Path.home() / '.local/share/kilix-wsl'
        refresh_environment()
        raise SystemExit(main(sys.argv[1:], install=(state / 'current').resolve(), env=os.environ))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'kilix session: {error}', file=sys.stderr)
        raise SystemExit(2)
