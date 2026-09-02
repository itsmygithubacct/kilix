"""Contained browsers: disposable by default, persistent when asked."""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import app_profiles  # noqa: E402


class PrepareAppCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.session = os.path.join(self.tmp.name, "session")
        os.makedirs(self.session, mode=0o700)
        patcher = mock.patch.dict(os.environ, {
            "KILIX_SESSION_HOME": self.session,
            app_profiles.PERSISTENT_PROFILE_ENV: ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_is_a_disposable_profile_under_the_session(self):
        argv, profile = app_profiles.prepare_app_command(["chromium", "x"])
        self.assertIsNotNone(profile)
        self.assertTrue(profile.startswith(os.path.join(self.session, "app-profiles")))
        self.assertIn(f"--user-data-dir={profile}", argv)

    def test_an_explicit_profile_is_left_alone(self):
        argv, profile = app_profiles.prepare_app_command(
            ["chromium", "--user-data-dir=/somewhere", "x"])
        self.assertIsNone(profile)
        self.assertEqual(argv, ["chromium", "--user-data-dir=/somewhere", "x"])

    def test_the_persistent_profile_is_used_and_never_handed_back_for_deletion(self):
        persistent = os.path.join(self.tmp.name, "browser-profile")
        with mock.patch.dict(os.environ, {app_profiles.PERSISTENT_PROFILE_ENV: persistent}):
            argv, profile = app_profiles.prepare_app_command(["google-chrome", "x"])
        self.assertIsNone(profile, "a persistent profile must never be deleted")
        self.assertIn(f"--user-data-dir={persistent}", argv)
        self.assertTrue(os.path.isdir(persistent))
        self.assertEqual(stat.S_IMODE(os.stat(persistent).st_mode), 0o700)

    def test_firefox_gets_the_same_persistent_profile(self):
        persistent = os.path.join(self.tmp.name, "ff-profile")
        with mock.patch.dict(os.environ, {app_profiles.PERSISTENT_PROFILE_ENV: persistent}):
            argv, profile = app_profiles.prepare_app_command(["firefox", "x"])
        self.assertIsNone(profile)
        self.assertEqual(argv[:3], ["firefox", "--profile", persistent])
        self.assertIn("--no-remote", argv)

    def test_an_explicit_flag_still_wins_over_the_persistent_profile(self):
        persistent = os.path.join(self.tmp.name, "browser-profile")
        with mock.patch.dict(os.environ, {app_profiles.PERSISTENT_PROFILE_ENV: persistent}):
            argv, profile = app_profiles.prepare_app_command(
                ["chromium", "--user-data-dir=/mine"])
        self.assertIsNone(profile)
        self.assertNotIn(persistent, " ".join(argv))

    def test_a_symlinked_persistent_profile_is_refused(self):
        real = os.path.join(self.tmp.name, "real"); os.makedirs(real, mode=0o700)
        link = os.path.join(self.tmp.name, "link"); os.symlink(real, link)
        with mock.patch.dict(os.environ, {app_profiles.PERSISTENT_PROFILE_ENV: link}):
            with self.assertRaises(RuntimeError):
                app_profiles.prepare_app_command(["chromium"])

    def test_the_control_an_unset_setting_changes_nothing(self):
        argv_a, prof_a = app_profiles.prepare_app_command(["chromium"])
        self.assertIsNotNone(prof_a)


if __name__ == "__main__":
    unittest.main()
