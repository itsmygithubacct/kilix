"""The Help launcher preserves questions and keeps acquisition explicit."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]


class HelpLauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.host = root / 'checkout'
        (self.host / 'config').mkdir(parents=True)
        (self.host / 'scripts').mkdir()
        for name in ('kilix', 'kilix-settings'):
            shutil.copy2(ROOT / name, self.host / name)
        for name in ('kitty.conf', 'kilix.env'):
            shutil.copy2(ROOT / 'config' / name, self.host / 'config' / name)
        shutil.copytree(ROOT / 'config/kilix_sdk', self.host / 'config/kilix_sdk')
        (self.host / 'config/content_app.py').write_text(
            "import json,os,sys\nprint(json.dumps({'argv':sys.argv[1:],"
            "'sizer':os.environ.get('PLEBIAN_MODEL_SIZER')}))\n")
        installer = self.host / 'scripts/install-kilix-tts-sizer.sh'
        installer.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HELP_TEST_LOG"\nprintf /bin/true\n')
        installer.chmod(0o700)
        self.log = root / 'installer.log'
        self.env = sandbox_env(HOME=str(root / 'home'), PATH='/usr/bin:/bin',
                               KILIX_STORAGE_HOME=str(root / 'storage'), HELP_TEST_LOG=str(self.log))
        self.env.pop('PLEBIAN_MODEL_SIZER', None)

    def run_help(self, *args):
        return subprocess.run([str(self.host / 'kilix'), *args], env=self.env,
                              capture_output=True, text=True, timeout=20)

    def test_answer_preserves_shell_metacharacters_as_question_data(self):
        question = '--sizer $(touch should-not-exist); How do I list panes?'
        result = self.run_help('help-answer', '--json', '--', question)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['argv'],
                         ['run', 'kilix-help-llm', '--', 'answer', '--json', '--', question])
        self.assertEqual(self.log.read_text(), '--print-path\n')

    def test_only_setup_installs_provider_and_search_needs_no_provider(self):
        result = self.run_help('help-setup')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['argv'], ['run', 'kilix-help-llm', '--', 'setup'])
        self.assertEqual(self.log.read_text(), '\n')
        result = self.run_help('help-search', 'List panes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['argv'], ['run', 'kilix-help-llm', '--', 'lookup', 'List panes'])
        self.assertEqual(self.log.read_text(), '\n')

    def test_explicit_provider_is_forwarded_without_install(self):
        result = self.run_help('help-answer', '--sizer', '/provider with spaces', 'List panes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        self.assertEqual(json.loads(result.stdout)['argv'][-3:],
                         ['--sizer', '/provider with spaces', 'List panes'])
