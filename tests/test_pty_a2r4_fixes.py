"""Regression tests for the A2 round-4 review (reviews/A2-r4/REVIEW.md): N3 and N4.

N3: every duration setting has one supported range (greater than 0, at most 86400 s); anything else
falls back to the default, and an owned broker client never outlives a failed wait. N4: the standalone
build script validates its lock deadline exactly as the launcher does. Each fails on fbf80515.
"""
import fcntl
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_cli import LAUNCHER, PtyCliCase  # noqa: E402
import kilix_pty  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build-pty-broker.sh"
MAX = "86400"

#: (setting, what the helper must return for a default of 77)
RANGE = [
    ("1", "1"), ("0.5", "0.5"), (".5", ".5"), ("2.", "2."), ("007", "007"), ("0.001", "0.001"),
    (MAX, MAX), ("86400.", "86400."), ("86399.999", "86399.999"), ("00086400", "00086400"),
    ("86400.001", "77"), ("86401", "77"), ("3000000", "77"), ("9" * 20, "77"), ("9" * 400, "77"),
    ("0." + "0" * 400 + "1", "77"),                      # underflows to 0 once it is a number
    ("0", "77"), ("0.0", "77"), ("00", "77"), (".0", "77"), ("0.", "77"), ("-1", "77"), ("", "77"),
    ("abc", "77"), ("1e1", "77"), ("1,5", "77"), ("+1", "77"), (" 1", "77"), ("1 ", "77"), ("1\n", "77"),
]


def function_text(path, name="_kilix_pty_seconds"):
    text = Path(path).read_text()
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n", start) + 3]


def seconds(path, value):
    script = f'source <(sed -n "/^_kilix_pty_seconds()/,/^}}/p" "$1"); _kilix_pty_seconds "$2" 77'
    return subprocess.run(["bash", "-c", script, "x", str(path), value], capture_output=True, text=True,
                          check=True).stdout.rstrip("\n")


class RangeTests(unittest.TestCase):
    def test_n3_the_launcher_accepts_exactly_the_supported_range(self):
        for value, expected in RANGE:
            with self.subTest(value=value[:12]):
                self.assertEqual(seconds(LAUNCHER, value), expected)

    def test_n3_the_build_script_applies_the_same_range(self):
        for value, expected in RANGE:
            with self.subTest(value=value[:12]):
                self.assertEqual(seconds(BUILD_SCRIPT, value), expected)

    def test_n4_the_two_copies_of_the_helper_are_identical(self):
        self.assertEqual(function_text(LAUNCHER), function_text(BUILD_SCRIPT))

    def test_n3_the_helper_rejects_what_it_cannot_represent(self):
        for value in ("10", MAX):
            self.assertEqual(kilix_pty.guard_seconds(value), float(value))
        for value in ("nan", "inf", "-inf", "1e999", "9" * 400, "3000000", "86400.5", "0", "-1", "", "abc", None):
            with self.subTest(value=value):
                self.assertEqual(kilix_pty.guard_seconds(value), 10.0)


