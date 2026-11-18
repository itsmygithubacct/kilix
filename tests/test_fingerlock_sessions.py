"""Custody across session lock, switch, logout, suspend, resume and reboot.

Policy: custody belongs to the logind session that deposited the key. Lock,
switching away, logout, logind losing the session or being unable to say,
and any suspend or hibernate forget it. A pending approval or scan for that
key stops. Nothing is persisted, so a restart or reboot holds nothing
(process restart: test_fingerlock_lifecycle). Every logind and clock input
here is a fake; no session is locked, suspended or ended.
"""
import importlib
import os
import subprocess
import sys
import threading
import time
import unittest
import unittest.mock
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

core = importlib.import_module("fingerlock_core")
agentmod = importlib.import_module("fingerlock_agent")
policy = importlib.import_module("fingerlock_policy")

try:
    import nacl  # noqa: F401  deposit derives the public key
    HAVE_NACL = True
except ImportError:
    HAVE_NACL = False

KEY = bytes(range(32))
PUBLIC = bytes(32)


@unittest.skipUnless(HAVE_NACL, "python3-nacl is not installed")
class SessionTransitionTests(unittest.TestCase):
    def setUp(self):
        self.uid = os.getuid()
        self.slept = [0.0]
        self.custody = agentmod.Custody(now=lambda: 1000.0, min_ttl=0, sleep_offset=lambda: self.slept[0])
        self.addCleanup(self.custody.close)
        self.desk = agentmod.CallerSession("3", self.uid, True, "active", "seat0", False, False)
        self.state = {"3": self.desk}
        self.reader_calls = []

        def lookup(session_id):
            value = self.state.get(session_id)
            if value is None:
                raise agentmod.SessionGone("gone")
            if isinstance(value, Exception):
                raise value
            return value

        with unittest.mock.patch.object(agentmod, "_harden_process", return_value=agentmod.HardenReport()):
            self.agent = agentmod.Agent(custody=self.custody, client_factory=self._no_reader,
                                        session_lookup=lambda pid: self.caller(pid),
                                        session_state=lookup, approve=lambda *args: None)
        self.callers = {4242: "3"}
        self.logged = []
        self.agent.log = self.logged.append

    def _no_reader(self):
        self.reader_calls.append(1)
        raise AssertionError("the reader must not be reached")

    def caller(self, pid):
        return self.state[self.callers[pid]]

    def deposit(self, pid=4242):
        return self.agent.handle({"protocol": 1, "op": "deposit", "key": KEY.hex(),
                                  "locker_id": "disposable", "ttl": 600}, self.uid, pid)[0]

    def status(self):
        return self.agent.handle({"protocol": 1, "op": "status"}, self.uid, 4242)[0]

    def test_unchanged_session_keeps_custody(self):
        self.assertTrue(self.deposit()["ok"])
        self.assertEqual(self.agent.check_transitions(), [])
        self.assertTrue(self.status()["held"])

    def test_lock_switch_logout_and_unknown_state_forget(self):
        cases = {
            "session-locked": lambda: replace(self.desk, locked=True),
            "session-inactive": lambda: replace(self.desk, active=False, state="online"),
            "session-ended": lambda: None,
            "session-unknown": lambda: core.AccessDenied("logind unavailable"),
        }
        for reason, change in cases.items():
            with self.subTest(reason=reason):
                self.state["3"] = self.desk
                self.assertTrue(self.deposit()["ok"])
                changed = change()
                if changed is None:
                    del self.state["3"]
                else:
                    self.state["3"] = changed
                self.assertEqual(self.agent.check_transitions(), [(self.uid, reason)])
                status = self.status()   # status needs no seat, so it answers after logout
                self.assertFalse(status["held"])
                self.assertEqual(status["last_forget"], reason)
                self.assertIsNone(self.custody.peek(self.uid))
                self.assertIn(f"custody for uid {self.uid} forgotten ({reason})", self.logged)

    def test_unlocking_again_does_not_restore_custody(self):
        self.deposit()
        self.state["3"] = replace(self.desk, locked=True)
        self.agent.check_transitions()
        self.state["3"] = self.desk
        self.assertEqual(self.agent.check_transitions(), [])
        self.assertFalse(self.status()["held"])

    def test_a_transition_does_not_drop_a_newer_deposit(self):
        self.deposit()
        _uid, _session, stale = self.custody.held()[0]
        self.deposit()
        self.assertFalse(self.custody.forget_generation(self.uid, stale, "session-locked"))
        self.assertTrue(self.status()["held"])

    def test_custody_is_used_only_from_the_depositing_session(self):
        self.deposit()
        self.state["7"] = replace(self.desk, session_id="7")
        self.callers[5151] = "7"
        reply, plain = self.agent.handle({"protocol": 1, "op": "confirm", "locker_id": "disposable",
                                          "public_key": PUBLIC.hex()}, self.uid, 5151)
        self.assertEqual(reply["code"], "EACCESS")
        self.assertIn("session that deposited", reply["error"])
        blob = b"container"
        binding = {"kind": "unlock", "name": "a.flk", "size": len(blob),
                   "sha256": __import__("hashlib").sha256(blob).hexdigest()}
        reply, plain = self.agent.handle({"protocol": 1, "op": "operate", "locker_id": "disposable",
                                          "body_bytes": len(blob), "binding": binding}, self.uid, 5151, blob)
        self.assertEqual(reply["code"], "EACCESS")
        self.assertIn("session that deposited", reply["error"])
        self.assertEqual(plain, b"")
        self.assertEqual(self.reader_calls, [])

    def test_suspend_or_hibernate_forgets_on_first_use_after_resume(self):
        self.deposit()
        self.slept[0] += 0.4          # clock noise below the slack keeps custody
        self.assertTrue(self.status()["held"])
        self.slept[0] += 30.0         # a sleep: BOOTTIME advanced past MONOTONIC
        status = self.status()        # no sweep ran; the request itself checks
        self.assertFalse(status["held"])
        self.assertEqual(status["last_forget"], "suspend")
        self.assertEqual(self.custody.held(), [])

    def test_suspend_is_measured_from_the_deposit(self):
        self.slept[0] = 500.0         # time asleep before the deposit does not count
        self.deposit()
        self.assertTrue(self.status()["held"])

    def test_restart_holds_nothing(self):
        # A reboot is a restart with RAM cleared; there is no custody file to reload.
        self.deposit()
        with unittest.mock.patch.object(agentmod, "_harden_process", return_value=agentmod.HardenReport()):
            fresh = agentmod.Agent(custody=agentmod.Custody(), session_lookup=self.caller,
                                   session_state=lambda sid: self.desk)
        self.addCleanup(fresh.close)
        reply = fresh.handle({"protocol": 1, "op": "status"}, self.uid, 4242)[0]
        self.assertFalse(reply["held"])
        self.assertNotIn("last_forget", reply)

    def test_monitor_thread_forgets_on_lock(self):
        self.agent.session_interval = 0.01
        self.deposit()
        self.agent.start_monitor()
        self.addCleanup(self.agent._stopping.set)
        self.state["3"] = replace(self.desk, locked=True)
        deadline = time.monotonic() + 3
        while self.custody.peek(self.uid) is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNone(self.custody.peek(self.uid))
        self.assertEqual(self.custody.last_reason(self.uid), "session-locked")

    def test_lock_during_a_pending_approval_withdraws_it(self):
        # The real pkcheck runner, holding a stand-in for the dialog process.
        self.deposit()
        children = []
        real_popen = subprocess.Popen

        def start(argv, **kwargs):
            child = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(child)
            return child

        def approve(uid, pid, binding, alive):
            with unittest.mock.patch.object(policy.subprocess, "Popen", side_effect=start):
                policy._run_check(["unused"], alive, 10)

        self.agent.approve = approve
        blob = b"container"
        binding = {"kind": "unlock", "name": "a.flk", "size": len(blob),
                   "sha256": __import__("hashlib").sha256(blob).hexdigest()}
        box = {}
        worker = threading.Thread(target=lambda: box.setdefault("out", self.agent.handle(
            {"protocol": 1, "op": "operate", "locker_id": "disposable", "body_bytes": len(blob),
             "binding": binding}, self.uid, 4242, blob)))
        worker.start()
        deadline = time.monotonic() + 3
        while not children and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(children, "approval did not start")
        self.state["3"] = replace(self.desk, locked=True)
        self.agent.check_transitions()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        reply, plain = box["out"]
        self.assertEqual(reply["code"], "EACCESS")
        self.assertIn("approval withdrawn", reply["error"])
        self.assertEqual(plain, b"")
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(self.reader_calls, [])


