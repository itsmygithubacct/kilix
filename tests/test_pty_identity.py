"""`kilix pty kill --expect-started` against REAL brokers built from the pinned submodule.

The reviewed defect (A2 F1): a status check followed by an unconditional terminate
kills a session that was replaced under the same ID in between. These tests start
real scratch sessions, replace one between the lookup and the terminate request
with a forwarding wrapper, and require the replacement to survive. Everything runs
in a short private runtime under /tmp with a scratch HOME; only PIDs this test
started are ever signalled.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "kilix"
CAN_BUILD = bool(shutil.which("make") and shutil.which("cc") and shutil.which("flock"))
OWN = "aaaaaaaaaaaaaaaa"

WRAPPER = '''#!/usr/bin/env python3
import os, subprocess, sys, time
real, flag, record = %(real)r, %(flag)r, %(record)r
args = sys.argv[1:]
head = 2 + (2 if args[2] == "--timeout" else 0)      # --runtime-dir RT [--timeout T]
if args[head] == "kill" and os.path.exists(flag):
    os.unlink(flag)                                                  # once
    ident, prefix = args[head + 1], args[:head]
    subprocess.run([real, *prefix, "kill", ident], check=True)          # end the session that was seen
    for _ in range(200):
        if subprocess.run([real, *prefix, "status", ident, "--json"], capture_output=True).returncode:
            break
        time.sleep(0.02)
    subprocess.run([real, *prefix, "run", "--id", ident, "--", "/bin/sleep", "90"], check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)  # a new session, same ID
    done = subprocess.run([real, *prefix, "status", ident, "--json"], capture_output=True, check=True)
    open(record, "wb").write(done.stdout)
os.execv(real, [real, *args])
'''


OLD_BROKER = "8cf3eb3671458370e57539be6b7bd6f4a1b8f615"      # before the identity-bound terminate


@unittest.skipUnless(CAN_BUILD, "needs make, a C compiler and flock to build the pinned broker")
class RealBrokerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = Path(tempfile.mkdtemp(prefix="kxid-build."))
        env = {"HOME": str(cls.build), "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
               "KILIX_STORAGE_HOME": str(cls.build / "storage"),
               "KILIX_BUILD_DIRECTORY": str(cls.build / "storage" / "build")}
        built = subprocess.run(["bash", str(ROOT / "scripts" / "build-pty-broker.sh"), "--print-path"],
                               env=env, capture_output=True, text=True, timeout=600)
        if built.returncode:
            shutil.rmtree(cls.build, ignore_errors=True)
            raise unittest.SkipTest("the pinned broker did not build: " + built.stderr[-300:])
        cls.broker = built.stdout.strip()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.build, ignore_errors=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="kxid.", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for name in ("home", "tmp", "rt", "xdg"):
            (self.root / name).mkdir(mode=0o700)
        self.rt = self.root / "rt"
        self.pids = []
        self.addCleanup(self.stop_owned)
        self.executable = self.broker

    def stop_owned(self):
        for pid in reversed(self.pids):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def env(self, **extra):
        env = {"HOME": str(self.root / "home"), "TMPDIR": str(self.root / "tmp"), "LANG": "C.UTF-8",
               "PATH": "/usr/local/bin:/usr/bin:/bin", "USER": "kxid", "LOGNAME": "kxid",
               "KILIX_STORAGE_HOME": str(self.root / "store"), "KITTY_PTY_BROKER_RUNTIME": str(self.rt),
               "KITTY_PTY_BROKER_EXECUTABLE": self.executable, "KITTY_PTY_BROKER_SESSION": OWN,
               "PYTHONDONTWRITEBYTECODE": "1"}
        env.update(extra)
        return env

    def run_broker(self, *args):
        return subprocess.run([self.broker, "--runtime-dir", str(self.rt), *args], capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=30)

    def spawn(self, ident="target"):
        started = self.run_broker("run", "--id", ident, "--", "/bin/sleep", "90")
        self.assertEqual(started.returncode, 0, started.stderr)
        status = json.loads(self.run_broker("status", ident, "--json").stdout)
        self.pids.extend((status["broker_pid"], status["child_pid"]))
        return status

    def kill(self, ident, *flags):
        result = subprocess.run(["bash", str(LAUNCHER), "pty", "kill", ident, "--yes", "--json", *flags],
                                env=self.env(), capture_output=True, text=True, timeout=60,
                                stdin=subprocess.DEVNULL)
        return result, json.loads(result.stdout)

    def alive(self, ident):
        return self.run_broker("status", ident, "--json").returncode == 0

    def wrap(self):
        flag, record = self.root / "swap", self.root / "replacement.json"
        script = self.root / "wrapper"
        script.write_text(WRAPPER % {"real": self.broker, "flag": str(flag), "record": str(record)})
        script.chmod(0o700)
        flag.write_text("")
        self.executable = str(script)
        return record

    def capture(self, name, receipt):
        """Keep a real receipt for the consumers that want a fixture (KILIX_PTY_CAPTURE_DIR)."""
        target = os.environ.get("KILIX_PTY_CAPTURE_DIR")
        if target:
            os.makedirs(target, exist_ok=True)
            Path(target, name + ".json").write_text(json.dumps(receipt, indent=2) + "\n")


class IdentityBoundKillTests(RealBrokerCase):
    def test_the_exact_identity_ends_the_session_and_is_verified(self):
        seen = self.spawn()
        result, receipt = self.kill("target", "--expect-started", str(seen["started_millis"]))
        self.assertEqual((result.returncode, receipt["result"]), (0, "verified_absent"), receipt)
        self.assertFalse(self.alive("target"))
        self.capture("kill-verified_absent", receipt)

    def test_a_stale_expectation_leaves_the_session_alone(self):
        seen = self.spawn()
        result, receipt = self.kill("target", "--expect-started", str(seen["started_millis"] - 1))
        self.assertEqual((result.returncode, receipt["result"], receipt["reason"]), (3, "refused", "started_mismatch"))
        self.assertTrue(self.alive("target"))
        self.capture("kill-refused-started_mismatch", receipt)

    def test_a_replacement_between_the_lookup_and_the_terminate_survives(self):
        seen = self.spawn()
        record = self.wrap()
        try:
            result, receipt = self.kill("target", "--expect-started", str(seen["started_millis"]))
        finally:
            if record.exists():
                replacement = json.loads(record.read_text())
                self.pids.extend((replacement["broker_pid"], replacement["child_pid"]))
        self.assertTrue(record.exists(), "the wrapper never replaced the session")
        replacement = json.loads(record.read_text())
        self.assertNotEqual(replacement["started_millis"], seen["started_millis"])
        self.assertEqual((result.returncode, receipt["result"], receipt["reason"]),
                         (3, "refused", "started_mismatch"), receipt)
        self.assertFalse(receipt["request_sent"])
        self.assertTrue(self.alive("target"), "the replacement session was killed")
        self.assertEqual(json.loads(self.run_broker("status", "target", "--json").stdout)["started_millis"],
                         replacement["started_millis"])

    def test_a_plain_kill_without_an_expectation_is_unchanged(self):
        self.spawn()
        result, receipt = self.kill("target")
        self.assertEqual((result.returncode, receipt["result"]), (0, "verified_absent"), receipt)
        self.assertFalse(self.alive("target"))


class OldBrokerTests(RealBrokerCase):
    """A session whose broker predates the identity-bound terminate (built from 8cf3eb3)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        source = Path(cls.build) / "old-source"
        source.mkdir()
        archive = subprocess.run(["git", "-C", str(ROOT / "third_party" / "kitty-pty-broker"), "archive",
                                  OLD_BROKER], capture_output=True)
        if archive.returncode:
            raise unittest.SkipTest("the old broker source (8cf3eb3) is not in the submodule's history")
        subprocess.run(["tar", "-x", "-C", str(source)], input=archive.stdout, check=True)
        built = subprocess.run(["make", "--no-print-directory", "-C", str(source),
                                f"BUILD_DIR={cls.build / 'old-build'}", "all"],
                               capture_output=True, text=True, timeout=600,
                               env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(cls.build)})
        if built.returncode:
            raise unittest.SkipTest("the old broker did not build: " + built.stderr[-300:])
        cls.old_broker = str(cls.build / "old-build" / "kitty-pty-broker")

    def old_status(self, ident):
        return subprocess.run([self.old_broker, "--runtime-dir", str(self.rt), "status", ident, "--json"],
                              capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)

    def spawn_old(self, ident="target"):
        started = subprocess.run([self.old_broker, "--runtime-dir", str(self.rt), "run", "--id", ident, "--",
                                  "/bin/sleep", "90"], capture_output=True, text=True, timeout=30,
                                 stdin=subprocess.DEVNULL)
        self.assertEqual(started.returncode, 0, started.stderr)
        status = json.loads(self.old_status(ident).stdout)
        self.pids.extend((status["broker_pid"], status["child_pid"]))
        return status

    def test_a_bound_kill_of_an_old_session_is_refused_and_the_session_survives(self):
        seen = self.spawn_old()
        result, receipt = self.kill("target", "--expect-started", str(seen["started_millis"]))
        self.assertEqual((result.returncode, receipt["result"], receipt["reason"]), (3, "refused", "cannot_bind"), receipt)
        self.assertFalse(receipt["request_sent"])
        self.assertIn("kilix pty kill target --yes", receipt["hint"])
        self.assertIn("without --expect-started", receipt["message"])
        self.assertEqual(self.old_status("target").returncode, 0, "the old session was killed blind")
        self.capture("kill-refused-cannot_bind", receipt)

    def test_a_plain_kill_of_an_old_session_still_works(self):
        self.spawn_old()
        result, receipt = self.kill("target")
        self.assertEqual((result.returncode, receipt["result"]), (0, "verified_absent"), receipt)
        self.assertNotEqual(self.old_status("target").returncode, 0)


if __name__ == "__main__":
    unittest.main()
