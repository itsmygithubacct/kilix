"""Contained browsers: disposable by default, persistent when asked."""
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import app_profiles  # noqa: E402


def _dead_pid() -> int:
    """A PID that is not running: fork a child and reap it."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return pid


class StaleProfileReapingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = os.path.join(self.tmp.name, "app-profiles")
        os.makedirs(self.parent, mode=0o700)

    def _profile(self, pid, start, age_seconds):
        suffix = f"s{len(os.listdir(self.parent))}"      # distinct per call
        name = (f"chromium-{pid}-{start}-{suffix}" if start is not None
                else f"chromium-{pid}-{suffix}")
        path = os.path.join(self.parent, name)
        os.makedirs(path, mode=0o700)
        old = time.time() - age_seconds
        os.utime(path, (old, old))
        return path

    def test_a_dead_owner_is_reaped_immediately_not_after_a_week(self):
        # The 0.2.1 behaviour: this profile, one day old with its owner gone,
        # survived until it was a week old. Ten of them made 1.2 GB.
        path = self._profile(_dead_pid(), 12345, age_seconds=86400)
        app_profiles.cleanup_stale_app_profiles(self.parent)
        self.assertFalse(os.path.exists(path))

    def test_the_control_a_live_owner_is_kept_whatever_its_age(self):
        start = app_profiles._process_start(os.getpid())
        path = self._profile(os.getpid(), start, age_seconds=30 * 86400)
        app_profiles.cleanup_stale_app_profiles(self.parent)
        self.assertTrue(os.path.exists(path), "reaped a profile whose owner is alive")

    def test_a_live_pid_with_no_start_time_is_kept_only_for_a_week(self):
        # Without a start time a reused PID cannot be told from the owner, so
        # age is the only thing that can end such a claim.
        young = self._profile(os.getpid(), None, age_seconds=86400)
        old = self._profile(os.getpid(), None, age_seconds=8 * 86400)
        app_profiles.cleanup_stale_app_profiles(self.parent)
        self.assertTrue(os.path.exists(young))
        self.assertFalse(os.path.exists(old))


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