class OwnedChildTests(PtyCliCase):
    """`Broker.call` ends its client's process group on every path out of the wait."""

    def sleeper(self):
        pidfile = self.tmp / "client.pid"
        (self.fake / "list.hook").write_text(f'echo $$ > "{pidfile}"; exec sleep 60\n')
        return pidfile

    def gone(self, pid):
        try:
            return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] == "Z"
        except FileNotFoundError:
            return True

    def test_n3_a_failure_that_is_not_a_timeout_still_ends_the_child(self):
        pidfile = self.sleeper()
        broker = kilix_pty.Broker(str(self.broker), str(self.runtime), None, 30)
        real = subprocess.Popen.communicate
        calls = []

        def failing(process, *args, **kwargs):
            if not calls:
                calls.append(1)
                deadline = time.monotonic() + 10
                while not pidfile.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                raise RuntimeError("injected failure while waiting")
            return real(process, *args, **kwargs)

        with mock.patch.dict(os.environ, self.env()), \
                mock.patch.object(subprocess.Popen, "communicate", failing), \
                self.assertRaisesRegex(RuntimeError, "injected failure"):
            broker.call("list", "--json")
        child = int(pidfile.read_text())
        self.addCleanup(lambda: self.gone(child) or os.kill(child, signal.SIGKILL))
        deadline = time.monotonic() + 5
        while not self.gone(child) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.gone(child), "the client outlived the failed wait")

    def test_n3_a_timeout_still_returns_none_and_ends_the_child(self):
        pidfile = self.sleeper()
        broker = kilix_pty.Broker(str(self.broker), str(self.runtime), None, 0.5)
        with mock.patch.dict(os.environ, self.env()):
            self.assertEqual(broker.call("list", "--json"), (None, b"", ""))
        child = int(pidfile.read_text())
        self.addCleanup(lambda: self.gone(child) or os.kill(child, signal.SIGKILL))
        self.assertTrue(self.gone(child))

    def end_to_end(self, setting):
        pidfile = self.sleeper()
        process = subprocess.Popen(["bash", str(LAUNCHER), "pty", "list", "--json"],
                                   env=self.env(KILIX_PTY_GUARD=setting), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(pidfile.exists())
            child = int(pidfile.read_text())
            self.addCleanup(lambda: self.gone(child) or os.kill(child, signal.SIGKILL))
            _, err = process.communicate(timeout=30)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        self.assertNotIn(b"Traceback", err)
        self.assertIn(b"did not answer within 10s", err, "the out-of-range guard fell back to the default")
        self.assertTrue(self.gone(child), "the broker client outlived the guard")

    def test_n3_a_finite_but_too_large_guard_falls_back_instead_of_crashing(self):
        self.end_to_end("3000000")

    def test_n3_a_guard_that_overflows_a_float_falls_back_instead_of_crashing(self):
        self.end_to_end("9" * 400)


class BuildLockTests(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for name in ("home", "store", "bin"):
            (self.tmp / name).mkdir(mode=0o700)
        self.log = self.tmp / "flock.args"

    def env(self, setting=None, **extra):
        env = {"PATH": f"{self.tmp / 'bin'}:/usr/local/bin:/usr/bin:/bin", "HOME": str(self.tmp / "home"),
               "KILIX_STORAGE_HOME": str(self.tmp / "store"), "LANG": "C.UTF-8"}
        if setting is not None:
            env["KILIX_PTY_BUILD_LOCK_TIMEOUT"] = setting
        env.update(extra)
        return env

    def fake_flock(self, status):
        shim = self.tmp / "bin" / "flock"
        shim.write_text(f'#!/bin/sh\necho "$*" >> "{self.log}"\nexit {status}\n')
        shim.chmod(0o755)

    def run_script(self, setting=None, **extra):
        return subprocess.run(["bash", str(BUILD_SCRIPT), "--print-path"], env=self.env(setting, **extra),
                              capture_output=True, text=True, timeout=30)

    def flock_args(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_n4_a_supplied_deadline_goes_through_the_launchers_validation(self):
        self.fake_flock(75)
        for setting, wait in (("2.5", "2.5"), (MAX, MAX), ("0", "600"), ("-1", "600"), ("..", "600"), ("1,5", "600"),
                              ("+1", "600"), (" 1", "600"), ("9" * 400, "600"), ("9999999", "600"), ("abc", "600")):
            with self.subTest(setting=setting[:12]):
                self.log.unlink(missing_ok=True)
                self.run_script(setting)
                self.assertEqual(self.flock_args(), [f"-w {wait} -E 75 9"])

    def test_n4_unset_and_empty_stay_unbounded_for_a_person_running_the_script(self):
        self.fake_flock(75)
        for setting in (None, ""):
            with self.subTest(setting=setting):
                self.log.unlink(missing_ok=True)
                self.run_script(setting)
                self.assertEqual(self.flock_args(), ["9"])

    def test_n4_contention_and_flock_failures_are_told_apart(self):
        self.fake_flock(75)
        busy = self.run_script("1")
        self.assertEqual(busy.returncode, 75)
        self.assertIn("another build holds the lock", busy.stderr)
        for status in (64, 71, 1):
            with self.subTest(status=status):
                self.fake_flock(status)
                failed = self.run_script("1")
                self.assertEqual(failed.returncode, 1)
                self.assertNotIn("another build holds", failed.stderr)
                self.assertIn(f"could not take the build lock (flock exited {status})", failed.stderr)

    def test_n4_real_contention_still_gives_up_with_75(self):
        lock = self.tmp / "store" / "build" / "libraries" / "kitty-pty-broker" / ".build.lock"
        lock.parent.mkdir(parents=True, mode=0o700)
        with lock.open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            started = time.monotonic()
            result = self.run_script("0.3")
            self.assertEqual(result.returncode, 75, result.stderr)
            self.assertIn("another build holds the lock (waited 0.3s)", result.stderr)
            self.assertLess(time.monotonic() - started, 10)


if __name__ == "__main__":
    unittest.main()
