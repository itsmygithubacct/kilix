import importlib.util
import os
from pathlib import Path
import unittest
import errno
import io
import json
import pwd
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('companion', Path(__file__).resolve().parents[1] / 'wsl/companion.py')
module = importlib.util.module_from_spec(spec)
with patch.object(sys, 'path', [str(Path(spec.origin).parent), *sys.path]):
    spec.loader.exec_module(module)

class CompanionTests(unittest.TestCase):
    def test_arguments_and_linux_directory_are_preserved(self):
        with patch.dict(os.environ, {'WSL_DISTRO_NAME':'Ubuntu'}):
            argv=module.translate_launch(['--location=vsplit','--cwd',"/tmp/a b'c",'--','printf','%s','$(not-a-shell)'])
        self.assertEqual(argv[:2],['--location=vsplit','--'])
        self.assertEqual(argv[2:9],['wsl.exe','--distribution','Ubuntu','--user',pwd.getpwuid(os.getuid()).pw_name,'--cd',"/tmp/a b'c"])
        self.assertEqual(argv[-3:],['printf','%s','$(not-a-shell)'])
    def test_unknown_launch_option_is_rejected(self):
        with self.assertRaises(ValueError): module.translate_launch(['--watcher=/tmp/program.py'])
    def test_remote_endpoint_must_be_local_and_authenticated(self):
        for route in ('tcp:0.0.0.0:20','tcp:192.168.1.2:20','unix:/tmp/sock',''):
            with patch.dict(os.environ, {'KITTY_LISTEN_ON':route,'KITTY_RC_PASSWORD':'test','KITTY_WINDOW_ID':'1'}):
                with self.assertRaises(ValueError): module.require_control()
        with patch.dict(os.environ, {'KITTY_LISTEN_ON':'tcp:127.0.0.1:123','KITTY_RC_PASSWORD':'','KITTY_WINDOW_ID':'1'}):
            with self.assertRaises(ValueError): module.require_control()

    @unittest.skipUnless(hasattr(os, 'memfd_create'), 'Linux memfd required')
    def test_portable_python_constants_enforce_real_kernel_seals(self):
        import fcntl
        wrapper = Path(__file__).resolve().parents[1] / 'wsl/needle-run.py'
        with patch('runpy.run_path') as launch, patch.object(module.sys, 'argv', ['needle-run', '/tmp/needle_cli.py']), patch.object(sys, 'path', sys.path.copy()):
            exec(compile(wrapper.read_text(), str(wrapper), 'exec'), {})
            launch.assert_called_once()
        fd = os.memfd_create('companion-test', os.MFD_ALLOW_SEALING)
        try:
            os.write(fd, b'verified image')
            seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GET_SEALS), seals)
            with self.assertRaises(OSError) as denied:
                os.write(fd, b'changed')
            self.assertEqual(denied.exception.errno, errno.EPERM)
        finally:
            os.close(fd)

    def test_linux_environment_values_empty_and_removal(self):
        with patch.dict(os.environ, {'WSL_DISTRO_NAME': 'Ubuntu', 'REMOVE_ME': 'old'}):
            argv = module.translate_launch(['--env', 'VALUE=a b;$HOME', '--env=EMPTY=',
                '--env', 'REMOVE_ME', '--env', 'VALUE=final "value"', '--', sys.executable,
                '-c', 'import os,json; print(json.dumps([os.getenv("VALUE"),os.getenv("EMPTY"),os.getenv("REMOVE_ME")]))'])
            result = subprocess.check_output(argv[argv.index('/usr/bin/env'):], text=True)
        self.assertEqual(json.loads(result), ['final "value"', '', None])

    def test_path_does_not_grow_on_nested_calls(self):
        with patch.dict(os.environ, module.environment()):
            first = module.environment()['PATH']
            with patch.dict(os.environ, {'PATH': first}):
                self.assertEqual(first, module.environment()['PATH'])

    def test_help_needs_no_control_and_never_parses_json(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(module.subprocess, 'call', return_value=0) as call:
            for args in ([], ['--help'], ['ls', '--help'], ['launch', '--help'],
                         ['launch', '--type=tab', '--help'], ['launch', '--cwd', '/tmp', '-h']):
                self.assertEqual(module.remote(args), 0)
                self.assertTrue(set(call.call_args.args[0]) & {'--help', '-h'})

    def test_child_help_and_option_values_do_not_bypass_control(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(module.subprocess, 'call') as call:
            for args in (['launch', '--', 'printf', '--help'],
                         ['launch', 'printf', '--help'],
                         ['launch', '--title', '--help', '--', 'printf'],
                         ['launch', '--title=--help', '--', 'printf']):
                with self.assertRaisesRegex(ValueError, 'Open a new'):
                    module.remote(args)
            call.assert_not_called()

    def test_fullscreen_uses_authenticated_control_and_exact_invoking_pane(self):
        env = {'KITTY_LISTEN_ON': 'tcp:127.0.0.1:1234', 'KITTY_RC_PASSWORD': 'test', 'KITTY_WINDOW_ID': '7'}
        with patch.dict(os.environ, env, clear=True), patch.object(module.subprocess, 'call', return_value=0) as call:
            self.assertEqual(module.main(['fullscreen']), 0)
            self.assertEqual(call.call_args.args[0], [str(module.KITTEN), '@', '--use-password', 'always',
                             'resize-os-window', '--match=id:7', '--action=toggle-fullscreen'])
            self.assertEqual(call.call_args.kwargs['env']['KITTY_LISTEN_ON'], env['KITTY_LISTEN_ON'])
            for value in ('', '0', '-1', '7 or all'):
                with patch.dict(os.environ, {'KITTY_WINDOW_ID': value}):
                    with self.assertRaises(ValueError): module.main(['fullscreen'])
            self.assertEqual(call.call_count, 1)

    def test_fullscreen_help_needs_no_connection(self):
        with patch.dict(os.environ, {}, clear=True), patch('sys.stdout', new_callable=io.StringIO) as output:
            with self.assertRaises(SystemExit) as result: module.main(['fullscreen', '--help'])
            self.assertEqual(result.exception.code, 0)
            self.assertIn('invoking native Kilix window', output.getvalue())

    def test_packaged_sdk_help_is_offline_but_actions_still_require_control(self):
        source = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'wsl').mkdir()
            (root / 'wsl-bin').mkdir()
            for pattern in ('*.py', '*.json'):
                for item in (source / 'windows/wsl').glob(pattern):
                    shutil.copy2(item, root / 'wsl' / item.name)
            shutil.copy2(source / 'windows/wsl/kitten.sh', root / 'wsl-bin/kitten')
            (root / 'wsl-bin/kitten').chmod(0o755)
            (root / 'wsl-bin/config').symlink_to(source / 'config', target_is_directory=True)
            env = {'HOME': directory, 'PATH': os.defpath}
            for args in (['pane', '--help'], ['tab', '--help'], ['tab', 'new', '--help'],
                         ['watch', '--help'], ['new-tab', '--help']):
                with self.subTest(args=args):
                    result = subprocess.run([sys.executable, str(root / 'wsl/companion.py'), *args],
                                            env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('usage:', result.stdout)
            result = subprocess.run([sys.executable, str(root / 'wsl/companion.py'), 'pane', 'list'],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Open a new Kilix WSL window', result.stderr)

    def test_listing_redacts_credentials_recursively(self):
        result = subprocess.CompletedProcess([], 0, json.dumps([{'env': {'KITTY_RC_PASSWORD':'secret','KEEP':'yes'}}]), '')
        with patch.object(module, 'require_control'), patch.object(module.subprocess, 'run', return_value=result), patch('sys.stdout', new_callable=io.StringIO) as output:
            module.remote(['ls'])
        self.assertEqual(json.loads(output.getvalue()), [{'env': {'KEEP':'yes'}}])

    def test_bad_options_fail_instead_of_succeeding_silently(self):
        for args in (['apps','typo'], ['games','list','typo']):
            with self.assertRaises(ValueError): module.main(args)
        for args in (['--hold=false'], ['--env==bad'], ['--type=clipboard'], ['--cwd=last_reported']):
            with self.assertRaises(ValueError): module.translate_launch(args)

if __name__=='__main__': unittest.main()
