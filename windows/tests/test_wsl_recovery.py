"""Real PTY lifetime/reconnect checks, using the pinned Linux broker.

Initialize third_party/kitty-pty-broker, or set KILIX_TEST_BROKER_SOURCE to a
checkout of that pin. KILIX_REQUIRE_RECOVERY_TESTS=1 makes missing sources fatal.
All clients, processes, runtime directories and builds belong to these tests.
"""
import errno
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import subprocess
import struct
import sys
import tempfile
import termios
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'windows/wsl/recovery.py'
spec = importlib.util.spec_from_file_location('recovery', MODULE)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def eventually(function, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        answer = function()
        if answer:
            return answer
        time.sleep(.025)
    raise AssertionError('Timed out waiting for ' + repr(function))


class Frontend:
    def __init__(self, args, env):
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve(sys.executable, [sys.executable, '-B', str(MODULE), *args], env)
        self.output = bytearray()
        self.status = None

    def drain(self):
        while select.select([self.fd], [], [], .02)[0]:
            try:
                chunk = os.read(self.fd, 65536)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            self.output.extend(chunk)
        return self.output

    def until(self, marker):
        return eventually(lambda: marker in self.drain())

    def send(self, command):
        os.write(self.fd, command)

    def resize(self, rows, columns):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))

    def wait(self):
        def finished():
            self.drain()
            if self.status is None:
                pid, status = os.waitpid(self.pid, os.WNOHANG)
                if pid:
                    self.status = os.waitstatus_to_exitcode(status)
            return self.status is not None
        eventually(finished)
        return self.status

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.status is None:
            def finished():
                pid, status = os.waitpid(self.pid, os.WNOHANG)
                if pid:
                    self.status = os.waitstatus_to_exitcode(status)
                return self.status is not None
            try:
                eventually(finished, 3)
            except AssertionError:
                os.kill(self.pid, signal.SIGKILL)
                self.status = os.waitstatus_to_exitcode(os.waitpid(self.pid, 0)[1])


class RecoveryContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kwr-')
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name)
        self.env = {recovery.RUNTIME_KEY: self.tmp.name, recovery.SESSION_KEY: 'work',
                    recovery.TOKEN_KEY: '1' * 32,
                    'KITTY_LISTEN_ON': 'tcp:127.0.0.1:1234', 'KITTY_RC_PASSWORD': 'old',
                    'KITTY_WINDOW_ID': '1', 'KITTY_PUBLIC_KEY': 'old-key', 'WSL_INTEROP': '/run/WSL/111_interop'}

    def test_context_refresh_tracks_only_live_private_attachment(self):
        new = dict(self.env, KITTY_LISTEN_ON='tcp:127.0.0.1:5678', KITTY_WINDOW_ID='9',
                   KITTY_RC_PASSWORD='new', KITTY_PUBLIC_KEY='new-key', WSL_INTEROP='/run/WSL/222_interop')
        recovery.write_attachment(self.runtime, 'work', new)
        record_path = recovery.attachment_path(self.runtime, 'work')
        self.assertEqual(record_path.stat().st_mode & 0o777, 0o600)
        with patch.dict(os.environ, self.env, clear=True):
            recovery.refresh_environment()
            for key in recovery.CONTROL_KEYS:
                self.assertEqual(os.environ.get(key), new.get(key))
        # No session marker: an ordinary pane keeps its own native context.
        plain = {k: v for k, v in self.env.items() if k != recovery.SESSION_KEY}
        with patch.dict(os.environ, plain, clear=True):
            recovery.refresh_environment()
            self.assertEqual(dict(os.environ), plain)
        for bad in ('missing', 'permissions', 'symlink', 'reused-pid', 'reused-name'):
            recovery.write_attachment(self.runtime, 'work', new)
            if bad == 'missing':
                record_path.unlink()
            elif bad == 'permissions':
                record_path.chmod(0o644)
            elif bad == 'symlink':
                target = self.runtime / 'other.json'
                record_path.rename(target)
                record_path.symlink_to(target)
            elif bad == 'reused-pid':
                record = json.loads(record_path.read_text())
                record['start'] = '0'
                record_path.write_text(json.dumps(record))
            else:
                recovery.write_attachment(self.runtime, 'work', dict(new, **{recovery.TOKEN_KEY: '2' * 32}))
            with patch.dict(os.environ, self.env, clear=True):
                recovery.refresh_environment()
                self.assertFalse(set(recovery.CONTROL_KEYS) & os.environ.keys(), bad)

    def test_runtime_and_names_cannot_escape_or_use_public_directory(self):
        for name in ('../other', '.', '-flag', '', 'x' * 33, 'bad.name', 'bad\nname'):
            with self.assertRaises(ValueError):
                recovery.session_name(name)
        for path in ('relative', '/tmp/../other', '/tmp/' + 'x' * 80):
            with patch.dict(os.environ, {recovery.RUNTIME_KEY: path}):
                with self.assertRaises(ValueError):
                    recovery.runtime_directory()
        self.runtime.chmod(0o755)
        with patch.dict(os.environ, self.env):
            with self.assertRaises(ValueError):
                recovery.runtime_directory()

    def test_restore_only_requests_detached_sessions_and_propagates_failure(self):
        listing = [{'id': 'first', 'attached': False}, {'id': 'busy', 'attached': True},
                   {'id': 'last', 'attached': False}]
        with patch.object(recovery.Sessions, '__init__', return_value=None), \
                patch.object(recovery.Sessions, 'listing', return_value=listing), \
                patch.object(recovery.Sessions, 'runtime', self.runtime, create=True), \
                patch('sys.stdout', new_callable=io.StringIO), \
                patch('subprocess.call', side_effect=AssertionError('No new processes')):
            calls = []
            def remote(args):
                calls.append(args)
                return 0 if len(calls) == 1 else 7
            self.assertEqual(recovery.main(['restore'], install=self.runtime, env={}, remote=remote), 7)
            self.assertEqual([args[-1] for args in calls], ['first', 'last'])
            self.assertTrue(all(args[-3:-1] == ['session', 'attach'] for args in calls))


class RecoveryLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='kwr-build-')
        cls.addClassCleanup(cls.build.cleanup)
        source = Path(os.environ.get('KILIX_TEST_BROKER_SOURCE', str(ROOT / 'third_party/kitty-pty-broker')))
        if not (source / 'Makefile').is_file() or not shutil.which('make') or not shutil.which('cc'):
            message = 'Initialize third_party/kitty-pty-broker and install make/cc for live reconnect tests'
            if os.environ.get('KILIX_REQUIRE_RECOVERY_TESTS') == '1':
                raise AssertionError(message)
            raise unittest.SkipTest(message)
        result = subprocess.run(['make', '-C', str(source), 'BUILD_DIR=' + cls.build.name, '-j2', 'all'],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.binary = Path(cls.build.name) / 'kitty-pty-broker'

    def test_generation_build_helper_keeps_library_resolvable(self):
        source = Path(os.environ.get('KILIX_TEST_BROKER_SOURCE', str(ROOT / 'third_party/kitty-pty-broker')))
        with tempfile.TemporaryDirectory(prefix='kwr-generation-') as temporary:
            generation = Path(temporary)
            shutil.copytree(source, generation / 'kitty-pty-broker', ignore=shutil.ignore_patterns('.git', 'build'))
            recovery.build_backend(generation)
            binary = generation / 'bin/kitty-pty-broker'
            self.assertTrue(binary.is_symlink())
            result = subprocess.run([str(binary), 'version'], env={'PATH': os.defpath},
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('protocol=1', result.stdout)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kwr-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / 'run'
        self.env = dict(os.environ, KILIX_WSL_BROKER=str(self.binary),
                        KILIX_WSL_SESSION_RUNTIME=str(self.runtime), TERM='xterm-256color',
                        KITTY_LISTEN_ON='tcp:127.0.0.1:1234', KITTY_RC_PASSWORD='first',
                        KITTY_WINDOW_ID='1', KITTY_PUBLIC_KEY='first-key', WSL_INTEROP='/run/WSL/111_interop')
        self.env.pop(recovery.SESSION_KEY, None)
        self.frontends = []
        self.addCleanup(self.cleanup_sessions)

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(MODULE), *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def listing(self):
        result = self.cli('list', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def frontend(self, *args, env=None):
        client = Frontend(args, env or self.env)
        self.frontends.append(client)
        return client

    def cleanup_sessions(self):
        for frontend in self.frontends:
            frontend.close()
        for item in self.listing():
            self.cli('kill', item['id'])
        eventually(lambda: not self.listing())

    def detached(self, name='work'):
        return eventually(lambda: next((item for item in self.listing()
                                        if item['id'] == name and not item['attached']), None))

    def program(self):
        script = self.root / 'child.py'
        script.write_text('''import json, os, pathlib, select, subprocess, sys, time, tty, uuid
tty.setraw(0)
root = pathlib.Path(sys.argv[1])
token = uuid.uuid4().hex
value = 'initial'
tick = 0
pending = b''
descendant = subprocess.Popen(['sleep', '120'])
(root / 'identity.json').write_text(json.dumps([os.getpid(), token, descendant.pid]))
print('READY:' + token, flush=True)
while True:
    tick += 1
    (root / 'heartbeat').write_text(str(tick))
    if tick % 5 == 0:
        print('TICK:' + str(tick), flush=True)
    if not select.select([0], [], [], .03)[0]:
        continue
    pending += os.read(0, 65536)
    while b'\\n' in pending:
        line, _, pending = pending.partition(b'\\n')
        if line.startswith(b'set '):
            value = line[4:].decode()
            print('SET:' + value, flush=True)
        elif line == b'state':
            print('STATE:' + json.dumps([os.getpid(), token, value, tick]), flush=True)
        elif line == b'graphics':
            os.write(1, b'\\x1b_Ga=T,f=24,s=1,v=1;////\\x1b\\\\GRAPHICS_END')
        elif line == b'exit':
            descendant.terminate()
            descendant.wait()
            sys.exit(17)
        elif line == b'context':
            code = 'import sys, json, os; sys.path.insert(0, sys.argv[1]); from companion import environment; e = environment(); print("CONTEXT:" + json.dumps([e.get(k) for k in ("KITTY_WINDOW_ID", "KITTY_LISTEN_ON", "KITTY_RC_PASSWORD", "WSL_INTEROP")]))'
            subprocess.run([sys.executable, '-c', code, sys.argv[2]], check=True)
''')
        return [sys.executable, str(script), str(self.root), str(MODULE.parent)]

    def test_frontend_loss_preserves_pid_state_output_graphics_and_new_control_context(self):
        first = self.frontend('new', 'work', '--', *self.program())
        first.until(b'READY:')
        identity = json.loads((self.root / 'identity.json').read_text())
        first.send(b'set changed\n')
        first.until(b'SET:changed')
        before = int((self.root / 'heartbeat').read_text())
        first.close()  # Lose the actual controlling terminal, as on window closure.
        state = self.detached()
        self.assertEqual(state['child_pid'], identity[0])
        eventually(lambda: int((self.root / 'heartbeat').read_text() or '0') > before + 10)
        new_env = dict(self.env, KITTY_WINDOW_ID='27', KITTY_LISTEN_ON='tcp:127.0.0.1:4321',
                       KITTY_RC_PASSWORD='second', KITTY_PUBLIC_KEY='second-key', WSL_INTEROP='/run/WSL/222_interop')
        second = self.frontend('attach', 'work', env=new_env)
        second.until(b'SET:changed')  # Replay from the same live PTY.
        self.assertTrue(any(int(tick) > before for tick in re.findall(rb'TICK:(\d+)', second.output)))
        second.resize(37, 113)
        eventually(lambda: any(item['rows'] == 37 and item['columns'] == 113 for item in self.listing()))
        second.send(b'state\ncontext\ngraphics\n')
        second.until(b'GRAPHICS_END')
        self.assertIn(('STATE:' + json.dumps(identity[:2] + ['changed']))[:-1].encode(), second.output)
        context = b'CONTEXT:["27", "tcp:127.0.0.1:4321", "second", "/run/WSL/222_interop"]'
        self.assertIn(context, second.output)
        self.assertIn(b'\x1b_Ga=T,f=24,s=1,v=1;////\x1b\\GRAPHICS_END', second.output)
        self.assertEqual(self.listing()[0]['child_pid'], identity[0])
        self.assertNotIn('second', self.cli('list', '--json').stdout)
        # A duplicate attach must neither steal the connection nor its context.
        duplicate = self.frontend('attach', 'work')
        self.assertEqual(duplicate.wait(), 2)
        second.output.clear()
        second.send(b'context\n')
        second.until(context)
        second.send(b'exit\n')
        self.assertEqual(second.wait(), 17)
        eventually(lambda: not self.listing())
        missing = self.frontend('attach', 'work')
        self.assertEqual(missing.wait(), 2)
        self.assertIn(b'attach never reruns', missing.output)

    def test_explicit_detach_and_kill_have_different_process_lifetimes(self):
        frontend = self.frontend('new', 'work', '--', *self.program())
        frontend.until(b'READY:')
        child, token, descendant = json.loads((self.root / 'identity.json').read_text())
        detached = self.cli('detach', 'work')
        self.assertEqual(detached.returncode, 0, detached.stderr)
        self.assertEqual(frontend.wait(), 0)
        self.assertEqual(self.detached()['child_pid'], child)
        self.assertIsNotNone(recovery.process_start(descendant))
        self.assertEqual(self.cli('detach', 'work').returncode, 0)
        self.assertEqual(self.cli('kill', 'work').returncode, 0)
        eventually(lambda: recovery.process_start(child) is None and recovery.process_start(descendant) is None)
        eventually(lambda: not self.listing())

    def test_noninteractive_create_is_refused_before_starting_processes(self):
        result = self.cli('new', 'work', '--', 'sleep', '120')
        self.assertEqual(result.returncode, 2)
        self.assertIn('interactive terminal', result.stderr)
        self.assertEqual(self.listing(), [])


if __name__ == '__main__':
    unittest.main()
