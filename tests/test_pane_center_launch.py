"""The enriched log snapshot must use the same caller guard as pane actions."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_agent_control_refresh import Fixture, SID
import agent_control as control
import pane_center_launch as launch
from kilix_sdk import caller_context

ROOT = Path(__file__).resolve().parents[1]


class RouteTests(Fixture):
    def dispatch(self, env, parent=100, snapshot=None):
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(control.os, 'getppid', return_value=parent), \
                mock.patch.object(launch, 'command', return_value=['/fixture/panes']), \
                mock.patch.object(control.Client, 'snapshot', return_value=(snapshot if snapshot is not None else [
                    {'id': 3, 'env': {'KITTY_PTY_BROKER_SESSION': SID}}])), \
                mock.patch.object(launch.os, 'execvpe') as execute, \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()):
            result = launch.main(['list', '--json'])
        return result, execute, output.getvalue()

    def test_stripped_tool_metadata_recovers_and_refreshes_exact_broker(self):
        self.process(100, self.kitty, ['sh'], {**self.stale, **self.broker_identity}, parent=1)
        result, execute, output = self.dispatch({**self.stale,
            'KILIX_BUILD_DIRECTORY': str(self.root / 'engine'),
            'KILIX_KITTEN': str(self.new_kitten), 'KILIX_RC_PASSWORD': 'stale-secret'})
        self.assertEqual(result, 0, output)
        executable, argv, environment = execute.call_args.args
        self.assertEqual((executable, argv), ('/fixture/panes', ['/fixture/panes', 'list', '--json']))
        self.assertEqual(self.route(environment), self.fresh_route())
        self.assertEqual(environment['KITTY_PID'], '20')
        self.assertEqual(environment['KILIX_KITTEN'], str(self.new_kitten))
        self.assertNotIn('KILIX_RC_PASSWORD', environment)

    def test_foreign_and_ambiguous_helpers_never_replace_route(self):
        for mode in ('foreign', 'ambiguous'):
            with self.subTest(mode=mode):
                env = {**self.stale, **self.broker_identity,
                       'KILIX_KITTEN': str(self.new_kitten)}
                if mode == 'foreign':
                    env['KITTY_PTY_BROKER_SESSION'] = 'f' * 16
                else:
                    self.process(41, self.broker, [str(self.broker), '--runtime-dir',
                                 str(self.runtime), 'attach', SID], self.fresh, parent=20)
                result, execute, _ = self.dispatch(env)
                self.assertEqual(result, 1)
                execute.assert_not_called()

    def test_reachable_frontend_with_wrong_or_duplicate_broker_is_refused(self):
        for snapshot in ([{'id': 3, 'env': {'KITTY_PTY_BROKER_SESSION': 'f' * 16}}],
                         [{'id': 3, 'env': {'KITTY_PTY_BROKER_SESSION': SID}},
                          {'id': 4, 'env': {'KITTY_PTY_BROKER_SESSION': SID}}]):
            with self.subTest(snapshot=snapshot):
                result, execute, output = self.dispatch(
                    {**self.stale, **self.broker_identity,
                     'KILIX_KITTEN': str(self.new_kitten)}, snapshot=snapshot)
                self.assertEqual(result, 1)
                execute.assert_not_called()
                self.assertEqual(json.loads(output)['code'], 'pane_context_unavailable')

    def test_private_password_is_required_before_toolkit_launch(self):
        self.new_password.chmod(0o644)
        result, execute, output = self.dispatch({**self.stale, **self.broker_identity})
        self.assertEqual(result, 1)
        execute.assert_not_called()
        self.assertEqual(json.loads(output)['code'], 'pane_context_unavailable')
        self.assertNotIn(str(self.new_password), output)

    def test_shared_server_cannot_use_even_a_valid_route(self):
        self.process(100, self.kitty, ['tool'], {}, parent=200)
        self.process(200, self.kitty, ['codex', 'app-server'], {}, parent=1)
        (self.proc / '100/comm').write_text('python3\n')
        (self.proc / '200/comm').write_text('codex\n')
        with mock.patch.object(caller_context, '_PROC', self.proc), \
                mock.patch.object(caller_context.os, 'getpid', return_value=100):
            result, execute, output = self.dispatch({**self.fresh,
                'KILIX_KITTEN': str(self.new_kitten)}, parent=200)
        self.assertEqual(result, 1)
        execute.assert_not_called()
        self.assertEqual(json.loads(output), {'schema': 'kilix.panes.error/v1',
                                              'code': 'caller_context_unverified'})


class HostDispatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.prefix = self.root / 'prefix'
        (self.prefix / 'bin').mkdir(parents=True)
        self.script = self.prefix / 'bin/kilix-panes'
        self.script.write_text('#!' + sys.executable + '\nimport json, os, sys\n'
            'print(json.dumps({"argv": sys.argv[1:], "credential": '
            'os.environ.get("KILIX_RC_PASSWORD_FILE"), "kitten": '
            'os.environ.get("KILIX_KITTEN")}))\n')
        self.script.chmod(0o700)
        self.password = self.root / 'inherited-password'
        self.password.write_text('fixture-secret')
        self.password.chmod(0o600)
        self.kitten = self.root / 'kitten'
        self.kitten.write_text('#!' + sys.executable + '\nimport json\n'
            'print(json.dumps([{"id":1,"tabs":[{"id":1,"windows":[{"id":3}]}]}]))\n')
        self.kitten.chmod(0o700)
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.home),
            'KILIX_TUI_UTILS_PREFIX': str(self.prefix), 'KILIX_KITTEN': str(self.kitten),
            'KILIX_RC_PASSWORD_FILE': str(self.password),
            'KITTY_LISTEN_ON': 'unix:/fixture-terminal', 'KITTY_WINDOW_ID': '3'}

    def test_real_host_preserves_credential_and_never_prepares_storage_or_engine(self):
        for alias in ('panes', 'pane-center'):
            result = subprocess.run([str(ROOT / 'kilix'), alias, 'list', '--json'],
                env=self.env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'argv': ['list', '--json'],
                'credential': str(self.password), 'kitten': str(self.kitten)})
            self.assertEqual(list(self.home.iterdir()), [])

    def test_real_shared_server_refuses_before_toolkit(self):
        server = ('import ctypes, subprocess, sys\n'
            'assert ctypes.CDLL(None).prctl(15, b"codex", 0, 0, 0) == 0\n'
            'raise SystemExit(subprocess.call(sys.argv[2:]))\n')
        result = subprocess.run([sys.executable, '-c', server, 'app-server',
            str(ROOT / 'kilix'), 'panes', 'list', '--json'], env=self.env,
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['code'], 'caller_context_unverified')
        self.assertIn('--no-daemon', result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_help_needs_no_connection(self):
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.home),
               'KILIX_TUI_UTILS_PREFIX': str(self.prefix)}
        result = subprocess.run([str(ROOT / 'kilix'), 'panes', '--help'], env=env,
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
