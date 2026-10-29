"""The optional claudex dispatch preserves arguments and child exit status."""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLIENT = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["CLAUDEX_TEST_LOG"], "w") as stream:
    json.dump({"executable": sys.argv[0], "arguments": sys.argv[1:]}, stream)
raise SystemExit(int(os.environ.get("CLAUDEX_TEST_STATUS", "0")))
"""

RESOLVER = """import json, os, sys
assert sys.argv[1:] == ['install', 'kilix-claudex'], sys.argv
assert os.environ['KILIX_APP_AUTO_INSTALL'] == '0'
target = os.environ.get('CLAUDEX_TEST_CATALOG_BIN')
if not target:
    raise SystemExit(1)
print(target)
"""


class ClaudexLauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        checkout = self.root / "checkout"
        (checkout / "config").mkdir(parents=True)
        for name in ("kilix", "kilix-settings"):
            shutil.copy2(ROOT / name, checkout / name)
        for name in ("kitty.conf", "kilix.env"):
            shutil.copy2(ROOT / "config" / name, checkout / "config" / name)
        shutil.copytree(ROOT / "config/kilix_sdk", checkout / "config/kilix_sdk")
        (checkout / "config/content_app.py").write_text(RESOLVER)
        self.launcher = checkout / "kilix"
        self.home = self.root / "home with spaces"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.json"
        self.env = sandbox_env(
            HOME=str(self.home), PATH=f"{self.bin}:/usr/bin:/bin",
            KILIX_STORAGE_HOME=str(self.root / "storage"),
            KILIX_VOICE_PREFIX=str(self.root / "voice-prefix"),
            XDG_CONFIG_HOME=str(self.home / ".config"),
            XDG_CACHE_HOME=str(self.home / ".cache"),
            XDG_DATA_HOME=str(self.home / ".local/share"),
            CLAUDEX_TEST_LOG=str(self.log),
        )

    def install_client(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CLIENT)
        path.chmod(0o755)
        return path

    def launch(self, *arguments, **env):
        return subprocess.run(
            [str(self.launcher), "claudex", *arguments],
            env={**self.env, **env}, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30,
        )

    def test_path_client_preserves_arguments_and_exit_status(self):
        client = self.install_client(self.bin / "claudex")
        self.install_client(self.home / "gpu_terminal/kilix-apps/kilix-claudex/claudex")
        arguments = ["route", "worker", "low", "--cwd", "/tmp/project with spaces",
                     "--", "literal $HOME; *", ""]
        result = self.launch(*arguments, CLAUDEX_TEST_STATUS="23")
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(self.log.read_text()),
                         {"executable": str(client), "arguments": arguments})

    def test_checkout_fallback_precedes_local_bin(self):
        client = self.install_client(self.home / "gpu_terminal/kilix-apps/kilix-claudex/claudex")
        self.install_client(self.home / ".local/bin/claudex")
        result = self.launch("usage", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.log.read_text()),
                         {"executable": str(client), "arguments": ["usage", "--json"]})

    def test_verified_catalog_copy_precedes_development_fallback(self):
        client = self.install_client(self.root / 'catalog with spaces/claudex')
        self.install_client(self.home / 'gpu_terminal/kilix-apps/kilix-claudex/claudex')
        arguments = ['team', 'plan', '/tmp/team with spaces.json', '--json']
        result = self.launch(*arguments, CLAUDEX_TEST_CATALOG_BIN=str(client),
                             CLAUDEX_TEST_STATUS='23')
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(self.log.read_text()),
                         {'executable': str(client), 'arguments': arguments})

    def test_help_works_without_an_installed_application(self):
        result = self.launch('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('install kilix-claudex', result.stdout)
        self.assertIn('team plan|launch', result.stdout)
        self.assertFalse(self.log.exists())

    def test_local_bin_fallback(self):
        client = self.install_client(self.home / ".local/bin/claudex")
        result = self.launch("route", "max", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.log.read_text())["executable"], str(client))

    def test_help_does_not_launch_client(self):
        self.install_client(self.bin / "claudex")
        for arguments in ((), ("help",), ("--help",), ("-h",)):
            with self.subTest(arguments=arguments):
                result = self.launch(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("claudex route NAME", result.stdout)
                self.assertFalse(self.log.exists())

    def test_missing_client_fails_without_starting_a_session(self):
        result = self.launch("route", "max")
        self.assertEqual(result.returncode, 1)
        self.assertIn("claudex is not installed", result.stderr)
        self.assertIn("kilix install kilix-claudex", result.stderr)
        self.assertFalse(self.log.exists())

    def test_nonexecutable_client_is_not_run(self):
        client = self.install_client(self.home / ".local/bin/claudex")
        client.chmod(0o644)
        result = self.launch("usage")
        self.assertEqual(result.returncode, 1)
        self.assertIn("claudex is not installed", result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
