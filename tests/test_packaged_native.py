"""The native helpers the kilix-native Debian package installs.

A tree that carries a helper's source builds its own pin, as before. A tree
without the source (a packaged Kilix) is served by the package: the pty
broker, the state library and the screensavers.
"""
import importlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

BROKER_STUB = """all:
\tprintf '#!/bin/sh\\necho built\\n' > "$(BUILD_DIR)/kitty-pty-broker"
\tchmod 0755 "$(BUILD_DIR)/kitty-pty-broker"
\t: > "$(BUILD_DIR)/libkitty-pty-broker.so"
.PHONY: all
"""


def executable(path, text="#!/bin/sh\necho packaged \"$@\"\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.storage = self.home / ".local/gpu_terminal/kilix"
        self.native = self.root / "usr-lib-kilix-native"
        # A Kilix tree with the scripts and no helper source: what a package ships.
        self.tree = self.root / "tree/kilix"
        (self.tree / "scripts").mkdir(parents=True)
        for name in ("build-pty-broker.sh", "build-state-library.sh"):
            shutil.copy2(ROOT / "scripts" / name, self.tree / "scripts" / name)

    def env(self, **extra):
        return sandbox_env(HOME=str(self.home), KILIX_STORAGE_HOME=str(self.storage),
                           KILIX_PACKAGED_NATIVE_HOME=str(self.native), **extra)

    def run_script(self, name, *args, tree=None):
        return subprocess.run(
            ["bash", str((tree or self.tree) / "scripts" / name), *args],
            env=self.env(), capture_output=True, text=True, timeout=300,
            stdin=subprocess.DEVNULL)

    def packaged_broker(self):
        executable(self.native / "kitty-pty-broker")
        (self.native / "libkitty-pty-broker.so").write_bytes(b"\x7fELF")
        return self.native / "kitty-pty-broker"


class PackagedBrokerTests(Fixture):
    def test_a_tree_without_source_is_served_by_the_package(self):
        broker = self.packaged_broker()
        result = self.run_script("build-pty-broker.sh", "--print-path")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"{broker}\n")
        self.assertFalse(self.storage.exists(), "nothing was built or created")

    def test_without_print_path_nothing_is_printed(self):
        self.packaged_broker()
        result = self.run_script("build-pty-broker.sh")
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_without_source_or_package_the_failure_is_unchanged(self):
        result = self.run_script("build-pty-broker.sh", "--print-path")
        self.assertEqual(result.returncode, 1)
        self.assertIn("source not found", result.stderr)

    def test_a_package_without_its_library_is_not_used(self):
        executable(self.native / "kitty-pty-broker")
        result = self.run_script("build-pty-broker.sh", "--print-path")
        self.assertEqual(result.returncode, 1)
        self.assertIn("source not found", result.stderr)

    def test_a_linked_packaged_broker_is_not_used(self):
        target = executable(self.root / "elsewhere/broker")
        self.native.mkdir()
        (self.native / "kitty-pty-broker").symlink_to(target)
        (self.native / "libkitty-pty-broker.so").write_bytes(b"\x7fELF")
        result = self.run_script("build-pty-broker.sh", "--print-path")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_a_tree_with_source_builds_its_own_and_ignores_the_package(self):
        self.packaged_broker()
        source = self.tree / "third_party/kitty-pty-broker"
        (source / "include").mkdir(parents=True)
        (source / "include/kitty_pty_broker.h").write_text("")
        (source / "Makefile").write_text(BROKER_STUB)
        result = self.run_script("build-pty-broker.sh", "--print-path")
        self.assertEqual(result.returncode, 0, result.stderr)
        built = self.storage / "build/libraries/kitty-pty-broker/kitty-pty-broker"
        self.assertEqual(result.stdout, f"{built}\n")
        self.assertTrue(built.is_file())


