"""Private record/validator fixtures; operational functions are inert or blocked."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import kilix_pty as pty
import kilix_pty_request as legacy
import kilix_pty_selector as api
import kilix_pty_selector_validation as validation


class InertBroker(pty.Broker):
    def __init__(self, runtime):
        super().__init__("/unused-inert-counter", str(runtime), "0.4", 1)
        self.calls = []
    def call(self, *args):
        self.calls.append(args)
        raise AssertionError("no broker call is allowed in this fixture")


class RetainedTimeout(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="retained-timeout-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "runtime").mkdir(mode=0o700)
        environment = mock.patch.dict(os.environ, {"KITTY_PTY_BROKER_SESSION": "synthetic-caller"})
        environment.start(); self.addCleanup(environment.stop)
        self.broker = InertBroker(self.root / "runtime")
        self.context = api.capture(self.broker)
        self.request = {"schema": api.REQUEST_SCHEMA, "verb": "end", "operation_id": "retained-key",
                        "selector": {"id": "synthetic-target"}}
        self.binding = {"id": "synthetic-target", "started_millis": 900, "attached": False}
        self.store = api.Store(str(self.root / "state"))
        for name in ("perform", "broker_document", "verify_absence", "process_absent"):
            guard = mock.patch.object(api, name, side_effect=AssertionError("operational path must stay inert: " + name))
            guard.start(); self.addCleanup(guard.stop)

    def record(self, phase="reserved", timeout=0.4, sent=False):
        context = copy.deepcopy(self.context); context["timeout_seconds"] = timeout
        census = {"complete": True, "census_count": 1, "matches": 1, "possible": 0,
                  "candidates": [{"id": self.binding["id"], "reachable": True, "possible": False}],
                  "candidates_truncated": False}
        outcome = api.receipt(self.broker, self.request, self.context, "refused" if sent is False else "uncertain",
                              sent=sent, reason="unresolved" if sent is False else "interrupted", resolved=self.binding,
                              census=census)
        return {"namespace": api.SCHEMA, "phase": phase, "operation_id": self.request["operation_id"],
                "request": copy.deepcopy(self.request), "context": context,
                "fingerprint": api.key_for(self.request, context), "updated_millis": 1000,
                "binding": None if phase == "reserved" else copy.deepcopy(self.binding),
                "receipt": outcome if phase == "done" else None}

    def put(self, value):
        with self.store.locked():
            self.store.write(value["operation_id"], value)

    def execute(self):
        return api.execute(self.broker, self.request, True, str(self.root / "state"))

    def observe_huge(self, timeout=10**400):
        self.put(self.record(timeout=timeout))
        path = Path(self.store.path(self.request["operation_id"]))
        before = path.read_bytes()
        answers = [self.execute(), self.execute()]
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.broker.calls)
        return {"answers": [{"code": code, "receipt": receipt} for code, receipt in answers],
                "broker_calls": len(self.broker.calls), "retained_bytes_unchanged": True}

    def test_huge_positive_and_negative_timeout_records_raise_history_value_error(self):
        for timeout in (10**400, -(10**400), 10**1000, -(10**1000)):
            with self.subTest(digits=len(str(timeout))):
                self.put(self.record(timeout=timeout))
                with self.assertRaises(ValueError):
                    self.store.read(self.request["operation_id"])

    def test_huge_timeout_same_key_retries_keep_unknown_history_and_bytes(self):
        for timeout in (10**400, -(10**400)):
            with self.subTest(positive=timeout > 0):
                observation = self.observe_huge(timeout)
                for answer in observation["answers"]:
                    receipt = answer["receipt"]
                    self.assertEqual((answer["code"], receipt["result"], receipt["request_sent"],
                                      receipt.get("attempt_sent"), receipt["completion"], receipt["reason"]),
                                     (1, "uncertain", None, False, "unknown", "store_unavailable"))

    def test_malformed_timeout_types_are_protected_and_never_a_fresh_operation(self):
        for timeout in (True, False, None, "0.4", [], {}, float("nan"), float("inf"), -float("inf"), 0, -1, 61, 0.09):
            with self.subTest(timeout=timeout):
                observation = self.observe_huge(timeout)
                for answer in observation["answers"]:
                    self.assertIsNone(answer["receipt"]["request_sent"])
                    self.assertIs(answer["receipt"]["attempt_sent"], False)
                path = Path(self.store.path(self.request["operation_id"]))
                self.assertFalse(self.store.prunable(path.name, time.time() + legacy.UNCERTAIN_TTL + 1))

    def test_valid_integer_and_float_timeout_bounds_still_validate(self):
        for timeout in (0.1, 0.4, 1, 1.0, 60, 60.0):
            with self.subTest(timeout=timeout):
                record = self.record(timeout=timeout); self.put(record)
                self.assertEqual(self.store.read(self.request["operation_id"]), record)

    def test_huge_malformed_history_is_protected_during_capacity_and_pruning(self):
        values = []
        for operation_id, timeout in (("retained-key", 10**400), ("negative-key", -(10**400)), ("valid-reserved", 0.4)):
            self.request["operation_id"] = operation_id
            value = self.record(timeout=timeout); self.put(value); values.append(value)
        paths = [Path(self.store.path(value["operation_id"])) for value in values]
        snapshots = {path: path.read_bytes() for path in paths}
        with mock.patch.object(legacy, "STORE_LIMIT", 3):
            for path in paths:
                self.assertFalse(self.store.prunable(path.name, time.time() + legacy.UNCERTAIN_TTL + 1))
            with self.store.locked(), self.assertRaises(legacy.Refusal) as raised:
                self.store.make_room()
            self.assertEqual(raised.exception.reason, "store_full")
            self.request["operation_id"] = "capacity-check"
            for _ in range(2):
                code, receipt = self.execute()
                self.assertEqual((code, receipt["result"], receipt["request_sent"], receipt["reason"]),
                                 (3, "refused", False, "store_full"))
        self.assertEqual({path: path.read_bytes() for path in paths}, snapshots)
        self.assertFalse(self.broker.calls)

    def test_only_valid_expired_terminal_fixture_can_be_pruned(self):
        malformed = self.record("done", timeout=10**400); self.put(malformed)
        protected = Path(self.store.path(malformed["operation_id"])); before = protected.read_bytes()
        self.request["operation_id"] = "expired-valid"
        expired = self.record("done"); self.put(expired)
        path = Path(self.store.path(expired["operation_id"]))
        old = time.time() - legacy.UNCERTAIN_TTL - 10
        os.utime(path, (old, old)); os.utime(protected, (old, old))
        self.assertTrue(self.store.prunable(path.name, time.time()))
        with mock.patch.object(legacy, "STORE_LIMIT", 2), self.store.locked():
            self.store.make_room()
        self.assertFalse(path.exists())
        self.assertEqual(protected.read_bytes(), before)

    def test_unexpected_failure_without_owner_keeps_unknown_prior_history(self):
        self.put(self.record())
        path = Path(self.store.path(self.request["operation_id"])); before = path.read_bytes()
        with mock.patch.object(api.Store, "read", side_effect=RuntimeError("synthetic unreadable history")):
            for _ in range(2):
                code, receipt = self.execute()
                self.assertEqual((code, receipt["request_sent"], receipt.get("attempt_sent"), receipt["completion"]),
                                 (1, None, False, "unknown"))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.broker.calls)

    def test_unexpected_owned_failure_uses_current_phase_and_terminal_send_knowledge(self):
        for phase, sent in (("reserved", False), ("resolved", False), ("dispatch", True),
                            ("done", False), ("done", True), ("done", None)):
            self.request["operation_id"] = "fault-" + phase + "-" + str(sent)
            def inert_failure(broker, request, context, store, record, yes):
                self.put(self.record(phase, sent=sent))
                raise RuntimeError("synthetic owned failure")
            with self.subTest(phase=phase, sent=sent), mock.patch.object(api, "perform", side_effect=inert_failure):
                code, receipt = self.execute()
                self.assertEqual((code, receipt["request_sent"], receipt["completion"]),
                                 (3 if sent is False else 1, sent, "not_sent" if sent is False else "unknown"))
                retained = self.store.read(self.request["operation_id"])
                self.assertEqual(retained["phase"], phase)
        self.assertFalse(self.broker.calls)

    def test_owned_failure_with_unreadable_or_missing_phase_keeps_possible_send(self):
        for failure in (RuntimeError("synthetic phase read failure"), None):
            self.request["operation_id"] = "phase-unknown-" + str(failure is None)
            read = api.Store.read
            reads = []
            def uncertain_read(store, operation_id):
                reads.append(operation_id)
                if len(reads) == 1:
                    return read(store, operation_id)
                if failure is not None:
                    raise failure
                return None
            with mock.patch.object(api.Store, "read", uncertain_read), \
                    mock.patch.object(api, "perform", side_effect=RuntimeError("inert failure")):
                code, receipt = self.execute()
                self.assertEqual((code, receipt["request_sent"], receipt["completion"]), (1, True, "unknown"))
        self.assertFalse(self.broker.calls)

    def test_accepted_fix2_broker_guard_and_retained_refusal_are_preserved_without_dispatch(self):
        document = json.loads((Path(__file__).parent / "fixtures/selector-unsent-recheck.json").read_bytes())
        self.request.update(selector=document["selector"], operation_id="-key", timeout_seconds=0.4)
        self.binding = document["resolved"]
        os.environ["KITTY_PTY_BROKER_SESSION"] = document["caller"]["id"]
        self.context = api.capture(self.broker)
        validation.broker(self.request, document, "end", 3, self.context["caller"], self.binding)
        self.put(self.record("done"))
        path = Path(self.store.path(self.request["operation_id"])); before = path.read_bytes()
        for _ in range(2):
            code, receipt = self.execute()
            self.assertEqual((code, receipt["result"], receipt["request_sent"], receipt["completion"], receipt["duplicate"]),
                             (3, "refused", False, "not_sent", True))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.broker.calls)
