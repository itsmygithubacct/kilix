import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UserConfigBehaviorTests(unittest.TestCase):
    def test_explicit_runtime_environment_wins_over_persisted_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            config = storage / "config"
            config.mkdir(parents=True)
            (config / "kilix.env").write_text("KILIX_DESKTOP_PROVIDER=none\n")
            env = dict(os.environ)
            env.update({
                "KILIX_STORAGE_HOME": str(storage),
                "KILIX_DESKTOP_PROVIDER": "command",
            })
            result = subprocess.run(
                [str(ROOT / "kilix"), "status"], env=env,
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("desktop provider: command", result.stdout)

            env.pop("KILIX_DESKTOP_PROVIDER")
            result = subprocess.run(
                [str(ROOT / "kilix"), "status"], env=env,
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("desktop provider: none", result.stdout)

    def test_screen_size_writes_xdg_override_not_tracked_default(self):
        tracked = ROOT / "config" / "kitty.conf"
        before = digest(tracked)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = dict(os.environ)
            env.pop("KILIX_CONFIG_DIRECTORY", None)
            env.pop("KITTY_CONFIG_DIRECTORY", None)
            env.pop("KILIX_ENV_CONFIG", None)
            storage = root / "storage"
            env.update({"HOME": str(root / "home"),
                        "KILIX_STORAGE_HOME": str(storage)})
            result = subprocess.run(
                [str(ROOT / "kilix"), "screen-size", "set", "14"],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            user = storage / "config" / "kitty.conf"
            text = user.read_text()
            self.assertIn("include .kilix-defaults.conf", text)
            self.assertIn("font_size", text)
            self.assertIn("14", text)
            self.assertTrue((storage / "config" / "kilix.env").exists())
            defaults = storage / "config" / ".kilix-defaults.conf"
            self.assertEqual(defaults.resolve(), tracked)
            password = storage / "session" / "rc-password"
            rc_config = storage / "session" / "rc-password.conf"
            self.assertEqual(stat.S_IMODE(password.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(rc_config.stat().st_mode), 0o600)
            self.assertRegex(password.read_text().strip(), r"^[0-9a-f]{64}$")
            self.assertNotIn(password.read_text().strip(), user.read_text())
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
            storage = root / "storage"
            env.update({"HOME": str(root / "home"),
                        "KILIX_STORAGE_HOME": str(storage)})
            initial = subprocess.run(
                [str(first / "kilix"), "screen-size", "show"], env=env,
                capture_output=True, text=True)
            self.assertEqual(initial.returncode, 0, initial.stderr)
            shutil.move(first, second)
            moved = subprocess.run(
                [str(second / "kilix"), "screen-size", "show"], env=env,
                capture_output=True, text=True)
            self.assertEqual(moved.returncode, 0, moved.stderr)
            defaults = storage / "config" / ".kilix-defaults.conf"
            self.assertEqual(defaults.resolve(), second / "config" / "kitty.conf")
            self.assertIn("include .kilix-defaults.conf",
                          (storage / "config" / "kitty.conf").read_text())


if __name__ == "__main__":
    unittest.main()
