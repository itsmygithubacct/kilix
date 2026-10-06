import hashlib
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# The suite runs both as `discover -s tests` (bare module names) and as
# `-m unittest tests.<module>` (package), so name this directory explicitly.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402



ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean_env():
    # KILIX_ with the underscore missed KILIX95_* entirely; sandbox_env
    # strips the family.
    return sandbox_env()


class UserConfigBehaviorTests(unittest.TestCase):
    def test_explicit_runtime_environment_wins_over_persisted_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "storage"
            config = storage / "config"
            config.mkdir(parents=True)
            (config / "kilix.env").write_text("KILIX_DESKTOP_PROVIDER=none\n")
            env = clean_env()
            env.update({
                "KILIX_STORAGE_HOME": str(storage),
                "KILIX_DESKTOP_PROVIDER": "command",
                "GPU_TERMINAL_SETTINGS_FILE": str(Path(tmp) / "settings.conf"),
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
            env = clean_env()
            env.pop("KITTY_CONFIG_DIRECTORY", None)
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

    def test_repeated_calls_leave_watched_config_files_untouched(self):
        # The running terminal reloads its configuration whenever kitty.conf or
        # rc-password.conf is replaced, dropping runtime settings. A call that
        # changes nothing must not replace either file.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = clean_env()
            env.pop("KITTY_CONFIG_DIRECTORY", None)
            storage = root / "storage"
            env.update({"HOME": str(root / "home"),
                        "KILIX_STORAGE_HOME": str(storage)})
            run = lambda *a: subprocess.run(
                [str(ROOT / "kilix"), *a], env=env, capture_output=True, text=True)
            first = run("screen-size", "show")
            self.assertEqual(first.returncode, 0, first.stderr)
            watched = [storage / "config" / "kitty.conf",
                       storage / "session" / "rc-password.conf",
                       storage / "config" / "kilix.env"]
            # ctime too: a no-op chmod leaves inode and mtime alone but still
            # raises the inotify event the terminal's config watcher reloads on.
            identity = lambda p: (p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
            before = [identity(p) for p in watched]
            time.sleep(0.02)
            for args in (("screen-size", "show"), ("status",)):
                again = run(*args)
                self.assertEqual(again.returncode, 0, again.stderr)
            after = [identity(p) for p in watched]
            self.assertEqual(after, before)
            # A wrong mode is still repaired, on both files.
            watched[0].chmod(0o644)
            watched[2].chmod(0o644)
            self.assertEqual(run("status").returncode, 0)
            self.assertEqual(watched[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(watched[2].stat().st_mode & 0o777, 0o600)
            # A real change is still written.
            changed = run("screen-size", "set", "15")
            self.assertEqual(changed.returncode, 0, changed.stderr)
            self.assertIn("15", watched[0].read_text())
            self.assertEqual(stat.S_IMODE(watched[1].stat().st_mode), 0o600)

    def test_a_symlinked_kitty_conf_is_not_touched_either(self):
        # Dotfile managers link kitty.conf; the watcher watches the target.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = clean_env()
            env.pop("KITTY_CONFIG_DIRECTORY", None)
            storage = root / "storage"
            env.update({"HOME": str(root / "home"), "KILIX_STORAGE_HOME": str(storage)})
            run = lambda *a: subprocess.run(
                [str(ROOT / "kilix"), *a], env=env, capture_output=True, text=True)
            self.assertEqual(run("status").returncode, 0)
            conf = storage / "config" / "kitty.conf"
            target = root / "dotfiles" / "kitty.conf"
            target.parent.mkdir()
            os.replace(conf, target)
            target.chmod(0o600)
            conf.symlink_to(target)
            self.assertEqual(run("status").returncode, 0)
            before = target.stat().st_ctime_ns
            time.sleep(0.02)
            self.assertEqual(run("status").returncode, 0)
            self.assertEqual(target.stat().st_ctime_ns, before, "the link target was chmodded again")
            self.assertTrue(conf.is_symlink(), "the link itself is kept")

    def test_managed_links_follow_a_moved_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            (first / "config").mkdir(parents=True)
            shutil.copy2(ROOT / "kilix", first / "kilix")
            shutil.copy2(ROOT / "kilix-settings", first / "kilix-settings")
            shutil.copy2(ROOT / "config" / "kitty.conf",
                         first / "config" / "kitty.conf")
            shutil.copy2(ROOT / "config" / "kilix.env",
                         first / "config" / "kilix.env")
            shutil.copytree(ROOT / "config" / "kilix_sdk",
                            first / "config" / "kilix_sdk")
            env = clean_env()
            env.pop("KITTY_CONFIG_DIRECTORY", None)
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
