"""The custodian's real D-Bus paths against fake logind, fprintd and polkit.

A private dbus-daemon started here carries the fakes (fingerlock_bus_fixture).
Each scenario runs in a fresh process whose system bus is that private bus, so
the real code runs unchanged:
- the Gio logind watch on its own main context (sleep guard, Lock signal),
- the monitor's NULL_MAIN_LOOP private lookups on its own thread,
- the reader scan iterating the default GLib context with dbus-python, and
- the polkit check over D-Bus.
All of them run at once. The system bus, logind, fprintd and polkit of this
machine are never contacted.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fingerlock_bus_fixture.py"


def _bindings():
    try:
        import dbus  # noqa: F401
        import dbus.mainloop.glib  # noqa: F401
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio  # noqa: F401
        return True
    except Exception:
        return False


DAEMON = shutil.which("dbus-daemon")
CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>custom</type>
  <listen>unix:path={path}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow user="*"/>
    <allow own="*"/>
    <allow send_destination="*"/>
    <allow receive_sender="*"/>
  </policy>
</busconfig>
"""


@unittest.skipUnless(DAEMON and _bindings(), "dbus-daemon, python3-dbus and python3-gi are required")
class BusTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        config = Path(cls.tmp.name) / "bus.conf"
        config.write_text(CONFIG.format(path=Path(cls.tmp.name) / "bus"))
        cls.daemon = subprocess.Popen([DAEMON, f"--config-file={config}", "--nofork", "--print-address=1"],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        address = cls.daemon.stdout.readline().decode().strip()
        if not address:
            cls._stop()
            raise unittest.SkipTest("the private dbus-daemon did not start")
        cls.env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": cls.tmp.name,
                   "DBUS_SYSTEM_BUS_ADDRESS": address, "DBUS_SESSION_BUS_ADDRESS": "disabled:"}
        cls.service = subprocess.Popen([sys.executable, "-I", "-B", str(FIXTURE), "service", str(os.getuid())],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=cls.env)
        if cls.service.stdout.readline() != b"ready\n":
            error = cls.service.stderr.read1(4096) if cls.service.poll() is not None else b""
            cls._stop()
            raise AssertionError(f"the fake services did not start: {error!r}")

    @classmethod
    def _stop(cls):
        # Only the processes this class started, through their own handles.
        service = getattr(cls, "service", None)
        if service is not None:
            service.stdin.close()            # the service exits when its stdin closes
            try:
                service.wait(timeout=5)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait()
            service.stdout.close()
            service.stderr.close()
        cls.daemon.terminate()
        try:
            cls.daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.daemon.kill()
            cls.daemon.wait()
        cls.daemon.stdout.close()
        cls.daemon.stderr.close()
        cls.tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        cls._stop()

    def run_scenario(self, name):
        proc = subprocess.run([sys.executable, "-I", "-B", str(FIXTURE), "scenario", name],
                              capture_output=True, text=True, timeout=60, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result["scenario"], name)
        return result

    @staticmethod
    def times(result, event):
        return [item["t"] for item in result["events"] if item["event"] == event]


class ConcurrentPathsTests(BusTestCase):
    def test_a_scan_runs_while_the_monitor_and_the_logind_watch_run(self):
        result = self.run_scenario("scan")
        self.assertTrue(result["watch_ready"])
        self.assertTrue(result["reply"]["ok"], result["reply"])
        self.assertEqual(result["plain"], "plain")
        start, = self.times(result, "verify-start")
        verdict, = self.times(result, "verify-status")
        during = [t for t in result["lookups"] if start <= t <= verdict]
        # About 20 ticks in the 1 s touch; the private lookups kept answering.
        self.assertGreaterEqual(len(during), 5, result["lookups"])
        self.assertGreaterEqual(len([t for t in self.times(result, "session-props") if start <= t <= verdict]), 5)
        inhibit, = [e for e in result["events"] if e["event"] == "inhibit"]
        self.assertEqual((inhibit["what"], inhibit["mode"]), ("sleep:shutdown", "delay"))
        self.assertEqual(result["status"]["watch"], {"monitor": "running", "sleep_guard": "ready",
                                                     "required": True})

    def test_a_lock_hint_during_a_scan_stops_it(self):
        result = self.run_scenario("lock-hint")
        self.assertEqual(result["reply"]["code"], "EACCESS", result["reply"])
        self.assertIn("revoked during verification", result["reply"]["error"])
        self.assertEqual(result["plain"], "")
        self.assertLess(result["seconds"], 5.0)              # not the 8 s touch
        self.assertEqual(result["status"]["last_forget"], "session-locked")
        names = [e["event"] for e in result["events"]]
        self.assertIn("verify-stop", names)
        self.assertIn("release", names)

    def test_a_lock_signal_during_a_scan_stops_it(self):
        result = self.run_scenario("lock-signal")
        self.assertEqual(result["reply"]["code"], "EACCESS", result["reply"])
        self.assertIn("revoked during verification", result["reply"]["error"])
        self.assertLess(result["seconds"], 5.0)
        self.assertEqual(result["status"]["last_forget"], "session-locked")
        # LockedHint never changed: the signal alone forgot the key.
        self.assertFalse([e for e in result["events"] if e["event"] == "set"])


