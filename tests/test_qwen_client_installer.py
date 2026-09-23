"""Offline smoke test for the pinned, inference-free Qwen client generation."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-kilix-qwen-client.sh"
REF = "64a377cbab729e58f2877817bc0708b79e12a899"


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


if __name__ == "__main__":
    unittest.main()
