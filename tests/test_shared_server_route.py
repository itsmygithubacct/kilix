"""A shared server's valid old pane must never become its caller's identity."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from unittest import mock
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import agent_control as control
import agent_context
from kilix_sdk import caller_context, frontend_context, geometry, panes
from test_agent_control_refresh import Fixture


class SharedServerRouteTests(Fixture):
    def setUp(self):
        super().setUp()
        # The tool runs under a server started in SID's old pane. An unrelated
        # current frontend could be anywhere; no frontend is a tool ancestor.
        self.process(100, self.kitty, ['tool'], {}, parent=200)
        self.process(200, self.kitty, ['codex', 'app-server', '--listen', 'unix://'],
                     {**self.stale, **self.broker_identity}, parent=900)
        self.process(900, self.broker, [str(self.broker), '--runtime-dir',
            str(self.runtime), 'run', '--id', self.broker_identity['KITTY_PTY_BROKER_SESSION'],
            '--', 'codex'], {}, parent=1)
        (self.proc / '900/comm').write_text('kitty-pty-broke\n')
        (self.proc / '100/comm').write_text('python3\n')
        (self.proc / '200/comm').write_text('codex\n')
        for patcher in (mock.patch.object(caller_context, '_PROC', self.proc),
                        mock.patch.object(caller_context.os, 'getpid', return_value=100)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.environment = {**self.stale, **self.broker_identity,
                            'KILIX_KITTEN': str(self.new_kitten)}

    def set_command(self, *args):
        (self.proc / '200/cmdline').write_bytes(b'\0'.join(a.encode() for a in args) + b'\0')

    def test_stale_server_cannot_rebind_to_its_original_panes_attach_helper(self):
        original = dict(self.environment)
        with self.assertRaisesRegex(caller_context.CallerContextError, 'shared Codex'):
            frontend_context.refresh(self.environment)
        self.assertEqual(self.environment, original)

    def test_live_engine_fast_path_cannot_make_server_environment_trustworthy(self):
        self.process(10, self.kitty, ['kitty'], {}, parent=1)
        with mock.patch.object(frontend_context, '_kitty_alive') as alive, \
                self.assertRaises(caller_context.CallerContextError):
            frontend_context.refresh(self.environment)
        alive.assert_not_called()

    def test_stripped_tool_env_does_not_recover_the_servers_credentials(self):
        with self.assertRaisesRegex(control.ControlError, 'shared Codex'):
            self.values({}, ppid=200)

    def test_explicit_caller_cannot_override_server_identity(self):
        with self.assertRaisesRegex(control.ControlError, 'shared Codex'):
            self.client(self.environment, caller_pane=3)

    def test_sdk_refuses_socket_and_tty_without_starting_a_client(self):
        with mock.patch.dict(os.environ, self.environment, clear=True), \
                mock.patch.object(panes.subprocess, 'run') as run:
            for tty in (False, True):
                with self.subTest(tty=tty), self.assertRaises(panes.PaneError):
                    panes._run(['ls'], via_tty=tty)
            with self.assertRaises(geometry.GeometryUnavailable):
                geometry.current()
        run.assert_not_called()

    def test_shared_context_is_partial_and_has_no_borrowed_identity(self):
        # Discovery is independent of route selection; avoid installation I/O.
        component = {'status': 'missing'}
        facts = {'kilix': {'invoked_source': component, 'path_selected': component,
                           'same_root': True}, 'needle': component,
                 'action_module_override': component, 'needle_kilix_selection': {}}
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = agent_context.collect(discover=lambda **kw: facts)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['caller'], {'status': 'unavailable'})
        self.assertEqual(result['connection_status'], 'unavailable')
        encoded = json.dumps(result)
        self.assertNotIn(str(self.old_password), encoded)
        self.assertNotIn(self.stale['KITTY_PUBLIC_KEY'], encoded)

    def test_no_daemon_tool_still_recovers_its_exact_broker_after_restart(self):
        self.set_command('codex', '--no-daemon', '--model', 'fixture-model')
        values = self.values(self.environment)
        self.assertEqual(self.route(values), self.fresh_route())

    def test_no_daemon_does_not_select_another_brokers_helper(self):
        self.set_command('codex', '--no-daemon')
        self.environment['KITTY_PTY_BROKER_SESSION'] = 'f' * 16
        with self.assertRaisesRegex(control.ControlError, 'conflicts with the caller broker'):
            self.values(self.environment)

    def test_missing_replacement_password_never_retains_old_password_with_new_key(self):
        self.set_command('codex', '--no-daemon')
        self.fresh.pop('KILIX_RC_PASSWORD_FILE')
        (self.helper / 'environ').write_bytes(
            b''.join(f'{key}={value}\0'.encode() for key, value in self.fresh.items()))
        values = self.values(self.environment)
        self.assertEqual(self.route(values), self.route(self.stale))

    def test_global_options_before_app_server_cannot_hide_the_boundary(self):
        self.set_command('codex', '-c', 'setting=true', 'app-server', '--listen=unix://')
        with self.assertRaises(caller_context.CallerContextError):
            caller_context.require_pane_local()

    def test_prompt_mention_is_not_a_server_command(self):
        self.set_command('codex', '--no-daemon', 'Please inspect app-server routing')
        caller_context.require_pane_local()

    def test_headless_exec_does_not_cross_a_shared_server(self):
        self.set_command('codex', 'exec', '--json', '-')
        caller_context.require_pane_local()

    def test_no_daemon_below_a_shared_server_does_not_restore_pane_identity(self):
        self.set_command('codex', '--no-daemon')
        (self.proc / '200/stat').write_text(
            '200 (codex) ' + ' '.join(['S', '300'] + ['0'] * 17 + ['777']))
        self.process(300, self.kitty, ['codex', 'app-server'], {}, parent=1)
        (self.proc / '300/comm').write_text('codex\n')
        with self.assertRaisesRegex(caller_context.CallerContextError, 'shared Codex'):
            caller_context.require_pane_local()

    def test_ancestry_limit_is_a_refusal(self):
        with mock.patch.object(caller_context, '_MAX_ANCESTORS', 1), \
                self.assertRaisesRegex(caller_context.CallerContextError, 'cannot verify'):
            caller_context.require_pane_local()

    def test_a_shell_argument_does_not_impersonate_codex(self):
        (self.proc / '200/comm').write_text('bash\n')
        self.set_command('bash', '-c', 'app-server')
        caller_context.require_pane_local()

    def test_ancestor_that_disappears_is_refused(self):
        (self.proc / '200/stat').unlink()
        with self.assertRaisesRegex(caller_context.CallerContextError, 'cannot verify'):
            caller_context.require_pane_local()

    def test_unreadable_and_oversized_ancestor_are_refused(self):
        (self.proc / '200/cmdline').write_bytes(b'x' * 65537)
        with self.assertRaisesRegex(caller_context.CallerContextError, 'cannot verify'):
            caller_context.require_pane_local()

    def test_pid_reuse_during_ancestry_read_is_refused(self):
        self.set_command('codex', '--no-daemon')
        identity = caller_context._identity
        calls = 0
        def changing(path):
            nonlocal calls
            value = identity(path)
            if path.name == '200':
                calls += 1
                if calls > 1:
                    return value[0], value[1], value[2] + 1
            return value
        with mock.patch.object(caller_context, '_identity', side_effect=changing), \
                self.assertRaises(caller_context.CallerContextError):
            caller_context.require_pane_local()


@unittest.skipUnless(sys.platform.startswith('linux'), 'requires Linux process metadata')
class ProcessBoundaryTests(unittest.TestCase):
    def test_real_process_ancestry_blocks_server_before_any_terminal_client_runs(self):
        # A synthetic server, no Codex/model process and no live terminal. Set
        # the Linux process name and argv exactly as the guard observes them.
        server = '''import ctypes, subprocess, sys
assert ctypes.CDLL(None).prctl(15, b"codex", 0, 0, 0) == 0
raise SystemExit(subprocess.call([sys.executable, "-c", sys.argv[2]]))
'''
        check = '''import json
import agent_control
from kilix_sdk import panes
result = []
for operation in (lambda: agent_control.Client().run(["ls"]), panes.load_state):
    try:
        operation()
        result.append("connected")
    except (agent_control.ControlError, panes.PaneError) as error:
        result.append(str(error))
print(json.dumps(result))
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            password = root / 'credential'
            password.write_text('fixture-only')
            password.chmod(0o600)
            marker = root / 'client-ran'
            kitten = root / 'kitten'
            kitten.write_text('#!' + sys.executable + '\nfrom pathlib import Path\n'
                              + 'Path(' + repr(str(marker)) + ').touch()\nprint("[]")\n')
            kitten.chmod(0o700)
            environment = {'PATH': os.defpath,
                           'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'config'),
                           'PYTHONDONTWRITEBYTECODE': '1', 'HOME': str(root),
                           'KILIX_KITTEN': str(kitten), 'KITTY_WINDOW_ID': '7',
                           'KITTY_LISTEN_ON': 'unix:@fixture-unused',
                           'KILIX_RC_PASSWORD_FILE': str(password)}
            for mode in ('app-server', '--no-daemon'):
                with self.subTest(mode=mode):
                    result = subprocess.run([sys.executable, '-c', server, mode, check],
                                            env=environment, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    answers = json.loads(result.stdout)
                    if mode == 'app-server':
                        self.assertTrue(all('shared Codex' in answer for answer in answers))
                        self.assertFalse(marker.exists())
                    else:
                        self.assertEqual(answers, ['connected', 'connected'])
                        self.assertTrue(marker.exists())


if __name__ == '__main__':
    unittest.main()
