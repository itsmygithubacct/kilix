"""Exercise SSH framing and literal command boundaries without contacting hosts."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

CONFIG = Path(__file__).resolve().parents[2] / 'config'
sys.path.insert(0, str(CONFIG))
# The SDK is imported lazily by the test setup so staged/source environments match.


class DesktopSSHTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import windows_control
        cls.module = windows_control

    def test_host_is_one_literal_ssh_argument_and_request_is_stdin(self):
        request = {'schema': 'kilix.desktop/v1', 'operation': 'type',
                   'target': {'hwnd': '17', 'pid': 41, 'started': '123'},
                   'params': {'text': 'é 字 😀 ; $(untrusted) "'}, 'timeout': 4}
        reply = {'schema': request['schema'], 'operation': 'type', 'ok': True, 'result': {}}
        completed = subprocess.CompletedProcess([], 0, json.dumps(reply), '')
        with patch.object(self.module.subprocess, 'run', return_value=completed) as run:
            self.assertEqual(self.module.SSHTransport('fixture-alias')(request), reply)
        args, kwargs = run.call_args
        self.assertEqual(args[0][:7], ['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=10', '--', 'fixture-alias', self.module.remote_script()])
        self.assertEqual(json.loads(kwargs['input']), request)
        self.assertNotIn('untrusted', args[0][-1])
        self.assertEqual(kwargs['timeout'], 39)

    def test_portable_root_is_data_not_powershell_source(self):
        root = "C:\\A & B\\it's $(untrusted)\\Kilix"
        command = self.module.remote_script(root)
        script = base64.b64decode(command.split()[-1]).decode('utf-16-le')
        self.assertNotIn(root, script)
        self.assertNotIn('untrusted', script)
        self.assertIn(base64.b64encode(root.encode()).decode(), script)

    def test_bad_hosts_are_rejected_without_a_process(self):
        for host in ('-oProxyCommand=evil', 'host;evil', 'host\ncommand', 'a b', ''):
            with self.subTest(host=host), self.assertRaises(ValueError):
                self.module.SSHTransport(host)

    def test_timeout_is_not_retried(self):
        request = {'schema': 'kilix.desktop/v1', 'operation': 'click', 'params': {}}
        with patch.object(self.module.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ssh', 55)) as run:
            result = self.module.SSHTransport('fixture')(request)
        run.assert_called_once()
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'timeout')
        self.assertIn('may have run', result['error']['message'])

    def test_wrong_operation_reply_and_nonzero_success_are_errors(self):
        request = {'schema': 'kilix.desktop/v1', 'operation': 'status', 'params': {}}
        for code, op in [(0, 'type'), (1, 'status')]:
            reply = {'schema': request['schema'], 'operation': op, 'ok': True, 'result': {}}
            with patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, json.dumps(reply), '')):
                result = self.module.SSHTransport('fixture')(request)
            self.assertFalse(result['ok'])

    def test_wrapper_keeps_launched_program_host_option_literal(self):
        argv = ['--host', 'fixture', 'launch', '--', 'app.exe', '--host', 'literal']
        with patch.object(self.module.windows, 'main', return_value=0) as main:
            self.assertEqual(self.module.main(argv), 0)
        self.assertEqual(main.call_args.args[0], ['launch', '--', 'app.exe', '--host', 'literal'])
        self.assertEqual(main.call_args.kwargs['runner'].host, 'fixture')


if __name__ == '__main__':
    unittest.main()
