"""Custody across session lock, switch, logout, suspend, resume and reboot.

Policy: custody belongs to the logind session that deposited the key. Lock
(LockedHint or the Lock signal), switching away, logout, logind losing the
session or being unable to say, and any suspend, hibernate or shutdown forget
it; sleep and shutdown wait on the custodian's inhibitor until the key is
gone. Custody is refused while the monitor or the sleep guard is not working.
A pending approval or scan for that key stops. Nothing is persisted, so a
restart or reboot holds nothing (process restart: test_fingerlock_lifecycle).
Every logind and clock input here is a fake; no session is locked, suspended
or ended. The real D-Bus paths run against fakes in test_fingerlock_bus.
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


class FakeWatch:
    """Stands in for LogindWatch: attaches the agent's sleep guard to pipe inhibitors."""

    def __init__(self, agent, ok=True):
        self.agent, self.ok = agent, ok
        self.inhibitors = []   # read ends; EOF means the agent released that inhibitor
        self.started = 0
        self.stopped = False

    def inhibit(self):
        read, write = os.pipe()
        self.inhibitors.append(read)
        return write

    def start(self):
        self.started += 1
        if self.ok:
            self.agent.sleep_guard.attach(self.inhibit)
        return self.ok

    def running(self):
        return self.ok and self.started > 0 and not self.stopped

    def stop(self):
        self.stopped = True
        self.agent.sleep_guard.detach("stopped")

    def released(self, index=-1):
        import select as _select
        fd = self.inhibitors[index]
        return bool(_select.select([fd], [], [], 0)[0]) and os.read(fd, 1) == b""


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
                                        session_state=lookup, approve=lambda *args: None,
                                        custody_watch=False, watch_factory=FakeWatch)
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
        self.slept[0] += 0.004        # measurement noise below the slack keeps custody
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
        # A stand-in for the open dialog that, like the polkit runner, watches the
        # agent's liveness callback. The real runner: test_fingerlock_bus.
        self.deposit()
        children = []
        closed = []

        def approve(uid, pid, binding, alive):
            children.append(1)
            end = time.monotonic() + 10
            while alive() and time.monotonic() < end:
                time.sleep(0.01)
            closed.append(not alive())
            if not alive():
                raise core.AccessDenied("operation approval withdrawn: the caller left or custody was forgotten")

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
        self.assertEqual(closed, [True])
        self.assertEqual(self.reader_calls, [])


def _quiet_agent(**kwargs):
    with unittest.mock.patch.object(agentmod, "_harden_process", return_value=agentmod.HardenReport()):
        return agentmod.Agent(**kwargs)


class WatchedCustodyCase(unittest.TestCase):
    """An agent that requires its watch, as the service does, over fake logind
    lookups and a FakeWatch. Keys go straight into custody, so python3-nacl is
    not needed; requests that must be refused are refused before any key use."""

    watch_ok = True

    def setUp(self):
        self.uid = os.getuid()
        self.slept = [0.0]
        self.custody = agentmod.Custody(now=lambda: 1000.0, min_ttl=0, sleep_offset=lambda: self.slept[0])
        self.desk = agentmod.CallerSession("3", self.uid, True, "active", "seat0", False, False)
        self.state = {"3": self.desk}
        self.watches = []
        self.logged = []

        def lookup(session_id):
            value = self.state.get(session_id)
            if value is None:
                raise agentmod.SessionGone("gone")
            if isinstance(value, Exception):
                raise value
            return value

        def factory(agent):
            watch = FakeWatch(agent, ok=self.watch_ok)
            self.watches.append(watch)
            return watch

        self.agent = _quiet_agent(custody=self.custody, client_factory=self._no_reader,
                                  session_lookup=lambda pid: lookup("3"), session_state=lookup,
                                  approve=lambda *args: None, watch_factory=factory,
                                  session_interval=0.01)
        self.agent.log = self.logged.append
        self.addCleanup(self.agent.close)

    def _no_reader(self):
        raise AssertionError("the reader must not be reached")

    def put(self):
        return self.custody.deposit(self.uid, "owner", KEY, "disposable", PUBLIC, 600, session_id="3")

    def request(self, op, **fields):
        return self.agent.handle({"protocol": 1, "op": op, **fields}, self.uid, 4242)[0]

    def deposit_request(self):
        return self.request("deposit", key=KEY.hex(), locker_id="disposable", ttl=600)

    def wait_for(self, predicate, seconds=3.0):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        return predicate()


