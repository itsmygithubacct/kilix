"""The VibeASR runtime installer pins one upstream commit and installs nothing on a query."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'scripts/install-kilix-vibeasr.sh'


class VibeAsrInstallerTests(unittest.TestCase):
    def test_queries_report_the_pin_and_path_without_installing(self):
        with tempfile.TemporaryDirectory() as root:
            env = sandbox_env(KILIX_DATA_HOME=root + '/data',
                              GPU_TERMINAL_SOURCE_HOME=root + '/sources')
            ref = subprocess.run([str(INSTALLER), '--print-ref'], env=env,
                                 capture_output=True, text=True)
            self.assertEqual(ref.returncode, 0, ref.stderr)
            self.assertRegex(ref.stdout, r'^vibeasr=[0-9a-f]{40}\n$')
            path = subprocess.run([str(INSTALLER), '--print-path'], env=env,
                                  capture_output=True, text=True)
            self.assertEqual(path.returncode, 0, path.stderr)
            # The exact path kilix-voice resolves for its VibeVoice runtime.
            self.assertEqual(path.stdout.strip(),
                             root + '/data/voice/vibeasr/current/bin/asr_infer')
            self.assertFalse(Path(root, 'data').exists())
            self.assertFalse(Path(root, 'sources').exists())

    def test_the_voice_verb_dispatches_to_the_installer(self):
        with tempfile.TemporaryDirectory() as root:
            env = sandbox_env(KILIX_HOME=str(ROOT), KILIX_DATA_HOME=root + '/g/kilix/data',
                              GPU_TERMINAL_HOME=root + '/g')
            result = subprocess.run([str(ROOT / 'kilix'), 'voice', 'vibeasr', '--print-ref'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertRegex(result.stdout, r'^vibeasr=[0-9a-f]{40}\n$')

    def test_unknown_arguments_are_refused(self):
        result = subprocess.run([str(INSTALLER), '--force'], env=sandbox_env(),
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('usage', result.stderr)


if __name__ == '__main__':
    unittest.main()
