"""The launcher verifies Paradee before it asks Piper for anything."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]
STUB = '''#!/usr/bin/env python3
import os
import sys
with open(os.environ["EVENTS"], "a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
command = sys.argv[1]
if command == "engine":
    raise SystemExit(0 if os.environ.get("PARADEE_ENGINE_OK") == "1" else 1)
if command == "session":
    sys.stdout.write("System voice ready: paradee af_heart\\n")
raise SystemExit(0)
'''


class ParadeeLauncherTests(unittest.TestCase):
    def setUp(self):
        host = (ROOT / 'kilix').read_text()
        self.dispatch = host[host.index('  tts|kilix-tts)'):host.index('  stt|kilix-stt)')]
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'config').mkdir()
        tool = self.root / 'tool'
        tool.write_text('#!/bin/bash\nprintf "%s\\n" tool "$@" >> "$EVENTS"\n')
        tool.chmod(0o755)
        installer = self.root / 'scripts/install-kilix-piper-tts.sh'
        installer.write_text('#!/bin/bash\necho installed >> "$EVENTS"\n')
        installer.chmod(0o755)
        self.runtime = self.root / 'config/paradee_runtime.py'
        self.runtime.write_text(STUB)
        self.runtime.chmod(0o755)
        wrapper = self.root / 'wrapper'
        wrapper.write_text('set -eu\n_kilix_voice_tool() { echo "$TOOL"; }\ncase "$1" in\n'
                           + self.dispatch + '\nesac\n')
        self.wrapper = wrapper
        self.events = self.root / 'events'
        self.env = sandbox_env(KILIX_HOME=str(self.root), TOOL=str(tool), EVENTS=str(self.events))

    def run_tts(self, *arguments, **env):
        self.events.write_text('')
        return subprocess.run(['bash', str(self.wrapper), 'tts', *arguments],
                              env=dict(self.env, **env), capture_output=True, text=True)

    def test_install_verifies_paradee_and_does_not_call_piper(self):
        for arguments in (['--install=paradee-8m-v1.2'], ['--install', 'paradee-8m-v1.2']):
            result = self.run_tts(*arguments)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(self.events.read_text().splitlines(), ['install'])
            self.assertNotIn('installed', self.events.read_text())

    def test_install_strips_its_flag_and_keeps_later_arguments(self):
        result = self.run_tts('--install=paradee-8m-v1.2', '--set', 'engine=piper')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.events.read_text().splitlines(),
                         ['install', 'tool', '--set', 'engine=piper'])

    def test_saved_paradee_session_does_not_fetch_piper(self):
        result = self.run_tts('--system-voice', '--speak=hello', PARADEE_ENGINE_OK='1')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.events.read_text().splitlines(),
                         ['engine', 'session --system-voice --speak=hello'])
        self.assertIn('System voice ready: paradee af_heart', result.stdout)
        self.assertNotIn('installed', self.events.read_text())

    def test_other_saved_engine_still_uses_piper(self):
        result = self.run_tts('--system-voice', '--speak=hello')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.events.read_text().splitlines()[:2], ['engine', 'installed'])
        self.assertIn('tool', self.events.read_text())

    def test_a_symlink_runtime_is_rejected(self):
        self.runtime.unlink()
        self.runtime.symlink_to(self.root / 'missing.py')
        result = self.run_tts('--install=paradee-8m-v1.2')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('symlink', result.stderr)
        self.assertEqual(self.events.read_text(), '')

    def test_a_missing_runtime_refuses_install_and_leaves_system_voice_to_piper(self):
        self.runtime.unlink()
        refused = self.run_tts('--install=paradee-8m-v1.2')
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('missing', refused.stderr)
        kept = self.run_tts('--system-voice', '--speak=hello')
        self.assertEqual(kept.returncode, 0, kept.stderr + kept.stdout)
        self.assertEqual(self.events.read_text().splitlines()[0], 'installed')
