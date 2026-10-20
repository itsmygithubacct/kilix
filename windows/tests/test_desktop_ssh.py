"""Exercise SSH framing and literal command boundaries without contacting hosts."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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
                   'params': {'text': 'é 字 😀 \n ; $(untrusted) "'}, 'timeout': 4}
        reply = {'schema': request['schema'], 'operation': 'type', 'ok': True, 'result': {}}
        completed = subprocess.CompletedProcess([], 0, json.dumps(reply), '')
        with patch.object(self.module.subprocess, 'run', return_value=completed) as run:
            self.assertEqual(self.module.SSHTransport('fixture-alias')(request), reply)
        args, kwargs = run.call_args
        self.assertEqual(args[0][:7], ['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=10', '--', 'fixture-alias', self.module.remote_script()])
        lines = kwargs['input'].splitlines()
        self.assertEqual(json.loads(lines[0]), request)
        self.assertEqual(json.loads(lines[1]), (CONFIG / '_windows_process.cs').read_text())
        self.assertNotIn('untrusted', lines[1])
        self.assertTrue(kwargs['input'].endswith('\n'))
        self.assertEqual(kwargs['input'].count('\n'), 2)
        self.assertNotIn('untrusted', args[0][-1])
        self.assertEqual(kwargs['timeout'], 39)

    def test_portable_root_is_data_not_powershell_source(self):
        root = "C:\\A & B\\it's $(untrusted)\\Kilix"
        command = self.module.remote_script(root)
        script = base64.b64decode(command.split()[-1]).decode('utf-16-le')
        self.assertNotIn(root, script)
        self.assertNotIn('untrusted', script)
        request = {'schema': 'kilix.desktop/v1', 'operation': 'status', 'params': {}}
        reply = dict(schema=request['schema'], operation='status', ok=True, result={})
        with patch.object(self.module.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(reply), '')) as run:
            self.assertEqual(self.module.SSHTransport('fixture', root)(request), reply)
        packet = json.loads(run.call_args.kwargs['input'].splitlines()[0])
        self.assertEqual(packet['root'], root)
        self.assertEqual(json.loads(packet['request']), request)
        self.assertEqual(run.call_args.kwargs['input'].count('\n'), 2)

    def test_long_unicode_portable_root_cannot_grow_the_windows_shell_command(self):
        ordinary = self.module.remote_script(r'C:\Kilix')
        self.assertEqual(self.module.remote_script('C:\\' + '\u5b57' * 1000), ordinary)
        self.assertLess(len(ordinary), 8000)
        self.assertLess(len(self.module.remote_script()), 8000)

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

    def test_timeout_preserves_bounded_partial_stderr_without_replaying_stdout(self):
        request = {'schema': 'kilix.desktop/v1', 'operation': 'capture', 'params': {}}
        for diagnostic in ('x' * 3000 + ' native execution finished', b'incomplete UTF-8: \xff'):
            with self.subTest(diagnostic=type(diagnostic).__name__), patch.object(
                    self.module.subprocess, 'run', side_effect=subprocess.TimeoutExpired(
                        'ssh', 55, output=b'private image data', stderr=diagnostic)) as run:
                result = self.module.SSHTransport('fixture')(request)
            run.assert_called_once()
            message = result['error']['message']
            self.assertEqual(result['error']['code'], 'timeout')
            self.assertIn('may have run', message)
            self.assertLess(len(message), 2100)
            self.assertNotIn('private image data', message)
            self.assertIn('native execution finished' if isinstance(diagnostic, str) else 'incomplete UTF-8', message)

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


@unittest.skipUnless(os.name == 'nt', 'requires Windows PowerShell and native pipe inheritance')
class NativeDesktopSSHTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import windows_control
        cls.module = windows_control
        cls.directory = tempfile.TemporaryDirectory(prefix='kilix-ssh-\u00e9-\u5b57-')
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        cls.exe = cls.root / 'kilix-cli.exe'
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        subprocess.run([str(compiler), '/nologo', '/target:exe', '/out:' + str(cls.exe),
                        str(Path(__file__).with_name('ssh-process-fixture.cs'))], check=True,
                       capture_output=True, text=True, timeout=30)

    def tearDown(self):
        subprocess.run([str(self.exe), 'cleanup'], check=True, timeout=10)
        for path in self.root.glob('*.pid'):
            path.unlink()

    def invoke(self, mode, padding=''):
        (self.root / 'mode').write_text(mode)
        request = dict(schema='kilix.desktop/v1', operation='status', timeout=1,
                       params={'padding': padding})
        command = self.module.remote_script(str(self.root)).split()
        started = time.monotonic()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        try:
            stdout, stderr = process.communicate(
                self.module.request_line(request, str(self.root)).encode(), timeout=35)
        except subprocess.TimeoutExpired:
            exit_code = process.poll()
            process.kill()
            subprocess.run([str(self.exe), 'cleanup'], check=True, timeout=10)
            _, stderr = process.communicate(timeout=10)
            raise self.failureException(f'Native SSH wrapper exceeded its request-plus-grace deadline (exit={exit_code}): '
                                        + stderr.decode('utf-8', errors='replace')[-2000:]) from None
        self.assertLess(time.monotonic() - started, 35)
        return process.returncode, stdout.decode('utf-8-sig'), stderr.decode('utf-8-sig')

    def test_success_preserves_unicode_and_separate_stderr(self):
        code, stdout, stderr = self.invoke('normal')
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)['result']['text'], 'caf\u00e9 \u5b57')
        self.assertIn('fixture diagnostic', stderr)

    def test_exited_child_with_inherited_output_pipe_has_a_deadline(self):
        code, stdout, stderr = self.invoke('output')
        self.assertNotEqual(code, 0)
        self.assertIn('timed out during stdout', stderr)
        self.assertIn('may have run', stderr)
        self.assertEqual(stdout, '')

    def test_child_that_never_reads_large_request_has_a_deadline(self):
        code, stdout, stderr = self.invoke('stdin', 'x' * (512 * 1024))
        self.assertNotEqual(code, 0)
        self.assertIn('timed out during request input', stderr)
        self.assertIn('may have run', stderr)
        self.assertEqual(stdout, '')


if __name__ == '__main__':
    unittest.main()
