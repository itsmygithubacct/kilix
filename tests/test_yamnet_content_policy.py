"""The sound installer uses the catalog asset and never invents agreement."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-yamnet.sh"


class SoundContentPolicy(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.host = self.root / "host"
        self.host.mkdir()
        self.data = self.root / "content"
        self.model = self.data / "assets/yamnet/yamnet.tflite"
        self.model.parent.mkdir(parents=True)
        self.model.write_bytes(b"synthetic model, assembler is stubbed")
        self.gate = self.root / "policy-covered"
        authority = self.host / "kilix"
        authority.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
if sys.argv[1:] == ['models','verify','yamnet']:
    sys.exit(0 if pathlib.Path(os.environ['TEST_POLICY_GATE']).is_file() else 1)
if sys.argv[1:] == ['models','show','yamnet']:
    print(json.dumps({'root': os.environ['TEST_CONTENT_ROOT']})); sys.exit(0)
sys.exit(3)
''')
        authority.chmod(0o755)
        provider = self.root / "data/sources/kilix-modules/kilix-sound-detect/tools"
        provider.mkdir(parents=True)
        assembler = provider / "kilix-sound-runtime"
        assembler.write_text('''import argparse
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--destination');p.add_argument('--model');p.add_argument('--installer')
args=p.parse_args();root=Path(args.destination);(root/'bin').mkdir(parents=True,exist_ok=True)
(root/'lib').mkdir(exist_ok=True);(root/'lib/kilix_sound_runtime.py').write_text('print("kilix.sound-runtime/content-v1")\\n')
(root/'runtime.json').write_text('{}');(root/'source-model').write_text(args.model)
w=root/'bin/kilix-listen-classify'
w.write_text('#!/bin/sh\\n"$KILIX_HOME/kilix" models verify yamnet >/dev/null || exit 1\\n'
             '[ "$1" = "--check-admission" ] && { echo kilix.sound-runtime/content-v1; exit; }\\ncat\\n')
w.chmod(0o755)
''')
        (provider / "kilix_sound_runtime.py").write_text('print("kilix.sound-runtime/content-v1")\n')
        self.provider = provider.parent
        subprocess.run(["git", "-C", str(self.provider), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.provider), "add", "tools"], check=True)
        subprocess.run(["git", "-C", str(self.provider), "-c", "user.name=itsmygithubacct",
                        "-c", "user.email=itsmygithubacct@users.noreply.github.com",
                        "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], check=True)
        self.provider_ref = subprocess.check_output(["git", "-C", str(self.provider), "rev-parse", "HEAD"], text=True).strip()
        self.runtime = self.root / "runtime"
        self.config = self.root / "config"
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("KILIX", "GPU_TERMINAL", "KITTY", "PLEB"))}
        self.env.update(KILIX_HOME=str(self.host), GPU_TERMINAL_DATA_HOME=str(self.root / "data"),
                        KILIX_YAMNET_DIR=str(self.runtime), KILIX_USER_CONFIG_DIRECTORY=str(self.config),
                        KILIX_PYTHON=sys.executable, KILIX_SOUND_REF=self.provider_ref, TEST_POLICY_GATE=str(self.gate),
                        TEST_CONTENT_ROOT=str(self.data))

    def run_installer(self, *args, input=None):
        return subprocess.run([str(INSTALLER), *args], env=self.env, input=input,
                              capture_output=True, timeout=20)

    def test_without_receipt_yes_does_not_fetch_create_or_select(self):
        result = self.run_installer("--install", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"licence receipt", result.stderr)
        self.assertFalse(self.runtime.exists())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.gate.exists())

    def test_verified_default_asset_is_used_and_revocation_stops_run(self):
        self.gate.touch()
        result = self.run_installer("--install", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.runtime / "source-model").read_text(), str(self.model))
        selection = (self.config / "kilix.env").read_bytes()
        running = self.run_installer("--run", input=b"fixture")
        self.assertEqual((running.returncode, running.stdout), (0, b"fixture"))
        self.gate.unlink()
        denied = self.run_installer("--run", input=b"fixture")
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(denied.stdout, b"")
        self.assertEqual((self.config / "kilix.env").read_bytes(), selection)

    def test_catalog_installed_nvr_provides_the_exact_pinned_sound_source(self):
        self.gate.touch()
        installed = self.data / "kilix-nvr/third_party/kilix-sound-detect"
        installed.parent.mkdir(parents=True)
        self.provider.rename(installed)
        result = self.run_installer("--install", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.runtime / "source-model").read_text(), str(self.model))

    def test_dirty_provider_does_not_replace_an_existing_runtime(self):
        self.gate.touch()
        (self.provider / "tools/kilix-sound-runtime").write_text("modified\n")
        (self.runtime / "bin").mkdir(parents=True)
        manifest = self.runtime / "runtime.json"
        manifest.write_text("previous")
        wrapper = self.runtime / "bin/kilix-listen-classify"
        wrapper.write_text("#!/bin/sh\nexit 0\n")
        wrapper.chmod(0o755)
        result = self.run_installer("--install", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(manifest.read_text(), "previous")
        self.assertFalse(self.config.exists())

    def test_old_uncovered_wrapper_is_not_treated_as_upgraded(self):
        self.gate.touch()
        (self.runtime / "bin").mkdir(parents=True)
        (self.runtime / "runtime.json").write_text("{}")
        wrapper = self.runtime / "bin/kilix-listen-classify"
        wrapper.write_text("#!/bin/sh\nexit 0\n")
        wrapper.chmod(0o755)
        result = self.run_installer("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.config.exists())


if __name__ == "__main__":
    unittest.main()
