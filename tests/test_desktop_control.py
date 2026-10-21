"""The public desktop verb must preserve argv and avoid engine/storage setup."""
import json
import os
from pathlib import Path
import subprocess
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
             patch.object(module.shutil, 'which', side_effect=lambda name: '/catalog/kilix-needle' if name=='kilix-needle' else None), \
             patch.object(module.os, 'execv', side_effect=SystemExit(0)) as execute:
            with self.assertRaises(SystemExit): module.main(argv)
            execute.assert_called_once_with('/catalog/kilix-needle', ['/catalog/kilix-needle', 'desktop-action', *argv])
            os.environ['KILIX_DESKTOP_CLI'] = '/missing/explicit'
            execute.reset_mock()
            self.assertEqual(module.main(argv), 7)
            execute.assert_not_called()

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
