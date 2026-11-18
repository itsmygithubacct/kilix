"""Default fingerprint unlock against a stateful fake fprintd.

The 2026-10-30 receipts showed the same enrolled finger matching on some
touches and not on others, outside the locker too (no-match, no-match, match),
while every default unlock ended on its first verdict. fprintd ends a
verification at each no-match; its own PAM client re-arms the reader up to
three times inside one claim. These tests pin that bounded re-arm, its shared
deadline and its limits, and the approval cancel path. No reader, D-Bus
daemon, polkit or logind is used.
"""
import hashlib
import importlib
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

core = importlib.import_module("fingerlock_core")
agentmod = importlib.import_module("fingerlock_agent")
policy = importlib.import_module("fingerlock_policy")

try:
    import nacl  # noqa: F401
    HAVE_NACL = True
except ImportError:
    HAVE_NACL = False

PATH = "/net/reactivated/Fprint/Device/0"


class FprintdError(Exception):
    def __init__(self, short):
        super().__init__(short)
        self.name = core.ERROR_PREFIX + short

    def get_dbus_name(self):
        return self.name

    def get_dbus_message(self):
        return ""


class FakeReader:
    """One fprintd device with fprintd's state rules.

    scans: one entry per armed scan, each (seconds the touch takes, [results]).
    The last result of a scan is its verdict. A scan with no results never sees
    a finger. VerifyStart while a verification is armed is AlreadyInUse, as in
    fprintd, so a missing VerifyStop between scans fails loudly.
    """

    MAX_STARTS = 10   # a runaway retry loop fails instead of hanging the suite

    def __init__(self, clock, scans, enrolled=("right-index-finger",)):
        self.clock, self.scans, self.enrolled = clock, list(scans), list(enrolled)
        self.calls, self.handlers = [], {}
        self.claimed = self.verifying = False
        self.armed = None
        self.waits = []
        self.during_wait = None

    def connect_to_signal(self, name, handler):
        self.handlers[name] = handler
        return unittest.mock.Mock()

    def Claim(self, username, reply_handler=None, error_handler=None, timeout=None):
        self.calls.append(("Claim", username))
        if self.claimed:
            return error_handler(FprintdError("AlreadyInUse"))
        self.claimed = True
        reply_handler()

    def Release(self):
        self.calls.append(("Release",))
        self.claimed = False

    def ListEnrolledFingers(self, username, reply_handler=None, error_handler=None, timeout=None):
        self.calls.append(("ListEnrolledFingers", username))
        reply_handler(list(self.enrolled))

    def VerifyStart(self, finger):
        self.calls.append(("VerifyStart", finger))
        if not self.claimed:
            raise FprintdError("ClaimDevice")
        if self.verifying:
            raise FprintdError("AlreadyInUse")
        if sum(1 for call in self.calls if call[0] == "VerifyStart") > self.MAX_STARTS:
            raise AssertionError("verification re-armed without a bound")
        self.verifying = True
        self.handlers["VerifyFingerSelected"](finger)
        seconds, results = self.scans.pop(0) if self.scans else (None, [])
        self.armed = (seconds, list(results))

    def VerifyStop(self):
        self.calls.append(("VerifyStop",))
        if not self.verifying:
            raise FprintdError("NoActionInProgress")
        self.verifying = False
        self.armed = None

    def starts(self):
        return [call[1] for call in self.calls if call[0] == "VerifyStart"]


class FakeBus:
    """run_until advances a fake monotonic clock instead of sleeping."""

    def __init__(self, reader):
        self.reader = reader

    def interface(self, path, name):
        return self.reader

    def run_until(self, predicate, timeout):
        reader = self.reader
        reader.waits.append(round(timeout, 3))
        if predicate():
            return True
        if reader.during_wait is not None:
            reader.during_wait()
            if predicate():
                return True
        seconds, results = reader.armed if reader.armed else (None, [])
        if not results or seconds > timeout:
            reader.clock[0] += timeout
            return False
        reader.clock[0] += seconds
        # One status at a time, as signals arrive: the wait ends as soon as the
        # client's condition holds, and later statuses stay queued for the scan.
        while results:
            result = results.pop(0)
            done = not results or not result.startswith(("verify-retry", "verify-swipe",
                                                         "verify-finger", "verify-remove"))
            reader.handlers["VerifyStatus"](result, done)
            if predicate():
                reader.armed = (0.0, results)
                return True
        reader.armed = (None, [])
        return predicate()


class ClockTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        patcher = unittest.mock.patch.object(core.time, "monotonic", side_effect=lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def reader(self, *scans, **kwargs):
        return FakeReader(self.clock, scans, **kwargs)

    def verify(self, reader, **kwargs):
        client = core.FprintdClient(FakeBus(reader))
        return client.verify(PATH, core.ANY_FINGER, username="owner", **kwargs)


class BoundedRearmTests(ClockTestCase):
    def test_rejected_touch_rearms_and_the_next_match_succeeds(self):
        # The receipt pattern: two rejected touches of the enrolled finger, then a match.
        reader = self.reader((2.4, ["verify-no-match"]), (5.0, ["verify-no-match"]), (1.0, ["verify-match"]))
        session = self.verify(reader, timeout=45, attempts=3)
        self.assertTrue(session.succeeded)
        self.assertEqual(session.attempts, 3)
        self.assertEqual(reader.starts(), ["right-index-finger"] * 3)
        # One claim for every scan, a stop before each re-arm, and one release.
        names = [call[0] for call in reader.calls]
        self.assertEqual(names.count("Claim"), 1)
        self.assertEqual(names[-2:], ["VerifyStop", "Release"])
        self.assertEqual(names.count("VerifyStop"), 3)
        self.assertFalse(reader.claimed)

    def test_three_rejected_touches_end_in_no_match_without_a_fourth_scan(self):
        reader = self.reader(*[(1.0, ["verify-no-match"])] * 5)
        session = self.verify(reader, timeout=45, attempts=3)
        self.assertEqual(session.result, core.VERIFY_NO_MATCH)
        self.assertFalse(session.succeeded)
        self.assertEqual(len(reader.starts()), 3)
        self.assertEqual(session.attempts, 3)

    def test_retry_statuses_do_not_use_up_scans(self):
        reader = self.reader((1.0, ["verify-retry-scan", "verify-finger-not-centered", "verify-no-match"]),
                             (1.0, ["verify-match"]))
        session = self.verify(reader, timeout=45, attempts=2)
        self.assertTrue(session.succeeded)
        self.assertEqual(len(reader.starts()), 2)

    def test_all_scans_share_one_deadline(self):
        # 20 s per rejected touch: the third scan has 5 s left, not a fresh 45 s.
        reader = self.reader(*[(20.0, ["verify-no-match"])] * 3)
        session = self.verify(reader, timeout=45, attempts=3)
        self.assertTrue(session.timed_out)
        scan_waits = [wait for wait in reader.waits if wait < core.AUTH_TIMEOUT_SECONDS]   # not Claim/List
        self.assertEqual(scan_waits, [45.0, 25.0, 5.0])
        self.assertEqual(self.clock[0], 145.0)

    def test_errors_other_than_no_match_are_not_retried(self):
        for verdict in ("verify-disconnected", "verify-unknown-error"):
            with self.subTest(verdict=verdict):
                reader = self.reader((1.0, [verdict]), (1.0, ["verify-match"]))
                session = self.verify(reader, timeout=45, attempts=3)
                self.assertEqual(session.result, verdict)
                self.assertEqual(len(reader.starts()), 1)

    def test_single_scan_is_the_default_for_the_reader_verb(self):
        reader = self.reader((1.0, ["verify-no-match"]), (1.0, ["verify-match"]))
        session = self.verify(reader, timeout=45)
        self.assertEqual(session.result, core.VERIFY_NO_MATCH)
        self.assertEqual(len(reader.starts()), 1)

    def test_attempt_bound_cannot_exceed_the_pam_default(self):
        for attempts in (0, core.MAX_VERIFY_ATTEMPTS + 1, True, 2.0):
            with self.subTest(attempts=attempts), self.assertRaises(core.UsageError):
                self.verify(self.reader(), timeout=45, attempts=attempts)

    def test_cancel_stops_the_scan_and_releases_the_reader(self):
        reader = self.reader((30.0, ["verify-match"]))
        state = {"cancel": False}
        reader.during_wait = lambda: state.__setitem__("cancel", True)
        session = self.verify(reader, timeout=45, attempts=3, cancelled=lambda: state["cancel"])
        self.assertTrue(session.cancelled)
        self.assertFalse(session.succeeded)
        self.assertEqual(self.clock[0], 100.0)   # stopped at once, not after the 30 s touch
        self.assertEqual([call[0] for call in reader.calls][-2:], ["VerifyStop", "Release"])
        self.assertFalse(reader.claimed)


@unittest.skipUnless(HAVE_NACL, "python3-nacl is not installed")
class AgentDefaultUnlockTests(ClockTestCase):
    """The custodian path with the real fprintd client over the fake reader."""

    def setUp(self):
        super().setUp()
        lockermod = importlib.import_module("fingerlock_locker")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.uid = os.getuid()
        self.session = agentmod.CallerSession("3", self.uid, True, "active", "seat0", False, False)
        self.custody = agentmod.Custody(now=lambda: 1000.0, min_ttl=0, sleep_offset=lambda: 0.0)
        self.addCleanup(self.custody.close)
        self.approvals = []
        self.reader_obj = None
        with unittest.mock.patch.object(agentmod, "_harden_process", return_value=agentmod.HardenReport()):
            self.agent = agentmod.Agent(
                custody=self.custody, client_factory=self.client, custody_watch=False,
                session_lookup=lambda pid: self.session, session_state=lambda sid: self.session,
                approve=lambda uid, pid, binding, alive: self.approvals.append(binding))
        locker = lockermod.Locker(Path(self.tmp.name) / "locker", kdf_policy="test")
        info = locker.init("correct horse battery", opslimit=1, memlimit=8192)
        self.key = locker.derive_private_key("correct horse battery")
        self.locker_id = info.locker_id
        self.payload = b"fixture plaintext\n"
        self.blob = lockermod.encrypt_container(self.payload, info, info.public_key, name="note.flk")
        self.binding = lockermod.binding_for("password-get", "note", self.blob)

    def client(self):
        bus = FakeBus(self.reader_obj)

        class Client(core.FprintdClient):
            def default_device(self_inner):
                return core.DeviceInfo(PATH, "Synaptics Sensors", "press", 9, False, False)
        return Client(bus)

    def operate(self):
        self.agent.handle({"protocol": 1, "op": "deposit", "key": self.key.hex(),
                           "locker_id": self.locker_id, "ttl": 600}, self.uid, 4242)
        return self.agent.handle({"protocol": 1, "op": "operate", "locker_id": self.locker_id,
                                  "finger": "any", "body_bytes": len(self.blob), "binding": self.binding},
                                 self.uid, 4242, self.blob)

    def test_first_touch_rejected_second_matches_returns_the_plaintext(self):
        self.reader_obj = self.reader((1.5, ["verify-no-match"]), (1.0, ["verify-match"]))
        reply, plain = self.operate()
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(plain, self.payload)
        self.assertEqual(reply["plain_sha256"], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(self.approvals, [self.binding])
        self.assertEqual(self.reader_obj.starts(), ["right-index-finger"] * 2)
        self.assertEqual(reply["scans"], 2)

    def test_three_rejections_are_no_match_and_release_nothing(self):
        self.reader_obj = self.reader(*[(1.0, ["verify-no-match"])] * 4)
        reply, plain = self.operate()
        self.assertEqual(reply["code"], "ENOMATCH")
        self.assertEqual(plain, b"")
        self.assertEqual(len(self.reader_obj.starts()), 3)
        self.assertNotIn(self.key.hex(), json.dumps(reply))

    def test_forget_during_a_scan_cancels_it_and_releases_nothing(self):
        self.reader_obj = self.reader((30.0, ["verify-match"]))
        self.reader_obj.during_wait = lambda: self.custody.forget(self.uid)
        reply, plain = self.operate()
        self.assertEqual(reply["code"], "EACCESS")
        self.assertIn("revoked during verification", reply["error"])
        self.assertEqual(plain, b"")
        self.assertEqual(len(self.reader_obj.starts()), 1)
        self.assertFalse(self.reader_obj.claimed)


class PromptTests(unittest.TestCase):
    def test_unlock_prompt_asks_for_approval_before_the_touch(self):
        fl = importlib.import_module("fingerlock")
        text = fl._touch_prompt("unlock", {"name": "a.flk", "sha256": "c" * 64})
        self.assertTrue(text.startswith("approve the request to unlock a.flk (cccccccccccc), then touch"))
        self.assertIn(f"up to {agentmod.VERIFY_ATTEMPTS} scans", text)


class ApprovalCancelTests(unittest.TestCase):
    binding = {"kind": "password-get", "name": "bank \"main\"", "size": 9, "sha256": "b" * 64}

    def test_dismissed_dialog_is_reported_as_cancelled(self):
        check = unittest.mock.Mock(side_effect=[policy.PKCHECK_CHALLENGE, policy.PKCHECK_DISMISSED])
        with self.assertRaises(core.AccessDenied) as caught:
            policy.authorize_operation(os.getuid(), os.getpid(), self.binding, lambda: True, run_check=check)
        self.assertEqual(caught.exception.message, "locker operation approval was cancelled")

    def test_refusal_is_not_reported_as_cancel(self):
        check = unittest.mock.Mock(side_effect=[policy.PKCHECK_CHALLENGE, 1])
        with self.assertRaises(core.AccessDenied) as caught:
            policy.authorize_operation(os.getuid(), os.getpid(), self.binding, lambda: True, run_check=check)
        self.assertEqual(caught.exception.message, "locker operation approval was refused")

    def test_dialog_shows_the_operation_before_interaction(self):
        shown = []
        def check(argv, alive, timeout):
            shown.append(argv)
            return policy.PKCHECK_DISMISSED if "--allow-user-interaction" in argv else policy.PKCHECK_CHALLENGE
        with self.assertRaises(core.AccessDenied):
            policy.authorize_operation(os.getuid(), os.getpid(), self.binding, lambda: True, run_check=check)
        message = shown[1][shown[1].index("polkit.message") + 1]
        for detail in ("approve password-get", f"process {os.getpid()}", 'Requested name: "bank \\"main\\""',
                       "Container bytes: 9", "SHA-256: " + "b" * 64):
            self.assertIn(detail, message)

    @unittest.skipUnless(HAVE_NACL, "python3-nacl is not installed")
    def test_cancelled_approval_never_reaches_the_reader_and_keeps_custody(self):
        lockermod = importlib.import_module("fingerlock_locker")
        uid = os.getuid()
        session = agentmod.CallerSession("3", uid, True, "active", "seat0", False, False)
        custody = agentmod.Custody(now=lambda: 1000.0, min_ttl=0, sleep_offset=lambda: 0.0)
        self.addCleanup(custody.close)
        reader_calls = []

        def cancel(uid_, pid, binding, alive):
            check = unittest.mock.Mock(side_effect=[policy.PKCHECK_CHALLENGE, policy.PKCHECK_DISMISSED])
            policy.authorize_operation(os.getuid(), os.getpid(), binding, alive, run_check=check)

        with unittest.mock.patch.object(agentmod, "_harden_process", return_value=agentmod.HardenReport()):
            agent = agentmod.Agent(custody=custody, client_factory=lambda: reader_calls.append(1),
                                   custody_watch=False,
                                   session_lookup=lambda pid: session, session_state=lambda sid: session,
                                   approve=cancel)
        with tempfile.TemporaryDirectory() as tmp:
            locker = lockermod.Locker(Path(tmp) / "locker", kdf_policy="test")
            info = locker.init("correct horse battery", opslimit=1, memlimit=8192)
            key = locker.derive_private_key("correct horse battery")
            blob = lockermod.encrypt_container(b"x", info, info.public_key, name="a.flk")
        binding = lockermod.binding_for("unlock", "a.flk", blob)
        agent.handle({"protocol": 1, "op": "deposit", "key": key.hex(), "locker_id": info.locker_id,
                      "ttl": 600}, uid, 4242)
        reply, plain = agent.handle({"protocol": 1, "op": "operate", "locker_id": info.locker_id,
                                     "finger": "any", "body_bytes": len(blob), "binding": binding},
                                    uid, 4242, blob)
        self.assertEqual(reply["code"], "EACCESS")
        self.assertIn("cancelled", reply["error"])
        self.assertEqual(plain, b"")
        self.assertEqual(reader_calls, [])
        status, _ = agent.handle({"protocol": 1, "op": "status"}, uid, 4242)
        self.assertTrue(status["held"])


if __name__ == "__main__":
    unittest.main()
