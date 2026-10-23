"""Bounded synthetic receipt/store regressions; never starts a broker or agent."""
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
import kilix_pty_selector as api


class FixtureBroker(pty.Broker):
    def __init__(self, runtime):
        super().__init__("/tmp/unused-fixture-agent", str(runtime), "0.3", 1)
        self.calls = []
        self.answer = (0, b"", "")

    def call(self, *args):
        self.calls.append(args)
        return self.answer


class ReceiptData(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="receipt-data-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "runtime").mkdir(mode=0o700)
        self.environment = mock.patch.dict(os.environ, {"KITTY_PTY_BROKER_SESSION": "fixture-caller"})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.broker = FixtureBroker(self.root / "runtime")
        self.context = api.capture(self.broker)
        self.request = {"schema": api.REQUEST_SCHEMA, "verb": "end", "operation_id": "fixture-key",
                        "selector": {"id": "target-7", "attached": False}}
        self.binding = {"id": "target-7", "started_millis": 123, "attached": False,
                        "broker_pid": 51, "child_pid": 52, "boot_id": "11111111-1111-4111-8111-111111111111",
                        "start_ticks": 100, "child_start_ticks": 101}

    def census(self, count=1):
        return {"complete": True, "census_count": count, "matches": count, "possible": 0,
                "candidates": [{"id": self.binding["id"], "reachable": True, "possible": False}] if count else [],
                "candidates_truncated": False}

    def record(self, phase="done", sent=False):
        result = api.receipt(self.broker, self.request, self.context,
                             "verified_absent" if sent else "refused", reason=None if sent else "not_found",
                             sent=sent, resolved=self.binding if sent else None, census=self.census() if sent else None,
                             completion="verified_absent" if sent else None)
        return {"namespace": api.SCHEMA, "phase": phase, "operation_id": self.request["operation_id"],
                "request": copy.deepcopy(self.request), "context": copy.deepcopy(self.context),
                "fingerprint": api.key_for(self.request, self.context),
                "binding": copy.deepcopy(self.binding) if sent or phase in ("resolved", "dispatch") else None,
                "receipt": copy.deepcopy(result) if phase == "done" else None, "updated_millis": 1234}

    def put(self, record):
        store = api.Store(str(self.root / "state"))
        with store.locked():
            store.write(self.request["operation_id"], record)
        return store

    def wire(self):
        return {"schema": "kitty-pty-selector/v1", "operation": "resolve", "selector": copy.deepcopy(self.request["selector"]),
                "caller": self.context["caller"], "result": "unique", "reason": "none", "complete": True,
                "census_count": 1, "matches": 1, "possible": 0, "actionable": True,
                "request_sent": False, "receiver_refused": False,
                "candidates": self.census()["candidates"], "candidates_truncated": False,
                "resolved": copy.deepcopy(self.binding)}

    def broker_doc(self, doc, verb="resolve", code=0, binding=None):
        self.broker.answer = (code, json.dumps(doc).encode(), "")
        return api.broker_document(self.broker, self.request, verb, binding)[0]

    def test_reported_malformed_done_receipt_never_replays_success_or_starts_an_operation(self):
        record = self.record()
        record["receipt"] = {"result": "verified_absent", "request_sent": False, "resolved": None}
        store = self.put(record)
        original = Path(store.path(self.request["operation_id"])).read_bytes()
        for _ in range(2):
            code, result = api.execute(self.broker, self.request, True, str(self.root / "state"))
            self.assertEqual((code, result["result"], result.get("request_sent"), result.get("attempt_sent")),
                             (1, "uncertain", None, False))
            self.assertEqual(result["completion"], "unknown")
            self.assertFalse(self.broker.calls)
        self.assertEqual(Path(store.path(self.request["operation_id"])).read_bytes(), original)

    def test_unknown_send_history_has_unknown_completion_independently_of_review(self):
        doc = api.receipt(self.broker, self.request, self.context, "uncertain", sent=None, reason="in_progress")
        self.assertEqual((doc["request_sent"], doc["completion"]), (None, "unknown"))
        self.assertEqual(api.receipt(self.broker, self.request, self.context, "refused")["completion"], "not_sent")

    def test_broker_unique_requires_actionable_resolved_selection(self):
        doc = self.wire(); doc.update(actionable=False, resolved=None)
        self.assertIsNone(self.broker_doc(doc))

    def test_valid_done_records_and_all_nonterminal_phases_are_readable(self):
        for phase, sent in (("reserved", False), ("resolved", False), ("dispatch", False), ("done", False), ("done", True)):
            with self.subTest(phase=phase, sent=sent):
                record = self.record(phase, sent)
                self.assertEqual(self.put(record).read(self.request["operation_id"]), record)
        before = len(self.broker.calls)
        code, result = api.execute(self.broker, self.request, True, str(self.root / "state"))
        self.assertEqual((code, result["result"], result["duplicate"]), (0, "verified_absent", True))
        self.assertEqual(len(self.broker.calls), before)

    def test_malformed_record_relationships_are_failures_not_fresh_admission(self):
        changes = [
            lambda r: r.update(fingerprint="0" * 64),
            lambda r: r["request"].update(operation_id="different-key"),
            lambda r: r["request"]["selector"].update(id="different-id"),
            lambda r: r["context"]["caller"].update(id="../invalid"),
            lambda r: r["context"]["runtime"].update(directory_identity=[True, 7]),
            lambda r: r["context"].update(timeout_seconds=True),
            lambda r: r["binding"].update(started_millis=True),
            lambda r: r["binding"].update(id="../invalid"),
            lambda r: r["binding"].update(attached=True),
            lambda r: r["receipt"].update(operation_id="different-key"),
            lambda r: r["receipt"]["caller"].update(id="different-caller"),
            lambda r: r["receipt"]["runtime_context"].update(path="/tmp/different-runtime"),
            lambda r: r["receipt"]["resolved"].update(started_millis=124),
            lambda r: r["receipt"].update(request_sent=False),
            lambda r: r["receipt"]["selector"].update(attached=0),
            lambda r: r["receipt"]["census"].update(candidates=[None]),
        ]
        for i, change in enumerate(changes):
            with self.subTest(case=i):
                record = self.record(sent=True); change(record); self.put(record)
                code, result = api.execute(self.broker, self.request, True, str(self.root / "state"))
                self.assertEqual((code, result["result"], result["request_sent"]), (1, "uncertain", None))
                self.assertFalse(self.broker.calls)
        for phase in ("reserved", "resolved", "dispatch"):
            with self.subTest(phase=phase):
                record = self.record(phase)
                record.update(binding=self.binding if phase == "reserved" else None)
                self.put(record)
                self.assertEqual(api.execute(self.broker, self.request, True, str(self.root / "state"))[0], 1)
                self.assertFalse(self.broker.calls)

    def test_broker_outcome_binding_candidates_and_duplicate_fields_are_checked(self):
        changes = [lambda d: d.update(result="accepted"), lambda d: d.update(reason="transport_error"),
                   lambda d: d["selector"].update(attached=0),
                   lambda d: d.update(actionable=False), lambda d: d.update(request_sent=True),
                   lambda d: d.update(receiver_refused=True), lambda d: d.update(candidates_truncated=True),
                   lambda d: d["resolved"].update(id="../invalid"), lambda d: d["resolved"].update(id="different-id"),
                   lambda d: d["resolved"].update(attached=True), lambda d: d.update(candidates=[None]),
                   lambda d: d["candidates"][0].update(reachable=False),
                   lambda d: d["candidates"][0].update(id="different-id")]
        self.assertIsNotNone(self.broker_doc(self.wire()))
        for i, change in enumerate(changes):
            with self.subTest(case=i):
                doc = self.wire(); change(doc); self.assertIsNone(self.broker_doc(doc))
        raw = json.dumps(self.wire())[:-1] + ',"result":"unique"}'
        self.broker.answer = (0, raw.encode(), "")
        self.assertIsNone(api.broker_document(self.broker, self.request, "resolve")[0])

    def test_end_ack_receiver_refusal_and_uncertainty_require_original_binding(self):
        for result, reason, code, rejected in (("accepted", "none", 0, False),
                                              ("refused", "attachment_mismatch", 3, True),
                                              ("uncertain", "terminate_timed_out", 1, False)):
            doc = self.wire(); doc.update(operation="end", result=result, reason=reason,
                                          request_sent=True, receiver_refused=rejected)
            self.assertIsNotNone(self.broker_doc(doc, "end", code, self.binding))
            for change in (lambda d: d.update(request_sent=False), lambda d: d.update(resolved=None),
                           lambda d: d["resolved"].update(started_millis=124)):
                bad = copy.deepcopy(doc); change(bad)
                self.assertIsNone(self.broker_doc(bad, "end", code, self.binding))

    def test_invalid_resolve_is_cached_without_end_and_invalid_end_preserves_uncertainty(self):
        invalid = self.wire(); invalid["resolved"]["id"] = "different-id"
        self.broker.answer = (0, json.dumps(invalid).encode(), "")
        for duplicate in (False, True):
            code, doc = api.execute(self.broker, self.request, True, str(self.root / "state"))
            self.assertEqual((code, doc["result"], doc["request_sent"], doc["duplicate"]), (3, "refused", False, duplicate))
        self.assertEqual([args[0] for args in self.broker.calls], ["resolve"])
        self.request["operation_id"] = "after-send"
        accepted = self.wire(); accepted.update(operation="end", result="accepted", request_sent=True, candidates=[None])
        with mock.patch.object(self.broker, "call", side_effect=[(0, json.dumps(self.wire()).encode(), ""),
                                                               (0, json.dumps(accepted).encode(), "")]) as calls, \
                mock.patch.object(api, "verify_absence", side_effect=AssertionError("invalid ACK cannot authorize verification")):
            code, doc = api.execute(self.broker, self.request, True, str(self.root / "state"))
            self.assertEqual((code, doc["result"], doc["request_sent"]), (1, "uncertain", True))
            self.assertEqual(calls.call_count, 2)
            replay = api.execute(self.broker, self.request, True, str(self.root / "state"))[1]
            self.assertEqual((replay["duplicate"], replay["completion"]), (True, "unknown"))
            self.assertEqual(calls.call_count, 2)

    def test_malformed_history_cannot_be_pruned_or_duplicate_fields_treated_as_absence(self):
        record = self.record()
        record["receipt"] = {"result": "verified_absent", "request_sent": False, "resolved": None}
        store = self.put(record)
        path = Path(store.path(self.request["operation_id"]))
        os.utime(path, (time.time() - api.legacy.UNCERTAIN_TTL - 10,) * 2)
        self.assertFalse(store.prunable(path.name, time.time()))
        raw = json.dumps(self.record())[:-1] + ',"phase":"done"}'
        path.write_text(raw)
        self.assertEqual(api.execute(self.broker, self.request, True, str(self.root / "state"))[0], 1)
        self.assertFalse(self.broker.calls)

    def test_valid_bound_refusal_interruption_and_timestamp_change_do_not_require_unique_current_census(self):
        for result, reason, sent, observed in (("refused", "started_mismatch", False, None),
                                              ("uncertain", "interrupted", True, None),
                                              ("refused", "cannot_bind", True, self.census())):
            record = self.record(sent=True)
            record["receipt"] = api.receipt(self.broker, self.request, self.context, result, reason=reason, sent=sent,
                                            rejected=reason == "cannot_bind", resolved=copy.deepcopy(self.binding), census=observed)
            self.assertEqual(self.put(record).read(self.request["operation_id"]), record)
        record = self.record("resolved")
        record["receipt"] = api.receipt(self.broker, self.request, self.context, "refused", reason="interrupted_before_dispatch",
                                        resolved=copy.deepcopy(self.binding), receipt_durable=True)
        record["phase"] = "done"
        self.assertEqual(self.put(record).read(self.request["operation_id"]), record)
        record = self.record(sent=True)
        record["receipt"].update(result="uncertain", completion="unknown", reason="interrupted")
        record["receipt"].pop("request_sent")
        store = self.put(record)
        with self.assertRaises(ValueError): store.read(self.request["operation_id"])

    def test_valid_broker_nonactionable_previews_and_changed_binding_refusal(self):
        request = copy.deepcopy(self.request); request["selector"] = {"startup_words": ["literal", "word"]}
        for reachable, possible in ((False, False), (False, True), (True, True)):
            doc = self.wire(); doc.update(selector=request["selector"], result="unresolved", resolved=None, actionable=False,
                                          matches=0 if possible else 1, possible=1 if possible else 0)
            doc["candidates"][0].update(reachable=reachable, possible=possible)
            self.broker.answer = (0, json.dumps(doc).encode(), "")
            self.assertIsNotNone(api.broker_document(self.broker, request, "resolve")[0])
        doc = self.wire(); doc.update(operation="end", result="refused", reason="started_mismatch")
        doc["resolved"]["started_millis"] = 124
        self.assertIsNotNone(self.broker_doc(doc, "end", 3, self.binding))
        doc = self.wire(); doc.update(result="not_found", actionable=False, resolved=None, matches=0,
                                      candidates=[], census_count=0)
        self.assertIsNotNone(self.broker_doc(doc))
        doc.pop("resolved")
        self.assertIsNone(self.broker_doc(doc))


if __name__ == "__main__":
    unittest.main()
