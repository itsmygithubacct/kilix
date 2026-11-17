"""The launcher finds the engine the kilix-engine Debian package installs.

Order: a fork built in the storage root, then the packaged engine, then the
downloaded prebuilt bundle. With a packaged engine present nothing is built
or downloaded.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
KILIX = ROOT / "kilix"


def engine(directory, label):
    """A stand-in engine: kitty and kitten that name themselves."""
    directory.mkdir(parents=True)
    for name in ("kitty", "kitten"):
        path = directory / name
        path.write_text(f"#!/bin/sh\necho '{name} {label}' \"$@\"\n")
        path.chmod(0o755)
    return directory / "kitty"


class PackagedEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.storage = self.home / ".local/gpu_terminal/kilix"
        self.packaged_home = self.root / "usr-lib-kilix-engine"

    def run_kilix(self, *args):
        env = sandbox_env(
            HOME=str(self.home),
            KILIX_STORAGE_HOME=str(self.storage),
            KILIX_PACKAGED_ENGINE_HOME=str(self.packaged_home),
        )
        return subprocess.run([str(KILIX), *args], env=env, capture_output=True,
                              text=True, timeout=120, stdin=subprocess.DEVNULL)

    def fork(self):
        return engine(self.storage / "build/current/src/kitty/launcher", "fork")

    def packaged(self):
        return engine(self.packaged_home / "bin", "packaged")

    def prebuilt(self):
        return engine(self.storage / "prebuilt/kitty.app/bin", "prebuilt")

    def test_the_packaged_engine_is_selected_when_nothing_is_built(self):
        packaged = self.packaged()
        result = self.run_kilix("--which")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         [str(packaged), "kitty packaged --version"])
        self.assertFalse((self.storage / "prebuilt").exists(), "nothing downloaded")
        self.assertFalse((self.storage / "build/current").exists(), "nothing built")

    def test_a_built_fork_is_selected_over_the_packaged_engine(self):
        fork = self.fork()
        self.packaged()
        result = self.run_kilix("--which")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], str(fork))

    def test_the_packaged_engine_is_selected_over_the_prebuilt_bundle(self):
        packaged = self.packaged()
        self.prebuilt()
        result = self.run_kilix("--which")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], str(packaged))

    def test_the_prebuilt_bundle_still_serves_without_a_package(self):
        prebuilt = self.prebuilt()
        result = self.run_kilix("--which")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], str(prebuilt))

    def test_no_engine_anywhere_is_still_reported(self):
        result = self.run_kilix("--which")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no kitty built yet", result.stderr)

    def test_engine_help_comes_from_the_packaged_engine(self):
        self.packaged()
        result = self.run_kilix("--help-engine")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "kitty packaged --help")

    def test_status_names_the_packaged_engine(self):
        packaged = self.packaged()
        result = self.run_kilix("status")
        self.assertIn(str(packaged), result.stdout + result.stderr)

    def test_engine_subcommands_run_the_packaged_engine(self):
        self.packaged()
        result = self.run_kilix("+kitten", "icat", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "kitty packaged +kitten icat --help")

    def test_engine_subcommands_prefer_a_built_fork(self):
        self.fork()
        self.packaged()
        result = self.run_kilix("+kitten", "icat", "--help")
        self.assertEqual(result.stdout.strip(), "kitty fork +kitten icat --help")

    def test_the_default_location_is_where_the_package_installs(self):
        text = KILIX.read_text()
        self.assertEqual(text.count(
            'KILIX_PACKAGED_ENGINE_HOME="${KILIX_PACKAGED_ENGINE_HOME:-/usr/lib/kilix/engine}"'), 1)
        self.assertEqual(text.count('PACKAGED="$KILIX_PACKAGED_ENGINE_HOME/bin/kitty"'), 1)


if __name__ == "__main__":
    unittest.main()
