"""Exercise the shipped shell launcher, with no live remote-control endpoint."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DefaultTabLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.log = self.home / 'argv.json'
        engine = self.home / '.local/gpu_terminal/kilix/prebuilt/kitty.app/bin'
        engine.mkdir(parents=True)
        (engine / 'kitty').write_text('#!/bin/sh\nexit 0\n')
        (engine / 'kitty').chmod(0o755)
        (engine / 'kitten').write_text(f'''#!{sys.executable}
import json, os, sys
with open(os.environ['ARGV_LOG'], 'w') as target:
    json.dump(sys.argv[1:], target)
sys.exit(int(os.environ.get('REMOTE_RC', '0')))
''')
        (engine / 'kitten').chmod(0o755)
        self.env = {'HOME': str(self.home), 'PATH': '/usr/bin:/bin',
                    'TERM': 'xterm-kitty', 'TMPDIR': str(self.home),
                    'KITTY_WINDOW_ID': '7', 'KITTY_LISTEN_ON': 'unix:fixture',
                    'ARGV_LOG': str(self.log)}

    def launch(self, args, **env):
        return subprocess.run([str(ROOT / 'kilix'), 'run', *args],
                              env={**self.env, **env}, capture_output=True, timeout=20)

    def test_chrome_and_qemu_open_exactly_one_tab_with_unchanged_arguments(self):
        for app in ('google-chrome', 'qemu-system-x86_64'):
            with self.subTest(app=app):
                arguments = [app, '', 'space\nquote\'";$()', 'byte-\udcff']
                result = self.launch(arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                command = json.loads(self.log.read_text())
                self.assertEqual(command.count('--type=tab'), 1)
                self.assertIn('KILIX_IN_OVERLAY=1', command)
                self.assertEqual(command[command.index('--tab-title') + 1], app)
                self.assertEqual(command[command.index('--') + 1:],
                                 [str(ROOT / 'kilix'), 'run', *arguments])

    def test_remote_refusal_does_not_fall_back_to_external_execution(self):
        result = self.launch(['qemu-system-x86_64', '-nodefaults'], REMOTE_RC='37')
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertIn('--type=tab', json.loads(self.log.read_text()))

    def test_manager_owned_tab_does_not_spawn_a_second_tab(self):
        result = self.launch(['--size', '800x600', 'qemu-system-x86_64', '-nodefaults'],
                             KILIX_IN_OVERLAY='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'must run on a terminal', result.stderr)
        self.assertFalse(self.log.exists())

    def test_nonterminal_refusal_and_browser_profile_cleanup(self):
        result = self.launch(['google-chrome'], KITTY_WINDOW_ID='', KITTY_LISTEN_ON='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'must run on a terminal', result.stderr)
        self.assertFalse(self.log.exists())
        profiles = list(self.home.rglob('app-profiles'))
        self.assertTrue(profiles)
        self.assertTrue(all(not list(path.iterdir()) for path in profiles))


if __name__ == '__main__':
    unittest.main()
