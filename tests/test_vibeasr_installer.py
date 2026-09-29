"""The VibeASR runtime installer pins one upstream commit and installs nothing on a query."""
import shutil
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

    def _published(self, root, ref_text):
        data = Path(root, "data")
        pin = subprocess.run([str(INSTALLER), "--print-ref"], env=sandbox_env(),
                             capture_output=True, text=True).stdout.strip().split("=")[1]
        generation = data / "voice" / "vibeasr" / "generations" / pin
        (generation / "bin").mkdir(parents=True)
        fake = generation / "bin" / "asr_infer"
        fake.write_text("#!/bin/sh\necho 'Error: --vae-model is required' >&2\nexit 1\n")
        fake.chmod(0o755)
        (generation / "REF").write_text(ref_text.replace("PIN", pin) + "\n")
        (data / "voice" / "vibeasr" / "current").symlink_to(generation)
        bare = Path(root, "bin")
        bare.mkdir()
        for tool in ("bash", "cat", "readlink", "mkdir", "flock", "id", "grep"):
            (bare / tool).symlink_to(shutil.which(tool))
        env = sandbox_env(KILIX_DATA_HOME=str(data),
                          GPU_TERMINAL_SOURCE_HOME=root + "/sources", PATH=str(bare))
        return subprocess.run(["bash", str(INSTALLER)], env=env,
                              capture_output=True, text=True)

    def test_a_generation_built_from_the_pin_is_current(self):
        with tempfile.TemporaryDirectory() as root:
            result = self._published(root, "vibeasr=PIN")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("runtime ready", result.stderr)

    def test_a_generation_without_the_pinned_ref_is_rebuilt(self):
        # Seat 2 (RC3): anything answering --vae-model used to count as current.
        with tempfile.TemporaryDirectory() as root:
            result = self._published(root, "vibeasr=0000000000000000000000000000000000000000")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("is required to build VibeASR", result.stderr)

    def test_unknown_arguments_are_refused(self):
        result = subprocess.run([str(INSTALLER), '--force'], env=sandbox_env(),
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('usage', result.stderr)


if __name__ == '__main__':
    unittest.main()
