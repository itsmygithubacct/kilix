"""The optional Pocket CPU runtime is locked and read-only until selected."""
import os
from pathlib import Path
import subprocess
import tomllib
import unittest
from uuid import uuid4
import sys

# The suite runs both as `discover -s tests` (bare module names) and as
# `-m unittest tests.<module>` (package), so name this directory explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "runtimes" / "pocket-cpu"
INSTALLER = ROOT / "scripts" / "install-kilix-pocket-tts-cpu.sh"


class PocketCpuRuntimeTests(unittest.TestCase):
    def test_host_routes_explicit_pocket_session_through_lazy_runtime(self):
        host = (ROOT / "kilix").read_text(encoding="utf-8")
        self.assertIn("--download-pocket", host)
        self.assertIn("--tier=pocket-cpu", host)
        self.assertIn('row["tier"] == "pocket-cpu"', host)
        self.assertIn('rows[0]["verdict"] == "estimated-fit"', host)
        self.assertIn("install-kilix-pocket-tts-cpu.sh", host)
        self.assertIn("Pocket CPU audition needs an interactive terminal", host)

    def test_lock_selects_cpu_pytorch_and_pocket(self):
        project = tomllib.loads((PROJECT / "pyproject.toml").read_text())
        lock = tomllib.loads((PROJECT / "uv.lock").read_text())
        self.assertEqual(project["project"]["requires-python"], ">=3.13,<3.14")
        self.assertEqual(project["tool"]["uv"]["index"][0]["url"],
                         "https://download.pytorch.org/whl/cpu")
        self.assertTrue(project["tool"]["uv"]["index"][0]["explicit"])
        selected = {package["name"]: package for package in lock["package"]}
        for name, version in {"torch": "2.6.0+cpu", "torchaudio": "2.6.0+cpu",
                              "pocket-tts": "3.1.0"}.items():
            self.assertEqual(selected[name]["version"], version)
            self.assertTrue(selected[name]["wheels"])
            self.assertTrue(all(wheel["hash"].startswith("sha256:")
                                for wheel in selected[name]["wheels"]))

    def test_print_path_is_read_only(self):
        self.assertTrue(os.access(INSTALLER, os.X_OK))
        root = Path("/tmp") / f"kilix-pocket-print-only-{uuid4().hex}"
        self.assertFalse(root.exists())
        result = subprocess.run([str(INSTALLER), "--print-path"],
                                env=sandbox_env(KILIX_DATA_HOME=str(root)),
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         str(root / "voice/pocket-cpu/current/bin/python"))
        self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