class SleepGuardTests(WatchedCustodyCase):
    """Finding 1: the key is forgotten before sleep, while logind waits on the inhibitor."""

    def start(self):
        self.agent.start_monitor()
        self.watch = self.watches[0]
        self.assertTrue(self.agent.sleep_guard.ready())

    def test_prepare_for_sleep_forgets_before_the_inhibitor_is_released(self):
        self.start()
        self.put()
        seen = []
        real_forget_all = self.custody.forget_all

        def forget_all(reason="forget"):
            # At this moment logind must still be waiting on us.
            seen.append(("released-before-forget", self.watch.released(0)))
            count = real_forget_all(reason)
            seen.append(("held-after-forget", self.custody.held()))
            return count

        self.custody.forget_all = forget_all
        self.agent.sleep_guard.prepare("sleep", True)
        self.assertEqual(seen[:2], [("released-before-forget", False), ("held-after-forget", [])])
        self.assertTrue(self.watch.released(0))
        self.assertEqual(self.custody.last_reason(self.uid), "suspend")

    def test_prepare_for_shutdown_forgets_before_the_inhibitor_is_released(self):
        self.start()
        self.put()
        order = []
        real_forget_all = self.custody.forget_all
        self.custody.forget_all = lambda reason="forget": (order.append(self.watch.released(0)),
                                                           real_forget_all(reason))[1]
        self.agent.sleep_guard.prepare("shutdown", True)
        self.assertEqual(order, [False])
        self.assertTrue(self.watch.released(0))
        self.assertEqual(self.custody.last_reason(self.uid), "shutdown")

    def test_custody_is_refused_while_asleep_and_the_inhibitor_returns_on_resume(self):
        self.start()
        self.agent.sleep_guard.prepare("sleep", True)
        reply = self.deposit_request()
        self.assertEqual(reply["code"], "ESERVICE")
        self.assertIn("sleep guard is sleeping", reply["error"])
        self.assertEqual(self.request("status")["watch"]["sleep_guard"], "sleeping")
        self.agent.sleep_guard.prepare("sleep", False)
        self.assertEqual(len(self.watch.inhibitors), 2)       # taken again
        self.assertFalse(self.watch.released(1))
        self.assertTrue(self.agent.sleep_guard.ready())
        self.assertIsNone(self.agent.watch_problem())

    def test_aborted_hibernate_with_no_clock_growth_still_forgets(self):
        # The image is written, the transition fails and userspace thaws: logind
        # sends PrepareForSleep(true) then (false); BOOTTIME barely moves.
        self.start()
        self.put()
        self.agent.sleep_guard.prepare("sleep", True)
        self.agent.sleep_guard.prepare("sleep", False)
        self.assertEqual(self.custody.held(), [])
        self.assertEqual(self.custody.last_reason(self.uid), "suspend")

    def test_a_short_sleep_without_signals_is_caught_by_the_clock_backstop(self):
        self.put()
        self.slept[0] += 0.05         # 50 ms asleep, no PrepareForSleep seen
        self.assertEqual(self.custody.held(), [])
        self.assertEqual(self.custody.last_reason(self.uid), "suspend")

    def test_a_lost_resume_signal_rearms_after_the_grace_period(self):
        now = [0.0]
        guard = agentmod.SleepGuard(lambda reason: None, lambda line: None, grace=30.0, clock=lambda: now[0])
        pipes = []
        guard.attach(lambda: pipes.append(os.pipe()) or pipes[-1][1])
        guard.prepare("sleep", True)
        now[0] = 29.0
        guard.ensure()
        self.assertFalse(guard.ready())
        now[0] = 31.0
        guard.ensure()
        self.assertTrue(guard.ready())
        self.assertEqual(len(pipes), 2)
        guard.close()

    def test_losing_the_watch_forgets_and_refuses_custody(self):
        self.start()
        self.put()
        self.watch.stop()
        self.assertEqual(self.custody.held(), [])
        self.assertEqual(self.custody.last_reason(self.uid), "sleep-watch-lost")
        reply = self.deposit_request()
        self.assertEqual(reply["code"], "ESERVICE")
        self.assertIn("sleep guard is unavailable", reply["error"])

    def test_the_monitor_restarts_a_lost_watch(self):
        self.start()
        self.watch.stop()
        self.agent._watch_tried = -1e9            # skip the retry spacing
        self.assertTrue(self.wait_for(lambda: len(self.watches) == 2 and self.agent.sleep_guard.ready()))
        self.assertIsNone(self.agent.watch_problem())


class SleepGuardUnavailableTests(WatchedCustodyCase):
    watch_ok = False

    def test_no_inhibitor_means_no_custody(self):
        self.agent.start_monitor()
        reply = self.deposit_request()
        self.assertEqual(reply["code"], "ESERVICE")
        self.assertIn("sleep guard is unavailable", reply["error"])
        self.assertIn("custody is refused", reply["error"])
        self.assertEqual(self.request("status")["watch"]["monitor"], "running")


