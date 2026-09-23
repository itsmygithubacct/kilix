"""The CPU audition environment is a locked, optional release input."""
import os
from pathlib import Path
import subprocess
import tomllib
import unittest
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "runtimes" / "qwen-cpu"
INSTALLER = ROOT / "scripts" / "install-kilix-qwen-tts-cpu.sh"


class QwenCpuRuntimeTests(unittest.TestCase):
    def test_lock_selects_cpu_pytorch_and_tested_qwen_versions(self):
        project = tomllib.loads((PROJECT / "pyproject.toml").read_text())
        lock = tomllib.loads((PROJECT / "uv.lock").read_text())
        self.assertEqual(project["project"]["requires-python"], ">=3.13,<3.14")
        self.assertTrue(project["tool"]["uv"]["index"][0]["explicit"])
        self.assertEqual(project["tool"]["uv"]["index"][0]["url"],
                         "https://download.pytorch.org/whl/cpu")
        selected = {package["name"]: package for package in lock["package"]}
        for name, version in {"torch": "2.6.0+cpu", "torchaudio": "2.6.0+cpu",
                              "qwen-tts": "0.1.1", "transformers": "4.57.3"}.items():
            self.assertEqual(selected[name]["version"], version)
            self.assertTrue(selected[name].get("wheels"))
            self.assertTrue(all(wheel["hash"].startswith("sha256:")
                                for wheel in selected[name]["wheels"]))
        for name in ("torch", "torchaudio"):
            self.assertEqual(selected[name]["source"]["registry"],
                             "https://download.pytorch.org/whl/cpu")

    def test_print_path_is_read_only_and_data_root_scoped(self):
        self.assertTrue(os.access(INSTALLER, os.X_OK))
        root = Path("/tmp") / f"kilix-qwen-print-only-{uuid4().hex}"
        self.assertFalse(root.exists())
        environment = dict(os.environ, KILIX_DATA_HOME=str(root))
        result = subprocess.run([str(INSTALLER), "--print-path"], env=environment,
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         str(root / "voice/qwen-cpu/current/bin/python"))
        self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