class PackagedStateLibraryTests(Fixture):
    def packaged_library(self):
        self.native.mkdir(exist_ok=True)
        library = self.native / "libkilix-state.so"
        library.write_bytes(b"\x7fELF")
        return library

    def test_a_tree_without_source_is_served_by_the_package(self):
        library = self.packaged_library()
        result = self.run_script("build-state-library.sh", "--print-path")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"{library}\n")
        self.assertFalse(self.storage.exists(), "nothing was built or created")

    def test_without_source_or_package_the_failure_is_unchanged(self):
        result = self.run_script("build-state-library.sh", "--print-path")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing pinned native source", result.stderr)

    def test_a_linked_packaged_library_is_not_used(self):
        self.native.mkdir()
        (self.root / "other.so").write_bytes(b"\x7fELF")
        (self.native / "libkilix-state.so").symlink_to(self.root / "other.so")
        result = self.run_script("build-state-library.sh", "--print-path")
        self.assertEqual(result.returncode, 1)

    @unittest.skipUnless((ROOT / "third_party/kilix-state/Makefile").is_file(),
                         "the pinned state source is not checked out")
    def test_a_tree_with_source_builds_its_own_and_ignores_the_package(self):
        self.packaged_library()
        result = self.run_script("build-state-library.sh", "--print-path", tree=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            f"{self.storage / 'build/libraries/kilix-state/libkilix-state.so'}\n")

    def test_the_launcher_takes_the_library_path_from_the_script(self):
        launcher = (ROOT / "kilix").read_text()
        self.assertEqual(launcher.count(
            'KILIX_STATE_LIBRARY="$("$KILIX_HOME/scripts/build-state-library.sh" --print-path)"'), 1)
        self.assertNotIn(
            'KILIX_STATE_LIBRARY="$KILIX_BUILD_DIRECTORY/libraries/kilix-state/libkilix-state.so"',
            launcher)

    def sdk_path(self, **env):
        sys.path.insert(0, str(ROOT / "config"))
        self.addCleanup(sys.path.remove, str(ROOT / "config"))
        state = importlib.import_module("kilix_sdk.state")
        values = {"KILIX_STORAGE_HOME": str(self.storage),
                  "KILIX_PACKAGED_NATIVE_HOME": str(self.native), **env}
        with mock.patch.dict(os.environ, sandbox_env(**values), clear=True):
            return state.native_library_path()

    def test_the_sdk_uses_the_package_when_nothing_is_built(self):
        library = self.packaged_library()
        self.assertEqual(self.sdk_path(), library)

    def test_the_sdk_prefers_a_host_build(self):
        self.packaged_library()
        built = self.storage / "build/libraries/kilix-state/libkilix-state.so"
        built.parent.mkdir(parents=True)
        built.write_bytes(b"\x7fELF")
        self.assertEqual(self.sdk_path(), built)

    def test_the_sdk_keeps_the_build_path_when_there_is_no_package(self):
        self.assertEqual(
            self.sdk_path(),
            self.storage / "build/libraries/kilix-state/libkilix-state.so")

    def test_an_explicit_library_still_wins(self):
        self.packaged_library()
        chosen = self.root / "chosen.so"
        self.assertEqual(self.sdk_path(KILIX_STATE_LIBRARY=str(chosen)), chosen)


class PackagedScreensaverTests(Fixture):
    """The launcher, run from a tree without config/screensavers."""

    def setUp(self):
        super().setUp()
        # The whole tree by reference, minus the screensaver source.
        shutil.copy2(ROOT / "kilix", self.tree / "kilix")
        shutil.rmtree(self.tree / "scripts")
        for entry in ROOT.iterdir():
            if entry.name not in ("kilix", "config", ".git", "__pycache__"):
                (self.tree / entry.name).symlink_to(entry)
        (self.tree / "config").mkdir()
        for entry in (ROOT / "config").iterdir():
            if entry.name != "screensavers":
                (self.tree / "config" / entry.name).symlink_to(entry)
        # The engine only has to exist: `screensaver` execs before using it.
        self.engine = self.root / "engine"
        for name in ("kitty", "kitten"):
            executable(self.engine / "bin" / name, "#!/bin/sh\nexit 0\n")

    def run_kilix(self, *args):
        env = self.env(KILIX_PACKAGED_ENGINE_HOME=str(self.engine), KILIX_PTY_BROKER="0",
                       KILIX_IN_OVERLAY="1")
        return subprocess.run([str(self.tree / "kilix"), *args], env=env,
                              capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)

    def test_a_packaged_screensaver_runs_without_a_compiler(self):
        executable(self.native / "screensavers/matrix")
        result = self.run_kilix("screensaver", "matrix", "--once")
        self.assertEqual(result.stdout.strip().splitlines()[-1:], ["packaged --once"],
                         result.stderr[-2000:])

    def test_an_unknown_screensaver_lists_the_packaged_ones(self):
        executable(self.native / "screensavers/matrix")
        result = self.run_kilix("screensaver", "nosuch")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no screensaver 'nosuch'", result.stderr)
        self.assertIn("  matrix", result.stderr)

    def test_a_name_with_a_path_never_leaves_the_package_directory(self):
        executable(self.native / "outside")
        executable(self.native / "screensavers/matrix")
        result = self.run_kilix("screensaver", "../outside")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no screensaver '../outside'", result.stderr)
        self.assertNotIn("packaged", result.stdout)


if __name__ == "__main__":
    unittest.main()