class LockSignalTests(WatchedCustodyCase):
    """Finding 2: logind's Lock signal forgets at once, whatever LockedHint says."""

    def test_lock_signal_for_the_depositing_session_forgets(self):
        self.put()
        self.assertEqual(self.agent.on_session_lock("/org/freedesktop/login1/session/_37"), [])
        self.assertEqual(len(self.custody.held()), 1)
        dropped = self.agent.on_session_lock("/org/freedesktop/login1/session/_33")
        self.assertEqual(dropped, [(self.uid, "session-locked")])
        self.assertEqual(self.custody.held(), [])
        self.assertEqual(self.custody.last_reason(self.uid), "session-locked")
        self.assertEqual(self.state["3"].locked, False)    # LockedHint was never set

    def test_session_object_paths_match_logind_escaping(self):
        for session_id, tail in (("3", "_33"), ("12", "_312"), ("c2", "c2"), ("a-b", "a_2db"),
                                 ("x_1", "x_5f1"), ("", "_")):
            with self.subTest(session_id=session_id):
                self.assertEqual(agentmod.session_object_path(session_id),
                                 "/org/freedesktop/login1/session/" + tail)


class MonitorFailClosedTests(WatchedCustodyCase):
    """Finding 3: a failed check forgets, logging cannot kill the monitor, and
    custody is refused while the monitor is not running."""

    def test_custody_is_refused_before_the_monitor_starts(self):
        reply = self.deposit_request()
        self.assertEqual(reply["code"], "ESERVICE")
        self.assertIn("session monitor is not-started", reply["error"])
        self.assertEqual(self.request("status")["watch"]["monitor"], "not-started")

    def test_a_check_that_raises_forgets_and_the_monitor_keeps_running(self):
        self.agent.start_monitor()
        self.put()
        self.agent.transition_reason = unittest.mock.Mock(side_effect=RuntimeError("bug"))
        self.assertTrue(self.wait_for(lambda: self.custody.last_reason(self.uid) == "session-check-failed"))
        self.assertEqual(self.custody.held(), [])
        time.sleep(0.05)
        self.assertEqual(self.agent.monitor_state(), "running")

    def test_a_log_that_raises_does_not_stop_the_monitor(self):
        def broken(line):
            raise BrokenPipeError("journal stream closed")
        self.agent.log = broken
        self.agent.start_monitor()
        self.put()
        self.agent.transition_reason = unittest.mock.Mock(side_effect=RuntimeError("bug"))
        self.assertTrue(self.wait_for(lambda: self.custody.held() == []))
        del self.agent.transition_reason
        self.put()
        self.state["3"] = replace(self.desk, locked=True)
        self.assertTrue(self.wait_for(lambda: self.custody.last_reason(self.uid) == "session-locked"))
        self.assertEqual(self.agent.monitor_state(), "running")

    def test_a_dead_monitor_forgets_and_refuses_custody(self):
        self.agent.start_monitor()
        self.put()
        self.agent._tick = unittest.mock.Mock(side_effect=SystemExit("thread killed"))
        self.assertTrue(self.wait_for(lambda: self.agent.monitor_state() == "stopped"))
        self.assertEqual(self.custody.held(), [])
        self.assertEqual(self.custody.last_reason(self.uid), "monitor-stopped")
        reply = self.deposit_request()
        self.assertEqual(reply["code"], "ESERVICE")
        self.assertIn("session monitor is stopped", reply["error"])
        self.assertEqual(self.request("status")["watch"]["monitor"], "stopped")

    def test_a_stalled_monitor_refuses_custody(self):
        self.agent.start_monitor()
        self.agent._tick = lambda: time.sleep(5)
        self.agent._beat = time.monotonic() - agentmod.MONITOR_STALL_SECONDS - 1
        time.sleep(0.05)
        self.assertEqual(self.agent.monitor_state(), "stalled")
        self.assertIn("stalled", self.deposit_request()["error"])
        self.agent._stopping.set()


class RestartTests(unittest.TestCase):
    def test_restart_holds_nothing(self):
        # A crash or reboot drops the process without any forget; the next agent,
        # built the way serve() builds it, must start empty. (Process-level:
        # test_fingerlock_lifecycle.)
        uid = os.getuid()
        desk = agentmod.CallerSession("3", uid, True, "active", "seat0", False, False)
        first = _quiet_agent(session_lookup=lambda pid: desk, session_state=lambda sid: desk,
                             custody_watch=False)
        first.custody.deposit(uid, "owner", KEY, "disposable", PUBLIC, 600, session_id="3")
        status = {"protocol": 1, "op": "status"}
        self.assertTrue(first.handle(status, uid, 4242)[0]["held"])
        first.custody._stop.set()          # the crash: threads stop, nothing is wiped
        second = _quiet_agent(session_lookup=lambda pid: desk, session_state=lambda sid: desk,
                              custody_watch=False)
        self.addCleanup(second.close)
        self.addCleanup(first.custody.forget_all)
        reply = second.handle(status, uid, 4242)[0]
        self.assertFalse(reply["held"])
        self.assertNotIn("last_forget", reply)


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
