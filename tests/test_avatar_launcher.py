"""Avatar dispatch preserves arguments and only installs immutable sources."""
import json
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from _env_support import sandbox_env

ROOT=Path(__file__).resolve().parents[1]
INSTALLER=ROOT/'scripts/install-kilix-avatar.sh'
class AvatarLauncherTests(unittest.TestCase):
    def test_pins_and_help_do_not_install(self):
        with tempfile.TemporaryDirectory() as root:
            env=sandbox_env(KILIX_AVATAR_SOURCES=root+'/absent')
            for flag in ('--help','--print-refs'):
                result=subprocess.run([str(INSTALLER),flag],env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
            self.assertRegex(result.stdout,r'kilix-avatar=[0-9a-f]{40}')
            self.assertFalse(Path(root,'absent').exists())
    def test_moving_ref_refused(self):
        result=subprocess.run([str(INSTALLER)],env=sandbox_env(KILIX_AVATAR_REF='main'),capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('40-character',result.stderr)
    def test_installed_prefix_and_literal_arguments(self):
        with tempfile.TemporaryDirectory(prefix='avatar space ') as root:
            prefix=Path(root);(prefix/'bin').mkdir()
            app=prefix/'bin/kilix-avatar'
            app.write_text('#!/usr/bin/python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n');app.chmod(0o755)
            env=sandbox_env()
            env.update(KILIX_AVATAR_PREFIX=root,KILIX_HOME=str(ROOT),GPU_TERMINAL_HOME=root+'/storage',PATH='/usr/bin:/bin')
            result=subprocess.run([str(ROOT/'kilix'),'avatar','--model','codex','--say','hello world'],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),['--chat','--model','codex','--say','hello world'])
            result=subprocess.run([str(ROOT/'kilix'),'avatar','--install-only'],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,'')
