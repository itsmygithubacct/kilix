"""Synthetic census/recheck receipts; all broker calls are dictionary counters."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import kilix_pty as pty
import kilix_pty_selector as api


class CounterBroker(pty.Broker):
    def __init__(self, runtime):
        super().__init__("/unused-synthetic-broker", str(runtime), "0.4", 1)
        self.calls, self.answers = [], []

    def call(self, *args):
        self.calls.append(args)
        if not self.answers:
            raise AssertionError("unexpected fake broker call")
        return self.answers.pop(0)


def response(document, code):
    return code, json.dumps(document).encode(), ""


class RecheckReceipts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="synthetic-recheck-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        runtime = self.root / "runtime"
        runtime.mkdir(mode=0o700)
        self.end = json.loads((Path(__file__).parent / "fixtures/selector-unsent-recheck.json").read_text())
        self.request = {"schema": api.REQUEST_SCHEMA, "verb": "end", "selector": copy.deepcopy(self.end["selector"]),
                        "operation_id": "-key", "timeout_seconds": 0.4}
        self.binding = copy.deepcopy(self.end["resolved"])
        self.environment = mock.patch.dict(os.environ, {"KITTY_PTY_BROKER_SESSION": self.end["caller"]["id"]})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.broker = CounterBroker(runtime)
        self.context = api.capture(self.broker)
        self.verification = mock.patch.object(api, "verify_absence", side_effect=AssertionError("unexpected absence verification"))
        self.verify = self.verification.start(); self.addCleanup(self.verification.stop)
        self.process = mock.patch.object(api, "process_absent", side_effect=AssertionError("unexpected process inspection"))
        self.process.start(); self.addCleanup(self.process.stop)

    def preview(self):
        document = copy.deepcopy(self.end)
        document.update(operation="resolve", result="unique", reason="none")
        return document

    def parse(self, document, code=3, verb="end", original=None):
        self.broker.answers = [response(document, code)]
        return api.broker_document(self.broker, self.request, verb,
                                   self.binding if original is None and verb == "end" else original)[0]

    def exercise(self, document=None):
        self.broker.calls = []
        self.broker.answers = [response(self.preview(), 0), response(self.end if document is None else document, 3)]
        state = self.root / "state"
        store = api.Store(str(state))
        first = api.execute(self.broker, self.request, True, str(state))
        retained = store.read(self.request["operation_id"])
        before = Path(store.path(self.request["operation_id"])).read_bytes()
        first_calls = len(self.broker.calls)
        replay = api.execute(self.broker, self.request, True, str(state))
        self.assertEqual(Path(store.path(self.request["operation_id"])).read_bytes(), before)
        self.assertEqual([args[0] for args in self.broker.calls], ["resolve", "end"])
        self.assertEqual(len(self.broker.calls), first_calls)
        self.assertFalse(self.broker.answers)
        self.verify.assert_not_called()
        return {"first": {"code": first[0], "receipt": first[1]},
                "replay": {"code": replay[0], "receipt": replay[1]}, "retained": retained,
                "fake_calls": [list(args) for args in self.broker.calls], "new_replay_calls": 0,
                "verification_calls": self.verify.call_count, "retained_bytes_unchanged": True}

    def test_independent_f1_dictionary_is_a_valid_unsent_recheck_refusal(self):
        self.assertEqual(self.parse(self.end), self.end)

    def test_first_answer_and_retained_replay_preserve_known_unsent_refusal(self):
        observation = self.exercise()
        for name, duplicate in (("first", False), ("replay", True)):
            with self.subTest(answer=name):
                answer = observation[name]; document = answer["receipt"]
                self.assertEqual((answer["code"], document["result"], document["request_sent"],
                                  document["receiver_refused"], document["completion"], document["reason"], document["duplicate"]),
                                 (3, "refused", False, False, "not_sent", "unresolved", duplicate))
                self.assertEqual(document["resolved"], self.binding)
                self.assertEqual(document["census"], api.census_of(self.end))
                self.assertEqual(document["caller"], self.context["caller"])
                self.assertEqual(document["selector"], self.request["selector"])
        retained = observation["retained"]
        self.assertEqual((retained["phase"], retained["binding"]), ("done", self.binding))
        self.assertEqual(retained["receipt"], observation["first"]["receipt"])

    def test_initial_nonactionable_unresolved_census_refusals_remain_valid(self):
        for possible in (0, 1):
            with self.subTest(possible=possible):
                document = copy.deepcopy(self.end)
                document.update(actionable=False, resolved=None, matches=0, possible=possible,
                                candidates=[{"id": self.binding["id"], "reachable": False, "possible": True}] if possible else [])
                self.assertEqual(self.parse(document), document)
        document["complete"] = False
        self.assertIsNone(self.parse(document))

    def test_recheck_refusal_requires_original_incarnation_and_nonown_caller(self):
        self.request["selector"] = {"id_prefix": "Target-", "attached": False}
        document = copy.deepcopy(self.end); document["selector"] = copy.deepcopy(self.request["selector"])
        self.assertEqual(self.parse(document), document)
        for mutate in (lambda d: d["resolved"].update(started_millis=901),
                       lambda d: (d["resolved"].update(id="Target-8"), d["candidates"][0].update(id="Target-8"))):
            bad = copy.deepcopy(document); mutate(bad)
            self.assertIsNone(self.parse(bad))
            # A changed census can legitimately explain a separate binding-mismatch refusal.
            bad["reason"] = "started_mismatch"
            self.assertEqual(self.parse(bad), bad)
        for ident in ("Target-7", None):
            bad = copy.deepcopy(document)
            caller = {"source": "environment", "authenticated": False, "status": "valid" if ident else "missing", "id": ident}
            self.broker.caller = caller; bad["caller"] = caller
            self.assertIsNone(self.parse(bad))

    def test_recheck_refusal_keeps_census_selector_echo_type_and_outcome_guards(self):
        changes = [lambda d: d.update(complete=False), lambda d: d.update(census_count=0),
                   lambda d: d.update(matches=True), lambda d: d.update(possible=1),
                   lambda d: d.update(actionable=False), lambda d: d.update(resolved=None),
                   lambda d: d.update(candidates=[None]), lambda d: d.update(candidates=[]),
                   lambda d: d["candidates"][0].update(reachable=False),
                   lambda d: d["candidates"][0].update(possible=True),
                   lambda d: d["candidates"][0].update(id="Target-8"),
                   lambda d: d.update(candidates_truncated=True), lambda d: d.update(candidates_truncated=0),
                   lambda d: d["resolved"].update(started_millis=True), lambda d: d["resolved"].update(attached=True),
                   lambda d: d["selector"].update(id="Target-8"), lambda d: d["selector"].update(attached=0),
                   lambda d: d["caller"].update(id="Other-Caller"), lambda d: d.update(operation="resolve"),
                   lambda d: d.update(request_sent=True), lambda d: d.update(request_sent=0),
                   lambda d: d.update(receiver_refused=True), lambda d: d.update(receiver_refused=0),
                   lambda d: d.update(result="accepted"), lambda d: d.update(result="uncertain"),
                   lambda d: d.update(reason="unrecognized")]
        for index, change in enumerate(changes):
            with self.subTest(case=index):
                bad = copy.deepcopy(self.end); change(bad)
                self.assertIsNone(self.parse(bad))
        for code in (0, 1, True):
            with self.subTest(exit=code):
                self.assertIsNone(self.parse(self.end, code))

    def test_unique_preview_and_accepted_send_constraints_are_unchanged(self):
        preview = self.preview()
        self.assertEqual(self.parse(preview, 0, "resolve"), preview)
        preview.update(actionable=False, resolved=None)
        self.assertIsNone(self.parse(preview, 0, "resolve"))
        accepted = copy.deepcopy(self.end); accepted.update(result="accepted", reason="none", request_sent=True)
        self.assertEqual(self.parse(accepted, 0), accepted)
        accepted["request_sent"] = False
        self.assertIsNone(self.parse(accepted, 0))

    def test_contradictory_recheck_evidence_stays_uncertain_without_verification_or_retry(self):
        document = copy.deepcopy(self.end); document["resolved"]["started_millis"] += 1
        observation = self.exercise(document)
        for name, duplicate in (("first", False), ("replay", True)):
            answer = observation[name]; retained = answer["receipt"]
            self.assertEqual((answer["code"], retained["result"], retained["request_sent"], retained["completion"],
                              retained["reason"], retained["duplicate"]),
                             (1, "uncertain", True, "unknown", "missing_end_receipt", duplicate))


if __name__ == "__main__":
    unittest.main()