class SleepSignalTests(BusTestCase):
    def check(self, name, reason):
        result = self.run_scenario(name)
        self.assertTrue(result["watch_ready"])
        self.assertTrue(result["forgotten"])
        wipe = [w for w in result["wipes"] if w["reason"] == reason and w["count"] == 1]
        self.assertEqual(len(wipe), 1, result["wipes"])
        released = self.times(result, "inhibitor-released")
        self.assertTrue(released, result["events"])
        self.assertLessEqual(wipe[0]["t"], released[0])
        self.assertEqual(result["status_asleep"]["last_forget"], reason)
        self.assertEqual(result["status_asleep"]["watch"]["sleep_guard"], "sleeping")
        self.assertEqual(result["deposit_asleep"]["code"], "ESERVICE")
        self.assertTrue(result["inhibitors_after_resume"])
        self.assertTrue(result["ready_after_resume"])
        self.assertEqual(result["status_awake"]["watch"]["sleep_guard"], "ready")

    def test_prepare_for_sleep_forgets_then_releases_and_resume_retakes(self):
        self.check("sleep", "suspend")

    def test_prepare_for_shutdown_forgets_then_releases(self):
        self.check("shutdown", "shutdown")


class PolkitCheckTests(BusTestCase):
    def test_the_request_travels_over_dbus_not_argv(self):
        result = self.run_scenario("scan")     # helper processes are forbidden in every scenario
        checks = [e for e in result["events"] if e["event"] == "polkit-check"]
        self.assertEqual([c["interactive"] for c in checks], [False, True])
        for check in checks:
            self.assertEqual(check["action"], "org.kilix.fingerlock.unseal")
            self.assertEqual(check["subject"], ["unix-process", ["pid", "start-time", "uid"]])
            self.assertIn('Requested name: "secret-entry-name"', check["details"]["polkit.message"])
            self.assertEqual(len(check["details"]["fingerlock.digest"]), 64)

    def test_withdrawal_cancels_the_open_dialog(self):
        result = self.run_scenario("withdraw")
        self.assertEqual(result["reply"]["code"], "EACCESS", result["reply"])
        self.assertIn("approval withdrawn", result["reply"]["error"])
        held, = [e for e in result["events"] if e["event"] == "polkit-check" and e["interactive"]]
        cancel, = [e for e in result["events"] if e["event"] == "polkit-cancel"]
        self.assertEqual(cancel["cancel_id"], held["cancel_id"])
        self.assertTrue(cancel["matched"])
        self.assertFalse(self.times(result, "claim"))      # the reader was never claimed

    def test_a_dismissed_dialog_is_cancelled_not_approved(self):
        result = self.run_scenario("dismiss")
        self.assertEqual(result["reply"]["code"], "EACCESS")
        self.assertIn("approval was cancelled", result["reply"]["error"])
        self.assertTrue(result["status"]["held"])
        self.assertFalse(self.times(result, "claim"))

    def test_a_timeout_cancels_the_open_dialog(self):
        result = self.run_scenario("timeout")
        self.assertEqual(result["error"], "OperationTimeout")
        cancel, = [e for e in result["events"] if e["event"] == "polkit-cancel"]
        self.assertTrue(cancel["matched"])


if __name__ == "__main__":
    unittest.main()
