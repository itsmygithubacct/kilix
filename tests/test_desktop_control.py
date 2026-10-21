"""The public desktop verb must preserve argv and avoid engine/storage setup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


class DesktopControlTests(unittest.TestCase):
    def test_catalog_command_and_explicit_selection(self):
        spec = importlib.util.spec_from_file_location('desktop_delegate', ROOT/'config/desktop_control.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        argv = ['--parse-only', 'keyboard', 'type', '--text', 'literal; $(text)']
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(module, 'installed_command', return_value=None), \
             patch.object(module.shutil, 'which', side_effect=lambda name: '/catalog/kilix-needle' if name=='kilix-needle' else None), \
             patch.object(module.os, 'execv', side_effect=SystemExit(0)) as execute:
            with self.assertRaises(SystemExit): module.main(argv)
            execute.assert_called_once_with('/catalog/kilix-needle', ['/catalog/kilix-needle', 'desktop-action', *argv])
            os.environ['KILIX_DESKTOP_CLI'] = '/missing/explicit'
            execute.reset_mock()
            self.assertEqual(module.main(argv), 7)
            execute.assert_not_called()

    def test_verified_catalog_dispatch_binds_content_root(self):
        spec = importlib.util.spec_from_file_location('desktop_delegate_catalog', ROOT/'config/desktop_control.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {'KILIX_DATA_HOME': directory}, clear=True), \
             patch.object(sys, 'path', [str(ROOT/'config'), *sys.path]):
            self.assertIsNone(module.installed_command())
            self.assertEqual(list(Path(directory).iterdir()), [])
            install_root = Path(directory)/'desktop-apps'
            install_root.mkdir()
            from kilix_sdk import content
            with patch.object(content.Installer, 'ready', return_value='/installed/kilix-needle') as ready, \
                 patch.object(content.Installer, 'ensure', side_effect=AssertionError('no installation')), \
                 patch.object(module.shutil, 'which', return_value=None), \
                 patch.object(module.os, 'execve', side_effect=SystemExit(0)) as execute:
                argv = ['--parse-only', 'window', 'list']
                with self.assertRaises(SystemExit): module.main(argv)
                self.assertEqual(ready.call_args.args[0].content_id, 'kilix-needle')
                binary, forwarded, environment = execute.call_args.args
                self.assertEqual(binary, '/installed/kilix-needle')
                self.assertEqual(forwarded, [binary, 'desktop-action', *argv])
                self.assertEqual(environment['KILIX_CONTENT_ROOT'], str(install_root))

    def test_read_only_help_and_exact_delegation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, HOME=str(root), GPU_TERMINAL_HOME=str(root/'storage'))
            result = subprocess.run([str(ROOT/'kilix'),'desktop-action','--help'],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((root/'storage').exists())
            delegate=root/'delegate'
            delegate.write_text('#!/usr/bin/python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            delegate.chmod(0o700)
            env['KILIX_DESKTOP_CLI']=str(delegate)
            argv=['--parse-only','keyboard','type','--text','$(literal); --yes "quoted"']
            result=subprocess.run([str(ROOT/'kilix'),'desktop-action',*argv],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),argv)
            self.assertFalse((root/'storage').exists())
            env['KILIX_DESKTOP_CLI']=str(root/'missing')
            result=subprocess.run([str(ROOT/'kilix'),'desktop-action','window','list'],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,7)
            self.assertFalse((root/'storage').exists())


if __name__=='__main__': unittest.main()
