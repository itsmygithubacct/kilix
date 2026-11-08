"""Launcher/provider joins; real model parity is tested by the sound provider."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-yamnet.sh"


class YamnetProvider(unittest.TestCase):
    def test_legacy_repair_delegates_and_relocation_refreshes_selection(self):
        with tempfile.TemporaryDirectory() as scratch:
            base = Path(scratch) / "data"
            runtime = base / "runtimes/yamnet"
            (runtime / "venv/bin").mkdir(parents=True)
            (runtime / "venv/bin/python").symlink_to("/missing-yamnet-test-interpreter")
            model = Path(scratch) / "model"
            model.write_bytes(b"fixture: assembler is stubbed, no inference")
            tools = base / "sources/kilix-modules/kilix-sound-detect/tools"
            tools.mkdir(parents=True)
            assembler = tools / "kilix-sound-runtime"
            assembler.write_text('''import argparse
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--destination');p.add_argument('--model');p.add_argument('--installer')
o=p.parse_args();root=Path(o.destination)
(root/'bin').mkdir(exist_ok=True)
(root/'lib').mkdir(exist_ok=True)
(root/'lib/kilix_sound_runtime.py').write_text('print("kilix.sound-runtime/content-v1")\\n')
(root/'runtime.json').write_text('{}')
wrapper=root/'bin/kilix-listen-classify'
wrapper.write_text('#!/bin/sh\\n[ "$1" = "--check-admission" ] && echo kilix.sound-runtime/content-v1\\n')
wrapper.chmod(0o755)
(root/'assembler-called').write_text(o.model)
''')
            (tools / "kilix_sound_runtime.py").write_text('print("kilix.sound-runtime/content-v1")\n')
            provider = tools.parent
            subprocess.run(["git", "-C", str(provider), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(provider), "add", "tools"], check=True)
            subprocess.run(["git", "-C", str(provider), "-c", "user.name=itsmygithubacct",
                            "-c", "user.email=itsmygithubacct@users.noreply.github.com",
                            "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], check=True)
            provider_ref = subprocess.check_output(["git", "-C", str(provider), "rev-parse", "HEAD"], text=True).strip()
            host = Path(scratch) / "host"
            host.mkdir()
            authority = host / "kilix"
            authority.write_text('#!/bin/sh\n[ "$*" = "models verify yamnet" ]\n')
            authority.chmod(0o755)
            def env(directory):
                return sandbox_env(GPU_TERMINAL_DATA_HOME=str(directory), KILIX_HOME=str(host),
                    KILIX_YAMNET_DIR=str(directory / "runtimes/yamnet"),
                    KILIX_USER_CONFIG_DIRECTORY=str(directory / "config"),
                    KILIX_YAMNET_MODEL=str(model), KILIX_PYTHON=sys.executable, KILIX_SOUND_REF=provider_ref)
            result = subprocess.run([str(INSTALLER), "--install", "--yes"], env=env(base),
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((runtime / "assembler-called").read_text(), str(model))
            shutil.rmtree(base / "sources")
            moved = Path(scratch) / "moved"
            base.rename(moved)
            result = subprocess.run([str(INSTALLER), "--print-path", "--yes"], env=env(moved),
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            selected = moved / "runtimes/yamnet/bin/kilix-listen-classify"
            self.assertEqual(result.stdout.strip(), str(selected))
            self.assertEqual((moved / "config/kilix.env").read_text(), f"KILIX_SOUND_CLASSIFIER={selected}\n")

    def test_run_never_installs_or_writes_selection(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            runtime = root / 'runtime'
            config = root / 'config'
            env = sandbox_env(KILIX_HOME=str(ROOT), KILIX_YAMNET_DIR=str(runtime),
                              KILIX_USER_CONFIG_DIRECTORY=str(config))
            def invoke():
                return subprocess.run([str(ROOT/'kilix'), 'yamnet', 'run'], env=env,
                                      input=b'fixture', capture_output=True, timeout=10)
            refused = invoke()
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(refused.stdout, b'')
            self.assertFalse(config.exists())
            (runtime/'bin').mkdir(parents=True)
            (runtime/'runtime.json').write_text('{}')
            wrapper = runtime/'bin/kilix-listen-classify'
            wrapper.write_text(f'#!{sys.executable}\nimport sys\n'
                               'if sys.argv[1:] != ["--check-runtime"]: sys.stdout.buffer.write(sys.stdin.buffer.read())\n')
            wrapper.chmod(0o700)
            good = invoke()
            # A self-checking legacy wrapper still lacks a Content receipt.
            self.assertNotEqual(good.returncode, 0)
            self.assertEqual(good.stdout, b'')
            self.assertFalse(config.exists())

    def test_policy_and_interpreter_keys_are_exported(self):
        text = (ROOT / "kilix").read_text()
        self.assertIn("|KILIX_SOUND_THRESHOLDS|KILIX_SOUND_PYTHON|", text)


if __name__ == "__main__":
    unittest.main()
