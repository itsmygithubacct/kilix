"""Exercise the staged command package against owned terminal/tmux fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

WINDOWS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('stage_commands', WINDOWS / 'stage-commands.py')
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)

# A separate process models authenticated remote transport. It owns only the
# state under this test's temporary directory and never contacts a real pane.
KITTEN = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
assert args.pop(0) == '@'
if args in (['--help'], ['-h']):
    print('launch --next-to --source-window --keep-focus --bias hsplit vsplit before after')
    raise SystemExit(0)
assert '--use-password' in args and args[args.index('--use-password') + 1] == 'always'
if os.getenv('KITTY_RC_PASSWORD') != 'fixture-password':
    print('authentication denied', file=sys.stderr)
    raise SystemExit(1)
while args and args[0].startswith('--'):
    del args[:2]
if args == ['launch', '--help']:
    print('launch --next-to --source-window --keep-focus --bias hsplit vsplit before after')
    raise SystemExit(0)
root = pathlib.Path(os.environ['KILIX_TEST_FIXTURE'])
path = root / 'state.json'
state = json.loads(path.read_text())
verb, rest = args[0], args[1:]
with (root / 'calls.jsonl').open('a') as output:
    output.write(json.dumps(args) + '\n')
def option(name, default=''):
    if name in rest:
        return rest[rest.index(name) + 1]
    return next((v.split('=', 1)[1] for v in rest if v.startswith(name + '=')), default)
if verb == 'ls':
    print(json.dumps(state))
elif verb == 'launch':
    pane = {'id': 3, 'pid': 103, 'title': option('--title', 'fixture-child'),
            'cwd': '/tmp', 'at_prompt': True, 'foreground_processes': [], 'env': {}}
    if option('--type') == 'tab':
        state[0]['tabs'].append({'id': 8, 'title': option('--tab-title'),
                                 'layout': 'splits', 'windows': [pane]})
    else:
        state[0]['tabs'][0]['windows'].append(pane)
    path.write_text(json.dumps(state))
    if os.environ.get('KILIX_TEST_FAIL_AFTER_LAUNCH'):
        raise SystemExit(1)
    print(3)
elif verb == 'send-text':
    (root / 'input.bin').write_bytes(sys.stdin.buffer.read())
elif verb == 'get-text':
    print('owned fixture output')
'''


class StagedCommandsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package_temp = tempfile.TemporaryDirectory(prefix='kilix commands café ')
        cls.root = Path(cls.package_temp.name)
        stage.stage(cls.root)
        (cls.root / 'kitty/launcher').mkdir(parents=True)
        kitten = cls.root / 'kitty/launcher/kitten.exe'
        kitten.write_text(KITTEN)
        kitten.chmod(0o755)
        for source, target in ((WINDOWS / 'kilix-wsl.sh', cls.root / 'wsl-bin/kilix'),
                               (WINDOWS / 'wsl/kitten.sh', cls.root / 'wsl-bin/kitten')):
            shutil.copy2(source, target)
            target.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.package_temp.cleanup()

    def setUp(self):
        self.home_temp = tempfile.TemporaryDirectory(prefix='kilix fixture ')
        self.addCleanup(self.home_temp.cleanup)
        self.home = Path(self.home_temp.name)
        self.env = {'PATH': os.defpath, 'HOME': str(self.home), 'LOCALAPPDATA': str(self.home),
                    'XDG_STATE_HOME': str(self.home / 'state'), 'WSL_DISTRO_NAME': 'Fixture',
                    'KITTY_LISTEN_ON': 'tcp:127.0.0.1:12345', 'KITTY_WINDOW_ID': '1',
                    'KITTY_RC_PASSWORD': 'fixture-password', 'KITTY_PUBLIC_KEY': 'fixture-key',
                    'KILIX_TEST_FIXTURE': str(self.home)}
        self.state = [{'id': 1, 'tabs': [{'id': 1, 'layout': 'splits', 'title': 'owned',
            'windows': [{'id': n, 'pid': 100+n, 'at_prompt': True, 'title': 'owned-' + str(n),
                         'env': {'KITTY_RC_PASSWORD': 'fixture-password'},
                         'foreground_processes': [{'pid': 100+n, 'cmdline': ['wsl.exe']}]}
                        for n in (1, 2)]}]}]
        self.write_state()

    def write_state(self):
        (self.home / 'state.json').write_text(json.dumps(self.state))

    def run_cli(self, *args, native=False, data=None, env=None):
        script = 'native-commands.py' if native else 'wsl/companion.py'
        return subprocess.run([sys.executable, str(self.root / script), *args],
                              input=data, text=True, capture_output=True, timeout=20,
                              env=self.env if env is None else env)

    def checked(self, *args, **kwargs):
        result = self.run_cli(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def test_full_help_and_subcommand_help_work_without_a_connection(self):
        env = {key: value for key, value in self.env.items() if not key.startswith('KITTY_')}
        for args in (('help',), ('help', 'settings'), ('models', 'install', '--help'),
                     ('agent-control', 'deliver', '--help'), ('help', 'action'),
                     ('tmux', 'type', '--help'), ('transcript', 'clean', '--help'),
                     ('help', 'transcript', 'capture'), ('--launch', '--help')):
            with self.subTest(args=args):
                self.assertTrue(self.checked(*args, env=env).stdout)
        self.assertFalse((self.home / 'calls.jsonl').exists())
        self.assertFalse((self.home / '.local').exists())
        capabilities = json.loads(self.checked('action', 'capabilities', env=env).stdout)
        self.assertEqual(capabilities['identity_kind'], 'windows-instance-pane')
        self.assertTrue(capabilities['input_requires_observed_readiness'])

    def test_settings_preserve_shared_validation_and_never_signal_windows_pid(self):
        # This owned sleeper would terminate on SIGUSR1 if the adapter used
        # the shared Linux notification against an inherited Windows PID.
        with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) as sleeper:
            try:
                env = dict(self.env, KITTY_PID=str(sleeper.pid))
                result = self.checked('settings', '--set', 'pane_memory=off',
                                      '--game', 'kilix-pong=off', '--print', env=env)
                self.assertIn('KILIX_CHROME_PANE_MEMORY_MODE=off', result.stdout)
                self.assertIsNone(sleeper.poll())
                bad = self.run_cli('settings', '--set', 'pane_memory=invalid', env=env)
                self.assertEqual(bad.returncode, 2)
            finally:
                sleeper.terminate()
                sleeper.wait(timeout=5)

    def test_models_use_companion_root_without_installation_or_receipts(self):
        data = json.loads(self.checked('models', 'list').stdout)
        self.assertEqual(data['root'], str(self.home / '.local/share/kilix-wsl/content'))
        self.assertTrue(any(item['id'] == 'needle2' for item in data['models']))
        self.checked('models', 'show', 'needle2')
        self.assertFalse((self.home / '.local').exists())

    def identities(self):
        result = self.checked('agent-control', 'list')
        self.assertNotIn('fixture-password', result.stdout)
        panes = json.loads(result.stdout)['panes']
        self.assertEqual(panes[0]['identity_kind'], 'windows-instance-pane')
        return [{key: pane[key] for key in ('pane_id', 'broker')} for pane in panes]

    def test_agent_input_identity_authentication_and_readiness(self):
        source, target = self.identities()
        self.state[0]['tabs'][0]['windows'][1]['at_prompt'] = False
        self.write_state()
        args = ('agent-control', 'send', '2', '--expect-broker', target['broker'], '--text', 'owned fixture')
        self.assertIn('readiness', self.run_cli(*args).stderr)
        self.assertFalse((self.home / 'input.bin').exists())
        self.state[0]['tabs'][0]['windows'][1]['at_prompt'] = True
        self.write_state()
        self.checked(*args)
        self.assertEqual((self.home / 'input.bin').read_bytes(), b'owned fixture')
        result = self.run_cli('agent-control', 'key', '1', '--expect-broker', source['broker'], 'enter')
        self.assertIn('own pane', result.stderr)
        self.assertNotEqual(self.run_cli(*args, env=dict(self.env, KITTY_PUBLIC_KEY='replacement')).returncode, 0)
        for password in ('', 'incorrect'):
            self.assertNotEqual(self.run_cli('agent-control', 'list',
                env=dict(self.env, KITTY_RC_PASSWORD=password)).returncode, 0)

    @unittest.skipUnless(hasattr(os, 'fork'), 'owned Linux controlling tty fixture')
    def test_linux_foreground_agent_is_scoped_and_ambiguous_ttys_fail_closed(self):
        import pty
        import signal

        def start_fixture():
            pid, master = pty.fork()
            if pid == 0:
                os.execve(sys.executable, ['codex', '-c', 'import time; time.sleep(30)'],
                          dict(self.env, KITTY_WINDOW_ID='2'))

            def close():
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)
                os.close(master)

            self.addCleanup(close)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if (Path('/proc') / str(pid) / 'cmdline').read_bytes().startswith(b'codex\0'):
                    return
                time.sleep(0.01)
            self.fail('owned fixture did not reach exec')

        self.state[0]['tabs'][0]['windows'][1]['at_prompt'] = False
        self.write_state()
        start_fixture()
        panes = json.loads(self.checked('agent-control', 'list').stdout)['panes']
        self.assertEqual(panes[1]['agent'], 'codex')
        self.checked('agent-control', 'send', '2', '--expect-broker', panes[1]['broker'],
                     '--text', 'owned process fixture')
        foreign = json.loads(self.checked('agent-control', 'list',
            env=dict(self.env, KITTY_PUBLIC_KEY='another-instance')).stdout)['panes']
        self.assertIsNone(foreign[1]['agent'])
        foreign = json.loads(self.checked('agent-control', 'list',
            env=dict(self.env, WSL_DISTRO_NAME='OtherFixture')).stdout)['panes']
        self.assertIsNone(foreign[1]['agent'])
        start_fixture()
        ambiguous = json.loads(self.checked('agent-control', 'list').stdout)['panes']
        self.assertIsNone(ambiguous[1]['agent'])
        refused = self.run_cli('agent-control', 'key', '2', '--expect-broker', ambiguous[1]['broker'], 'enter')
        self.assertIn('readiness', refused.stderr)

    def test_structured_launch_preserves_argv_and_durable_at_most_once_receipt(self):
        source, target = self.identities()
        literal = ['printf', 'a b', '', '$(never-execute)', "'quote'", 'env:KITTY_PTY_BROKER_SESSION=literal']
        request = {'schema': 'kilix.actions/v1', 'operation_id': 'fixture-operation',
                   'operation': 'pane.open', 'source': source, 'target': target,
                   'params': {'argv': literal, 'cwd': str(self.home), 'title': 'fixture',
                              'placement': 'new-tab'}}
        result = json.loads(self.checked('action', '--request-json', '-', data=json.dumps(request)).stdout)
        self.assertEqual(result['status'], 'created')
        self.assertTrue(result['pane_created_verified'])
        second = json.loads(self.checked('action', '--request-json', '-', data=json.dumps(request)).stdout)
        self.assertTrue(second['duplicate'])
        calls = [json.loads(line) for line in (self.home / 'calls.jsonl').read_text().splitlines()]
        launches = [call for call in calls if call[0] == 'launch']
        self.assertEqual(len(launches), 1)
        self.assertEqual(launches[0][-len(literal):], literal)
        self.assertIn('wsl.exe', launches[0])
        self.assertIn('--match=id:2', launches[0])
        request.update(operation='operation.status', params={})
        status = json.loads(self.checked('action', '--request-json', '-', data=json.dumps(request)).stdout)
        self.assertEqual(status['status'], 'created')

    def test_transcript_capture_clean_list_and_traversal_refusal(self):
        saved = json.loads(self.checked('transcript', 'capture', '--pane', '2').stdout)
        self.assertEqual(self.checked('transcript', 'show', saved['session']).stdout, 'owned fixture output\n')
        self.assertIn('owned fixture output', self.checked('transcript', 'clean', saved['session']).stdout)
        live = self.checked('transcript', 'clean', '--pane', '2', '--format', 'json')
        self.assertIn('owned fixture output', live.stdout)
        rows = json.loads(self.checked('transcript', 'list', '--json').stdout)
        self.assertFalse(rows['automatic_recording'])
        self.assertEqual(len(rows['transcripts']), 1)
        self.assertNotEqual(self.run_cli('transcript', 'path', '../state').returncode, 0)

    def test_child_help_cannot_bypass_action_intent_or_replay_uncertain_launch(self):
        source, target = self.identities()
        request = {'schema': 'kilix.actions/v1', 'operation_id': 'uncertain-operation',
                   'operation': 'pane.open', 'source': source, 'target': target,
                   'params': {'argv': ['fixture', '--help'], 'cwd': str(self.home),
                              'title': 'fixture', 'placement': 'new-tab'}}
        env = dict(self.env, KILIX_TEST_FAIL_AFTER_LAUNCH='1')
        first = self.run_cli('action', '--request-json', '-', data=json.dumps(request), env=env)
        self.assertEqual(first.returncode, 1)
        self.assertEqual(json.loads(first.stdout)['status'], 'uncertain')
        second = self.run_cli('action', '--request-json', '-', data=json.dumps(request))
        self.assertEqual(second.returncode, 1)
        self.assertTrue(json.loads(second.stdout)['duplicate'])
        calls = [json.loads(line) for line in (self.home / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual(sum(call[0] == 'launch' for call in calls), 1)

    def test_native_offline_help_status_and_literal_launch(self):
        env = {key: value for key, value in self.env.items() if not key.startswith('KITTY_')}
        for args in (('help',), ('help', 'pane'), ('help', 'settings'), ('transcript', '--help')):
            self.checked(*args, native=True, env=env)
        status = json.loads(self.checked('status', '--json', native=True, env=env).stdout)
        self.assertFalse(status['control_available'])
        self.assertFalse(status['wsl_required'])
        self.checked('launch', 'native-fixture.exe', '', 'a "quote"', '$HOME', native=True)
        calls = [json.loads(line) for line in (self.home / 'calls.jsonl').read_text().splitlines()]
        self.assertEqual(calls[-1], ['launch', '--type=tab', '--', 'native-fixture.exe', '', 'a "quote"', '$HOME'])

    @unittest.skipUnless(shutil.which('tmux'), 'tmux is needed for the owned socket fixture')
    def test_tmux_operates_only_on_an_explicit_owned_socket(self):
        socket = str(self.home / 'owned.sock')
        self.addCleanup(subprocess.run, ['tmux', '-S', socket, 'kill-server'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.checked('tmux', '--socket', socket, 'new', 'fixture', '--cwd', str(self.home))
        result = json.loads(self.checked('tmux', '--socket', socket, '--json', 'list').stdout)
        self.assertTrue(result['ok'])
        self.checked('tmux', '--socket', socket, 'send', 'fixture', 'literal $(do-not-run)')
        self.assertNotEqual(self.run_cli('tmux', 'list').returncode, 0)
        self.checked('tmux', '--socket', socket, 'close', 'fixture')


if __name__ == '__main__':
    unittest.main()
