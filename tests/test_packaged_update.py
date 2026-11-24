"""`kilix update` and `kilix status` on a Kilix installed from Debian packages.

The package ships a PACKAGED marker in its tree. With it, and with no
checkout, `kilix update` hands over to scripts/packaged-update.sh, which runs
a fixed apt upgrade of the Kilix packages. Every test runs it dry: nothing
here calls apt or sudo.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/packaged-update.sh"

ROOT_PREFIX = "" if os.getuid() == 0 else "/usr/bin/sudo "
PLAN = [
    f"would run: {ROOT_PREFIX}/usr/bin/apt-get update",
    f"would run: {ROOT_PREFIX}/usr/bin/apt-get install --only-upgrade "
    "kilix kilix-engine kilix-native kilix-multiplexer",
]


def plan_lines(text):
    return [line for line in text.splitlines() if line.startswith("would run")]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        # The whole tree by reference; the launcher itself is a real copy so
        # that it takes this directory as its home.
        self.tree = self.root / "tree/kilix"
        self.tree.mkdir(parents=True)
        shutil.copy2(ROOT / "kilix", self.tree / "kilix")
        for entry in ROOT.iterdir():
            if entry.name not in ("kilix", ".git", "__pycache__", "PACKAGED"):
                (self.tree / entry.name).symlink_to(entry)
        self.engine = self.root / "engine"
        for name in ("kitty", "kitten"):
            path = self.engine / "bin" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)

    def env(self, **extra):
        return sandbox_env(
            HOME=str(self.home),
            KILIX_STORAGE_HOME=str(self.home / ".local/gpu_terminal/kilix"),
            KILIX_PACKAGED_ENGINE_HOME=str(self.engine), KILIX_PTY_BROKER="0",
            KILIX_PACKAGED_UPDATE_DRY_RUN="1", **extra)

    def kilix(self, *args, **extra):
        return subprocess.run([str(self.tree / "kilix"), *args], env=self.env(**extra),
                              capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL)

    def mark_packaged(self):
        (self.tree / "PACKAGED").write_text("deb\n")


class PackagedUpdateTests(Fixture):
    def test_a_packaged_install_upgrades_the_fixed_package_list_with_apt(self):
        self.mark_packaged()
        result = self.kilix("update")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(plan_lines(result.stdout), PLAN)

    def test_a_tree_without_the_marker_never_reaches_apt(self):
        result = self.kilix("update")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(plan_lines(result.stdout + result.stderr), [])
        self.assertIn("not a git checkout", result.stderr)

    def test_a_symlinked_marker_is_not_a_marker(self):
        (self.root / "elsewhere").write_text("deb\n")
        (self.tree / "PACKAGED").symlink_to(self.root / "elsewhere")
        result = self.kilix("update")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(plan_lines(result.stdout + result.stderr), [])

    def test_a_checkout_with_the_marker_still_updates_as_a_checkout(self):
        self.mark_packaged()
        (self.tree / ".git").mkdir()
        result = self.kilix("update")
        self.assertEqual(plan_lines(result.stdout + result.stderr), [])

    def test_an_unknown_argument_is_refused_before_anything_runs(self):
        self.mark_packaged()
        result = self.kilix("update", "kilix-engine=1.0")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(plan_lines(result.stdout + result.stderr), [])

    def test_the_environment_cannot_change_the_commands(self):
        self.mark_packaged()
        result = self.kilix("update", APT="/bin/echo", SUDO="/bin/echo",
                            PACKAGES="something-else", as_root="/bin/echo")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(plan_lines(result.stdout), PLAN)

    def test_the_script_takes_no_arguments(self):
        result = subprocess.run(["sh", str(SCRIPT), "extra-package"], env=self.env(),
                                capture_output=True, text=True, timeout=60,
                                stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(plan_lines(result.stdout + result.stderr), [])

    def test_the_script_names_only_absolute_commands(self):
        text = SCRIPT.read_text()
        self.assertIn("APT=/usr/bin/apt-get\n", text)
        self.assertIn("SUDO=/usr/bin/sudo\n", text)
        self.assertIn('PACKAGES="kilix kilix-engine kilix-native kilix-multiplexer"\n', text)


class PackagedStatusTests(Fixture):
    def test_status_reports_a_package_not_a_modified_checkout(self):
        self.mark_packaged()
        result = self.kilix("status")
        first = result.stdout.splitlines()[0] if result.stdout else ""
        self.assertIn("(package, ", first, result.stderr[-2000:])
        self.assertNotIn("not-a-checkout", first)
        self.assertNotIn("modified", first)

    def test_status_of_a_plain_tree_is_unchanged(self):
        result = self.kilix("status")
        first = result.stdout.splitlines()[0] if result.stdout else ""
        self.assertIn("(not-a-checkout, ", first, result.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
