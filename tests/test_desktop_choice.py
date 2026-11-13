"""Saved first-login choices, including a real terminal prompt and launch."""
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('desktop_choice', ROOT / 'config/desktop_choice.py')
choice = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(choice)


class DesktopChoiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'storage/config'
        engine = self.root / 'prebuilt/bin'
        engine.mkdir(parents=True)
        for name in ('kitty', 'kitten'):
            executable = engine / name
            executable.write_text('#!/bin/sh\nexit 99\n')
            executable.chmod(0o700)
        self.env = {'PATH': os.defpath, 'HOME': str(self.root / 'home'),
                    'KILIX_STORAGE_HOME': str(self.root / 'storage'),
                    'KILIX_USER_CONFIG_DIRECTORY': str(self.config),
                    'KILIX_CONFIG_HOME': str(self.config),
                    'GPU_TERMINAL_HOME': str(self.root / 'shared'),
                    'GPU_TERMINAL_SOURCE_HOME': str(self.root / 'sources'),
                    'KILIX_IN_OVERLAY': '1', 'KILIX_PREBUILT_HOME': str(engine.parent)}

    def run_choice(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'config/desktop_choice.py'),
                               '--config', str(self.config), *args],
                              capture_output=True, text=True, timeout=10)

    def test_primary_menu_and_secondary_choices(self):
        output = io.StringIO()
        self.assertEqual(choice.prompt('auto', io.StringIO('4\n'), output), 'zell')
        self.assertIn('zell — RPG Desktop', output.getvalue())
        self.assertNotIn('Kilix Cap', output.getvalue())
        output = io.StringIO()
        self.assertEqual(choice.prompt('auto', io.StringIO('m\n6\n'), output), 'cap')
        self.assertIn('Other desktops', output.getvalue())

    def test_saved_choice_replaces_duplicates_preserves_settings_and_resets_flavor(self):
        self.config.mkdir(parents=True)
        (self.config / 'kilix.env').write_text('# custom\nKEEP=yes\nKILIX_DESKTOP_PROVIDER=land\n'
                                               'KILIX_DESKTOP_PROVIDER=xp\nKILIX_DESKTOP_FLAVOR=xp\n')
        self.assertEqual(self.run_choice('set', 'kilix95').returncode, 0)
        self.assertEqual(self.run_choice('show').stdout, 'external\n')
        text = (self.config / 'kilix.env').read_text()
        self.assertTrue(text.startswith('# custom\nKEEP=yes\n'))
        self.assertEqual(text.count('KILIX_DESKTOP_PROVIDER='), 1)
        self.assertIn('KILIX_DESKTOP_FLAVOR=95\n', text)
        self.assertTrue(choice.state(self.config)['complete'])

    def test_request_preserves_completed_choice_and_noninteractive_request(self):
        self.assertEqual(self.run_choice('request').returncode, 0)
        self.assertEqual(self.run_choice('choose', '--session-start').stdout, '')
        self.assertFalse(choice.state(self.config)['complete'])
        self.assertEqual(self.run_choice('set', 'zell-neuronte').returncode, 0)
        self.assertEqual(self.run_choice('request').returncode, 0)
        self.assertEqual(self.run_choice('choose', '--session-start').stdout, 'zell\n')

    def test_old_installation_without_request_does_not_change_desktop(self):
        self.assertEqual(self.run_choice('choose', '--session-start').stdout, '')
        self.assertIsNone(choice.state(self.config))

    def test_symlinked_settings_are_refused_without_changing_target(self):
        self.config.mkdir(parents=True)
        target = self.root / 'target'
        target.write_text('untouched\n')
        (self.config / 'kilix.env').symlink_to(target)
        self.assertNotEqual(self.run_choice('set', 'zell').returncode, 0)
        self.assertEqual(target.read_text(), 'untouched\n')
        self.assertFalse((self.config / 'desktop-choice.json').exists())

    def test_prompt_deferral_is_pending_and_invalid_input_reprompts(self):
        output = io.StringIO()
        self.assertIsNone(choice.prompt('auto', io.StringIO('bogus\nq\n'), output))
        self.assertIn('Enter one of the numbers shown', output.getvalue())

    def test_first_login_prompt_and_later_saved_selection_override_image_environment(self):
        runtime = self.root / 'zell'
        (runtime / 'weights').mkdir(parents=True)
        (runtime / 'weights/neuronte-split-v4.nrt').write_bytes(b'fixture')
        executable = runtime / 'zell-neuronte'
        executable.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                              'print("ZELL_LAUNCH="+json.dumps([sys.argv[1:],'
                              'os.environ.get("ZELL_NEURONTE_WEIGHTS"),'
                              'os.environ.get("KILIX_DESKTOP_NAME")]))\n')
        executable.chmod(0o700)
        env = {**self.env, 'ZELL_NEURONTE_HOME': str(runtime),
               'KILIX_DESKTOP_PROVIDER': 'external', 'KILIX_DESKTOP_NAME': 'Kilix 95'}
        self.assertEqual(self.run_choice('request').returncode, 0)
        master, slave = pty.openpty()
        child = subprocess.Popen([str(ROOT / 'kilix'), 'desktop', '--session-start', '--fixture'],
                                 env=env, stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        output = b''
        sent = False
        deadline = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    output += data
                    if b'Choose a number:' in output and not sent:
                        os.write(master, b'4\n')
                        sent = True
                if child.poll() is not None:
                    break
        finally:
            os.close(master)
        self.assertEqual(child.wait(timeout=3), 0, output.decode(errors='replace'))
        self.assertTrue(sent, output)
        self.assertIn(b'ZELL_LAUNCH=', output)
        self.assertEqual(choice.saved(self.config), 'zell')
        later = subprocess.run([str(ROOT / 'kilix'), 'desktop', '--session-start', 'two words'],
                               env=env, stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=15)
        self.assertEqual(later.returncode, 0, later.stderr)
        self.assertNotIn('Choose your default', later.stderr)
        payload = json.loads(later.stdout.split('ZELL_LAUNCH=', 1)[1].splitlines()[0])
        self.assertEqual(payload, [['two words'], str(runtime / 'weights/neuronte-split-v4.nrt'), 'zell'])


if __name__ == '__main__':
    unittest.main()
