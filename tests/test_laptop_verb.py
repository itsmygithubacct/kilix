"""`kilix laptop` — the host-side owner of the laptop profile convention.

Two contracts are pinned here. First, the parser must match the desktops'
rejection rules exactly: the fixtures and the rejection catalogue below are
the same ones kilix-cap's --laptop-test and kilix-land-desktop's
--laptop-test run, so a profile one surface refuses, every surface refuses,
and the generated session lines are asserted verbatim against the desktops'
own expectations. Second, the run registry (run/<profile-id>.pid beside the
profiles) must never trust a file without a real process check, must clean
stale files on sight, and must close a session by signalling the recorded
pid — that registry is what makes a desktop's laptop draw itself open.
"""
import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "kilix_laptop", ROOT / "config" / "laptop.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class LaptopVerbTests(unittest.TestCase):
    def setUp(self):
        self.laptop = load_module()
        self.root = Path(tempfile.mkdtemp(prefix="kilix-laptop-test."))
        self.addCleanup(self._cleanup_tree)
        self.profiles = self.root / "profiles"
        self.profiles.mkdir(mode=0o700)
        self.saved = {
            name: os.environ.get(name)
            for name in ("KILIX_LAPTOP_PROFILES", "KILIX_SESSION_HOME",
                         "KILIX_HOME", "HOME")
        }
        self.addCleanup(self._restore_environ)
        os.environ["KILIX_LAPTOP_PROFILES"] = str(self.profiles)
        os.environ["KILIX_SESSION_HOME"] = str(self.root / "session")
        os.environ["KILIX_HOME"] = str(self.root)
        self._install_fake_kilix()
        self.spawned = []

    def _cleanup_tree(self):
        for pid in getattr(self, "spawned", []):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        subprocess.run(["rm", "-rf", str(self.root)], check=False)

    def _restore_environ(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _install_fake_kilix(self):
        # `--session` keeps running the way a real session window does;
        # any provider word exits at once the way the tab wrapper does.
        fake = self.root / "kilix"
        fake.write_text("#!/bin/sh\ncase \"$1\" in\n"
                        "  --session) exec sleep 60 ;;\n"
                        "  *) exit 0 ;;\nesac\n")
        fake.chmod(0o700)

    def _write(self, name, text):
        (self.profiles / name).write_text(text)

    def _open(self, profile_id):
        code = self.laptop.cmd_open(profile_id)
        pid = self.laptop.session_pid(profile_id)
        if pid is not None:
            self.spawned.append(pid)
        return code

    # ── parsing parity with the desktops ────────────────────────────────

    def test_pane_profile_fields_and_session_lines(self):
        # The dev fixture from the desktops' own selftests, asserted down
        # to the exact generated kitty --session lines.
        self._write("dev.profile",
                    "# comment\n"
                    "name=Dev Bench\n"
                    "layout=splits\n"
                    "pane.1.title=editor\n"
                    "pane.1.cwd=~/projects\n"
                    "pane.2.cmd=htop\n"
                    "pane.3.ssh=user@build-host\n"
                    "pane.3.cwd=/srv\n"
                    "pane.3.cmd=tail -f service.log\n")
        profile = self.laptop.load_profile("dev")
        self.assertEqual(profile["name"], "Dev Bench")
        self.assertFalse(profile["tabs"])
        self.assertEqual(profile["pane_count"], 3)
        self.assertEqual(profile["panes"][2]["ssh"], "user@build-host")
        self.assertEqual(self.laptop.desktop_arguments(profile), [])
        text = self.laptop.session_text(profile)
        self.assertIn("os_window_title Dev Bench\n", text)
        self.assertIn("layout splits\n", text)
        self.assertIn("title editor\n", text)
        self.assertIn("/projects\n", text)
        self.assertIn('launch --location=vsplit sh -lc "htop"\n', text)
        self.assertIn('launch --location=hsplit ssh -t user@build-host '
                      '"cd /srv && exec tail -f service.log"\n', text)
        self.assertNotIn("new_tab editor", text)

    def test_tabbed_profile_session_lines(self):
        self._write("ops.profile",
                    "name=Ops\nlayout=tabs\n"
                    "pane.1.cmd=journalctl -f\n"
                    "pane.2.ssh=admin@gateway\n")
        profile = self.laptop.load_profile("ops")
        self.assertTrue(profile["tabs"])
        text = self.laptop.session_text(profile)
        self.assertIn("new_tab Ops\n", text)
        self.assertNotIn("new_tab journalctl", text)
        self.assertIn("launch ssh -t admin@gateway\n", text)

    def test_desktop_profile_argv(self):
        self._write("house.profile", "name=House\ndesktop=land\n")
        profile = self.laptop.load_profile("house")
        self.assertEqual(self.laptop.desktop_arguments(profile), ["land"])
        self._write("classic.profile", "desktop=95\n")
        profile = self.laptop.load_profile("classic")
        self.assertEqual(self.laptop.desktop_arguments(profile),
                         ["desktop", "95"])
        with self.assertRaises(self.laptop.ProfileError):
            self.laptop.session_text(profile)

    def test_rejection_catalogue_matches_the_desktops(self):
        # The exact catalogue the C desktops run in their --laptop-test.
        rejected = [
            ("bad1", 'pane.1.cmd=echo "hi"\n'),          # quotes
            ("bad2", "desktop=cap\npane.1.cmd=htop\n"),  # both kinds
            ("bad3", "pane.1.cmd=a\npane.3.cmd=b\n"),    # pane gap
            ("bad4", "desktop=gnome\n"),                 # unknown provider
            ("bad5", "pane.1.ssh=host; rm -rf /\n"),     # ssh charset
            ("bad6", "shape=round\n"),                   # unknown key
            ("bad7", "pane.1.cmd\n"),                    # not KEY=value
            ("bad8", "pane.0.cmd=x\n"),                  # pane range
            ("bad9", "name=\n"),                         # empty name
        ]
        for profile_id, text in rejected:
            self._write(profile_id + ".profile", text)
            with self.subTest(profile=profile_id):
                with self.assertRaises(self.laptop.ProfileError):
                    self.laptop.load_profile(profile_id)
        with self.assertRaises(self.laptop.ProfileError):
            self.laptop.load_profile("../escape")

    def test_scan_lists_only_valid_profile_stems(self):
        self._write("b.profile", "pane.1.cmd=htop\n")
        self._write("a.profile", "pane.1.cmd=htop\n")
        self._write("not-a-profile.txt", "x=y\n")
        self._write(".hidden.profile", "pane.1.cmd=htop\n")
        self.assertEqual(self.laptop.scan_profiles(), ["a", "b"])

    # ── the run registry ────────────────────────────────────────────────

    def test_registry_requires_a_live_process(self):
        # A recorded live pid reads back; the file alone is never
        # believed — a dead or garbled entry is stale and is deleted.
        self.laptop.record_session("alive", os.getpid())
        self.assertEqual(self.laptop.session_pid("alive"), os.getpid())
        dead = subprocess.Popen(["true"])
        dead.wait()
        self.laptop.record_session("dead", dead.pid)
        self.assertIsNone(self.laptop.session_pid("dead"))
        self.assertFalse(Path(self.laptop.pid_path("dead")).exists())
        Path(self.laptop.pid_path("junk")).write_text("not-a-pid\n")
        self.assertIsNone(self.laptop.session_pid("junk"))
        self.assertFalse(Path(self.laptop.pid_path("junk")).exists())

    def test_open_records_and_close_terminates(self):
        self._write("bench.profile", "name=Bench\npane.1.cwd=~\n")
        self.assertEqual(self._open("bench"), 0)
        pid = self.laptop.session_pid("bench")
        self.assertIsNotNone(pid)
        session = Path(os.environ["KILIX_SESSION_HOME"],
                       "laptop-bench.session")
        self.assertTrue(session.exists())
        self.assertEqual(session.stat().st_mode & 0o777, 0o600)
        # A second open is idempotent: no duplicate session, same pid.
        self.assertEqual(self._open("bench"), 0)
        self.assertEqual(self.laptop.session_pid("bench"), pid)
        self.assertEqual(self.laptop.cmd_close("bench"), 0)
        self.assertIsNone(self.laptop.session_pid("bench"))
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("close left the session process running")

    def test_close_without_a_session_is_calm(self):
        self._write("bench.profile", "name=Bench\npane.1.cwd=~\n")
        self.assertEqual(self.laptop.cmd_close("bench"), 0)
        with self.assertRaises(self.laptop.ProfileError):
            self.laptop.cmd_close("missing")

    def test_status_reports_every_profile_state(self):
        self._write("bench.profile", "name=Bench\npane.1.cwd=~\n")
        self._write("house.profile", "desktop=land\n")
        self._write("broken.profile", "shape=round\n")
        self.laptop.record_session("bench", os.getpid())
        result = subprocess.run(
            [sys.executable, str(ROOT / "config" / "laptop.py"), "status"],
            capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn("bench running (pid %d)" % os.getpid(), lines)
        self.assertIn("house desktop", lines)
        self.assertIn("broken invalid", lines)

    def test_help_carries_the_probe_token(self):
        # Desktops probe `kilix laptop help` for "open PROFILE" before
        # delegating, exactly like games.py probes for "play GAME".
        result = subprocess.run(
            [sys.executable, str(ROOT / "config" / "laptop.py"), "help"],
            capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(result.returncode, 0)
        self.assertIn("open PROFILE", result.stdout)
        self.assertIn("close PROFILE", result.stdout)

    def test_usage_errors_exit_two(self):
        for argv in (["open"], ["close"], ["sideways"], ["list", "extra"]):
            result = subprocess.run(
                [sys.executable, str(ROOT / "config" / "laptop.py"), *argv],
                capture_output=True, text=True, env=dict(os.environ))
            self.assertEqual(result.returncode, 2, argv)
            self.assertIn("usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
