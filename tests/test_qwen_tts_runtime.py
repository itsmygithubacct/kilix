"""Qwen audition environments are locked, optional release inputs."""
import os
from pathlib import Path
import subprocess
import tomllib
import unittest
from uuid import uuid4
import sys
import tempfile

# The suite runs both as `discover -s tests` (bare module names) and as
# `-m unittest tests.<module>` (package), so name this directory explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "runtimes" / "qwen-cpu"
INSTALLER = ROOT / "scripts" / "install-kilix-qwen-tts-cpu.sh"


class QwenCpuRuntimeTests(unittest.TestCase):
    def test_direct_model_session_bootstraps_runtime_and_preserves_arguments(self):
        host = (ROOT / "kilix").read_text()
        dispatch = host[host.index("  tts|kilix-tts)"):host.index("  stt|kilix-stt)")]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir()
            tool = root / "tool"
            tool.write_text('#!/bin/bash\nprintf "tool:%s\\n" "$*" >> "$EVENTS"\n'
                            '[[ " $* " != *" --check-session "* ]] || exit "${CHECK_STATUS:-0}"\n')
            tool.chmod(0o755)
            python = root / "python"
            python.write_text('#!/bin/bash\nprintf "run:%s\\n" "$*" >> "$EVENTS"\n')
            python.chmod(0o755)
            for backend in ("cpu", "gpu"):
                installer = scripts / f"install-kilix-qwen-tts-{backend}.sh"
                installer.write_text(f'#!/bin/bash\necho install:{backend} >> "$EVENTS"\necho "{python}"\n')
                installer.chmod(0o755)
            wrapper = root / "wrapper"
            wrapper.write_text('set -euo pipefail\n_kilix_voice_tool() { echo "$TOOL"; }\n'
                               'case "$1" in\n' + dispatch + '\nesac\n')
            events = root / "events"
            env = sandbox_env(KILIX_HOME=str(root), TOOL=str(tool), EVENTS=str(events))
            for model, extra in (("qwen3-tts-0.6b-customvoice", []),
                                 ("qwen3-tts-0.6b-base", ["--synthetic-reference"]),
                                 ("qwen3-tts-1.7b-voicedesign", ["--description", "A warm voice"])):
                for device in ("cpu", "cuda:0"):
                    with self.subTest(model=model, device=device):
                        events.write_text("")
                        args = ["tts", "--interactive", "--download-qwen", model, "--device", device, *extra]
                        result = subprocess.run(["bash", str(wrapper), *args], env=env, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        lines = events.read_text().splitlines()
                        self.assertIn("--check-session", lines[0])
                        self.assertEqual(lines[1], "install:" + ("cpu" if device == "cpu" else "gpu"))
                        self.assertIn(model, lines[2])
                        self.assertNotIn("--check-session", lines[2])
            events.write_text("")
            result = subprocess.run(["bash", str(wrapper), "tts", "--interactive",
                                     "--download-qwen=invalid"],
                                    env=dict(env, CHECK_STATUS="2"), capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("install:", events.read_text())
            events.write_text("")
            result = subprocess.run(["bash", str(wrapper), "tts", "--download-qwen", "all"],
                                    env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("install:", events.read_text())

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
        environment = sandbox_env(KILIX_DATA_HOME=str(root))
        result = subprocess.run([str(INSTALLER), "--print-path"], env=environment,
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         str(root / "voice/qwen-cpu/current/bin/python"))
        self.assertFalse(root.exists())


class QwenGpuRuntimeTests(unittest.TestCase):
    def test_lock_selects_cuda_pytorch_and_matching_flashattention_wheel(self):
        project = ROOT / "runtimes" / "qwen-gpu"
        config = tomllib.loads((project / "pyproject.toml").read_text())
        lock = tomllib.loads((project / "uv.lock").read_text())
        self.assertEqual(config["project"]["requires-python"], ">=3.13,<3.14")
        self.assertEqual(config["tool"]["uv"]["index"][0]["url"],
                         "https://download.pytorch.org/whl/cu124")
        self.assertTrue(config["tool"]["uv"]["index"][0]["explicit"])
        selected = {package["name"]: package for package in lock["package"]}
        for name, version in {"torch": "2.6.0+cu124", "torchaudio": "2.6.0+cu124",
                              "qwen-tts": "0.1.1", "transformers": "4.57.3",
                              "flash-attn": "2.7.4.post1"}.items():
            self.assertEqual(selected[name]["version"], version)
            self.assertTrue(selected[name].get("wheels"))
            self.assertTrue(all(wheel["hash"].startswith("sha256:")
                                for wheel in selected[name]["wheels"]))
        wheel = selected["flash-attn"]["wheels"][0]["url"]
        self.assertIn("cp313-cp313-linux_x86_64.whl", wheel)
        self.assertIn("cu12torch2.6cxx11abiFALSE", wheel)
        for name in ("torch", "torchaudio"):
            self.assertEqual(selected[name]["source"]["registry"],
                             "https://download.pytorch.org/whl/cu124")

    def test_print_path_is_read_only_and_data_root_scoped(self):
        installer = ROOT / "scripts" / "install-kilix-qwen-tts-gpu.sh"
        self.assertTrue(os.access(installer, os.X_OK))
        root = Path("/tmp") / f"kilix-qwen-gpu-print-only-{uuid4().hex}"
        self.assertFalse(root.exists())
        environment = sandbox_env(KILIX_DATA_HOME=str(root))
        result = subprocess.run([str(installer), "--print-path"], env=environment,
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         str(root / "voice/qwen-gpu/current/bin/python"))
        self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