class SleepClockTests(unittest.TestCase):
    def test_sleep_offset_is_boottime_minus_monotonic(self):
        clocks = {time.CLOCK_BOOTTIME: 1030.0, time.CLOCK_MONOTONIC: 1000.0}
        with unittest.mock.patch.object(agentmod.time, "clock_gettime", side_effect=clocks.__getitem__):
            self.assertEqual(agentmod._sleep_offset(), 30.0)


class ServeWiringTests(unittest.TestCase):
    def test_the_service_starts_the_session_monitor_and_stops_it(self):
        started, closed = [], []
        listener = unittest.mock.Mock()
        with unittest.mock.patch.object(agentmod.os, "geteuid", return_value=0), \
                unittest.mock.patch.object(agentmod, "_listener_from_systemd", return_value=listener), \
                unittest.mock.patch.object(agentmod, "_harden_process", return_value=agentmod.HardenReport()), \
                unittest.mock.patch.object(agentmod.Agent, "start_monitor", lambda self: started.append(self)), \
                unittest.mock.patch.object(agentmod.Agent, "serve", lambda self, sock: None), \
                unittest.mock.patch.object(agentmod.Agent, "close", lambda self: closed.append(self)):
            self.assertEqual(agentmod.serve(1000), 0)
        self.assertEqual(len(started), 1)
        self.assertEqual(closed, started)
        started[0].custody.close()


