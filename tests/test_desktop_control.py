"""The public desktop verb must preserve argv and avoid engine/storage setup."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DesktopControlTests(unittest.TestCase):
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
