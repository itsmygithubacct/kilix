"""Offline smoke test for the pinned, inference-free Qwen client generation."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-kilix-qwen-client.sh"
REF = "3f1031263b4369761d77a90fec8e480fcb87f3d0"


class QwenClientInstallerTests(unittest.TestCase):
    def test_read_only_pin_queries(self):
        ref = subprocess.check_output([INSTALLER, "--print-ref"], text=True).strip()
        self.assertEqual(ref, f"kilix-qwen-tts={REF}")
        path = subprocess.check_output([INSTALLER, "--print-path"], text=True).strip()
        self.assertTrue(path.endswith("/voice/qwen-client/current/bin/kilix-qwen-tts"))

    def test_preseeded_exact_source_installs_without_network(self):
        source = os.environ.get("KILIX_QWEN_TEST_SOURCE")
        if not source:
            self.skipTest("set KILIX_QWEN_TEST_SOURCE to an exact local Qwen checkout")
        if subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"],
                                   text=True).strip() != REF:
            self.skipTest("local Qwen checkout is not the pinned client commit")
        with tempfile.TemporaryDirectory(prefix="kilix-qwen-client-test-") as scratch:
            root = Path(scratch)
            sources = root / "sources"
            sources.mkdir()
            clone = sources / f".kilix-qwen-tts-{REF}"
            subprocess.run(["git", "clone", "-q", "--no-checkout", source, clone], check=True)
            subprocess.run(["git", "-C", clone, "checkout", "-q", "--detach", REF], check=True)
            environment = dict(os.environ, KILIX_HOME=str(ROOT),
                               GPU_TERMINAL_SOURCE_HOME=str(sources),
                               KILIX_DATA_HOME=str(root / "data"))
            command = [INSTALLER]
            first = subprocess.check_output(command, env=environment, text=True).strip()
            second = subprocess.check_output(command, env=environment, text=True).strip()
            self.assertEqual(first, second)
            self.assertEqual(Path(first).resolve().parent.parent.name, REF)
            subprocess.run([first, "--help"], check=True, stdout=subprocess.DEVNULL)

    def environment(self, root):
        sources = root / "sources"
        sources.mkdir(exist_ok=True)
        return sources, dict(os.environ, KILIX_HOME=str(ROOT), GPU_TERMINAL_SOURCE_HOME=str(sources),
                             KILIX_DATA_HOME=str(root / "data"))

    def test_current_link_outside_managed_generations_refuses(self):
        with tempfile.TemporaryDirectory(prefix="kilix-qwen-client-test-") as scratch:
            root = Path(scratch)
            _sources, environment = self.environment(root)
            managed = root / "data/voice/qwen-client"
            (managed / "generations").mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            for target in (outside, managed / "generations" / ".." / ("a" * 38),
                           managed / "generations" / "not-a-commit"):
                with self.subTest(target=str(target)):
                    current = managed / "current"
                    if current.is_symlink():
                        current.unlink()
                    current.symlink_to(target)
                    result = subprocess.run([INSTALLER], env=environment, text=True,
                                            capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("outside its generations", result.stderr)
                    self.assertEqual(os.readlink(current), str(target))

    def test_upgrade_from_previous_generation_moves_current(self):
        source = os.environ.get("KILIX_QWEN_TEST_SOURCE")
        if not source:
            self.skipTest("set KILIX_QWEN_TEST_SOURCE to an exact local Qwen checkout")
        if subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip() != REF:
            self.skipTest("local Qwen checkout is not the pinned client commit")
        with tempfile.TemporaryDirectory(prefix="kilix-qwen-client-test-") as scratch:
            root = Path(scratch)
            sources, environment = self.environment(root)
            clone = sources / f".kilix-qwen-tts-{REF}"
            subprocess.run(["git", "clone", "-q", "--no-checkout", source, clone], check=True)
            subprocess.run(["git", "-C", clone, "checkout", "-q", "--detach", REF], check=True)
            previous = root / "data/voice/qwen-client/generations" / ("e" * 40)
            (previous / "bin").mkdir(parents=True)
            (root / "data/voice/qwen-client/current").symlink_to(previous)
            installed = subprocess.check_output([INSTALLER], env=environment, text=True).strip()
            self.assertEqual(Path(installed).resolve().parent.parent.name, REF)
            self.assertTrue(previous.is_dir(), "the previous generation is left for cleanup, not deleted")


if __name__ == "__main__":
    unittest.main()