class LogindLookupTests(unittest.TestCase):
    def fake_dbus(self, error_name=None, facts=None):
        class Error(Exception):
            def get_dbus_name(self_inner):
                return error_name

        class Manager:
            def GetSession(self_inner, session_id, timeout=None):
                if error_name:
                    raise Error()
                return "/org/freedesktop/login1/session/_33"

        class Props:
            def GetAll(self_inner, iface, timeout=None):
                return facts

        class Bus:
            def get_object(self_inner, name, path, introspect=False):
                return path

            def close(self_inner):
                pass

        def interface(obj, name):
            return Manager() if name.endswith("Manager") else Props()

        loops = []

        def system_bus(private, mainloop=None):
            loops.append((private, mainloop))
            return Bus()

        self.loops = loops
        null = object()
        return unittest.mock.Mock(SystemBus=system_bus, Interface=interface, UInt32=int,
                                  mainloop=unittest.mock.Mock(NULL_MAIN_LOOP=null))

    def test_vanished_session_is_ended_not_unknown(self):
        with self.assertRaises(agentmod.SessionGone):
            agentmod.session_by_id("3", dbus_module=self.fake_dbus("org.freedesktop.login1.NoSuchSession"))

    def test_other_logind_failure_is_access_denied(self):
        with self.assertRaises(core.AccessDenied) as caught:
            agentmod.session_by_id("3", dbus_module=self.fake_dbus("org.freedesktop.DBus.Error.NoReply"))
        self.assertNotIsInstance(caught.exception, agentmod.SessionGone)

    def test_live_session_snapshot_includes_lock_hint(self):
        facts = {"Active": True, "LockedHint": True, "Remote": False, "User": (os.getuid(), "/u"),
                 "Id": "3", "State": "active", "Seat": ("seat0", "/s")}
        session = agentmod.session_by_id("3", dbus_module=self.fake_dbus(facts=facts))
        self.assertTrue(session.locked)
        self.assertEqual(session.session_id, "3")
        # Private, and never attached to the GLib loop a reader scan may be iterating.
        (private, loop), = self.loops
        self.assertTrue(private)
        self.assertIsNotNone(loop)


if __name__ == "__main__":
    unittest.main()
