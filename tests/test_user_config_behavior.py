import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UserConfigBehaviorTests(unittest.TestCase):
    def test_screen_size_writes_xdg_override_not_tracked_default(self):
        tracked = ROOT / "config" / "kitty.conf"
        before = digest(tracked)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = dict(os.environ)
            env.pop("KILIX_CONFIG_DIRECTORY", None)
            env.pop("KITTY_CONFIG_DIRECTORY", None)
            env.pop("KILIX_ENV_CONFIG", None)
            env.update({"HOME": str(root / "home"),
                        "XDG_CONFIG_HOME": str(root / "xdg")})
            result = subprocess.run(
                [str(ROOT / "kilix"), "screen-size", "set", "14"],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            user = root / "xdg" / "kilix" / "kitty.conf"
            text = user.read_text()
            self.assertIn("include .kilix-defaults.conf", text)
            self.assertIn("font_size", text)
            self.assertIn("14", text)
            self.assertTrue((root / "xdg" / "kilix" / "kilix.env").exists())
            defaults = root / "xdg" / "kilix" / ".kilix-defaults.conf"
            self.assertEqual(defaults.resolve(), tracked)
        self.assertEqual(digest(tracked), before)

    def test_managed_links_follow_a_moved_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            (first / "config").mkdir(parents=True)
            shutil.copy2(ROOT / "kilix", first / "kilix")
            shutil.copy2(ROOT / "config" / "kitty.conf",
                         first / "config" / "kitty.conf")
            shutil.copy2(ROOT / "config" / "kilix.env",
                         first / "config" / "kilix.env")
            env = dict(os.environ)
            env.pop("KILIX_CONFIG_DIRECTORY", None)
            env.pop("KITTY_CONFIG_DIRECTORY", None)
            env.pop("KILIX_ENV_CONFIG", None)
            env.update({"HOME": str(root / "home"),
                        "XDG_CONFIG_HOME": str(root / "xdg")})
            initial = subprocess.run(
                [str(first / "kilix"), "screen-size", "show"], env=env,
                capture_output=True, text=True)
            self.assertEqual(initial.returncode, 0, initial.stderr)
            shutil.move(first, second)
            moved = subprocess.run(
                [str(second / "kilix"), "screen-size", "show"], env=env,
                capture_output=True, text=True)
            self.assertEqual(moved.returncode, 0, moved.stderr)
            defaults = root / "xdg" / "kilix" / ".kilix-defaults.conf"
            self.assertEqual(defaults.resolve(), second / "config" / "kitty.conf")
            self.assertIn("include .kilix-defaults.conf",
                          (root / "xdg" / "kilix" / "kitty.conf").read_text())


if __name__ == "__main__":
    unittest.main()
