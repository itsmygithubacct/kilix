"""Structured actions: exact identities, truthful evidence and no replay."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import agent_actions as actions
import agent_control as control
import agent_delivery as delivery
from test_agent_control import FakeClient, SOURCE_BROKER, TARGET_BROKER


def request(operation="pane.open", operation_id="open-1"):
    params = {"argv": ["/bin/printf", "$(do-not-evaluate); literal"], "cwd": "/tmp",
              "title": "worker", "placement": "split"}
    if operation == "agent.launch":
        params.pop("argv")
        params["agent"] = "codex"
    elif operation == "agent.deliver":
        params = {"text": "Please review the changes"}
    elif operation == "operation.status":
        params = {}
    return {"schema": actions.SCHEMA, "operation_id": operation_id, "operation": operation,
            "source": {"pane_id": 1, "broker": SOURCE_BROKER},
            "target": {"pane_id": 2, "broker": TARGET_BROKER}, "params": params}


class ActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = FakeClient()
        self.client.command = ["kitten", "@", "--to", "unix:@kilix-test", "--password-file", "/private"]

    def dispatch(self, value):
        return actions.dispatch(value, self.client)

    def test_open_literal_argv_exact_broker_and_truthful_receipt(self):
        value = request()
        result = self.dispatch(value)
        self.assertEqual(result["status"], "created")
        self.assertTrue(result["pane_created_verified"])
        self.assertEqual(result["evidence"]["pane"]["pane_id"], 3)
        for field in ("agent_startup_verified", "delivery_verified", "acknowledgment_verified", "completion_verified"):
            self.assertFalse(result[field])
        launch = [args for args, _ in self.client.calls if args[0] == "launch" and "--help" not in args][0]
        self.assertEqual(launch[launch.index("--") + 1:], value["params"]["argv"])
        self.assertEqual(launch[launch.index("--match") + 1], "env:KITTY_PTY_BROKER_SESSION=" + TARGET_BROKER)
        self.assertEqual(result["target"], value["target"])

    def test_repeated_id_never_creates_second_pane(self):
        value = request()
        first = self.dispatch(value)
        second = self.dispatch(value)
        self.assertEqual(second["status"], "created")
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["evidence"], first["evidence"])
        self.assertEqual(len(self.client.panes), 3)
        value["params"]["title"] = "different"
        refused = self.dispatch(value)
        self.assertEqual(refused["status"], "blocked")
        self.assertIn("different request", refused["error"])
        self.assertEqual(len(self.client.panes), 3)

    def test_wrong_target_source_or_inherited_caller_blocks_before_launch(self):
        for field in ("target", "source"):
            value = request()
            value[field]["broker"] = "d" * 16
            self.assertEqual(self.dispatch(value)["status"], "blocked")
        value = request()
        value["source"] = copy.deepcopy(value["target"])
        self.assertIn("inherited caller", self.dispatch(value)["error"])
        self.assertEqual(self.client.calls, [])

    def test_source_change_immediately_before_launch_has_no_mutation(self):
        original_run = self.client.run
        def run(args, payload=None):
            result = original_run(args, payload)
            if args == ["launch", "--help"]:
                self.client.panes[0]["env"]["KITTY_PTY_BROKER_SESSION"] = "e" * 16
            return result
        self.client.run = run
        result = self.dispatch(request())
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(self.client.panes), 2)

    def test_timeout_after_mutation_is_durable_uncertain_without_relaunch(self):
        original_run = self.client.run
        def run(args, payload=None):
            result = original_run(args, payload)
            if args[0] == "launch" and "--help" not in args:
                raise control.ControlError("terminal request timed out")
            return result
        self.client.run = run
        first = self.dispatch(request())
        self.assertEqual(first["status"], "uncertain")
        second = self.dispatch(request())
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["status"], "uncertain")
        self.assertEqual(len(self.client.panes), 3)
        status = self.dispatch(request("operation.status"))
        self.assertEqual(status["status"], "uncertain")
        self.assertFalse(status["pane_created_verified"])

    def test_pending_intent_from_process_death_is_observation_only(self):
        value = request()
        normalized = actions.validate(value)
        digest = actions.hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with actions.ledger(self.client, value["operation_id"]) as store:
            store.write({"phase": "intent", "digest": digest, "receipt": actions.receipt(normalized, "uncertain")})
        result = self.dispatch(value)
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(result["duplicate"])
        status = self.dispatch(request("operation.status"))
        self.assertEqual(status["status"], "uncertain")
        self.assertIn("no action was repeated", status["error"])
        self.assertEqual(self.client.calls, [])

    def test_status_reads_intent_while_operation_lock_is_held(self):
        value = request()
        with actions.ledger(self.client, value["operation_id"]) as store:
            store.write({"phase": "intent", "digest": "a" * 64,
                         "receipt": actions.receipt(value, "uncertain")})
            status = self.dispatch(request("operation.status"))
            self.assertEqual(status["status"], "uncertain")
            duplicate = self.dispatch(value)
            self.assertEqual(duplicate["status"], "blocked")
            self.assertIn("active", duplicate["error"])
            independent = self.dispatch(request(operation_id="independent"))
            self.assertEqual(independent["status"], "created")
            store.write({"phase": "checked", "digest": "a" * 64,
                         "receipt": actions.receipt(value)})
            self.assertEqual(self.dispatch(request("operation.status"))["status"], "pending")

    def test_malformed_receipt_and_false_completion_are_refused_as_json(self):
        records = [{"phase": "intent", "digest": "a" * 64, "receipt": None}]
        for claim in ("acknowledgment_verified", "completion_verified", "agent_startup_verified"):
            saved = actions.receipt(request(), "blocked")
            saved[claim] = True
            records.append({"phase": "final", "digest": "a" * 64, "receipt": saved})
        for record in records:
            with actions.ledger(self.client, "open-1") as store:
                store.write(record)
            result = self.dispatch(request("operation.status"))
            self.assertIn(result["status"], ("blocked", "uncertain"))
            self.assertIn("error", result)
            self.assertFalse(result["completion_verified"])
            self.assertFalse(result["acknowledgment_verified"])
        self.assertEqual(self.client.calls, [])

    def test_status_retains_historical_receipt_when_recipient_gone(self):
        created = self.dispatch(request())
        self.client.panes = [self.client.panes[0]]
        result = self.dispatch(request("operation.status"))
        self.assertEqual(result, created)
        wrong = request("operation.status")
        wrong["target"]["broker"] = "f" * 16
        self.assertEqual(self.dispatch(wrong)["status"], "blocked")

    def test_missing_status_is_read_only(self):
        result = self.dispatch(request("operation.status"))
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_plan_does_not_persist_or_reserve_operation_id(self):
        value = request()
        value["dry_run"] = True
        result = self.dispatch(value)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["pane_created_verified"])
        self.assertEqual(len(self.client.panes), 2)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        value["dry_run"] = False
        self.assertEqual(self.dispatch(value)["status"], "created")

    def test_agent_launch_uses_existing_allowlist_and_argv(self):
        value = request("agent.launch")
        value["params"]["model"] = "literal-model"
        with mock.patch("agent_programs.resolve_agent_command", return_value="/bin/codex"):
            result = self.dispatch(value)
        self.assertEqual(result["status"], "created")
        launch = self.client.calls[-1][0]
        self.assertEqual(launch[launch.index("--") + 1:], ["/bin/codex", "--model", "literal-model"])
        self.assertFalse(result["agent_startup_verified"])

    def test_delivery_maps_submission_without_false_completion_and_no_replay(self):
        value = request("agent.deliver")
        def submit(client, args):
            client.input(args.pane, args.expect_broker, b"message")
            return {"status": "submitted", "delivery_verified": True, "verification": "codex-screen-v1", "verified_at": 42}
        with mock.patch.object(delivery, "deliver", side_effect=submit) as send:
            result = self.dispatch(value)
            repeated = self.dispatch(value)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result["status"], "submitted")
        self.assertTrue(result["delivery_verified"])
        self.assertTrue(repeated["duplicate"])
        self.assertFalse(result["acknowledgment_verified"])
        self.assertFalse(result["completion_verified"])

    def test_invalid_schema_fields_and_types_never_connect(self):
        cases = []
        for key, value in (("unknown", True), ("timeout", True), ("timeout", float("nan")), ("dry_run", "yes")):
            item = request()
            item[key] = value
            cases.append(item)
        item = request("agent.deliver")
        item["params"]["text"] = 42
        cases.append(item)
        item = request()
        item["target"]["extra"] = "secret"
        cases.append(item)
        for item in cases:
            with self.subTest(item=item):
                with mock.patch.object(control, "Client") as client:
                    result = actions.dispatch(item)
                self.assertEqual(result["status"], "blocked")
                client.assert_not_called()
        self.assertEqual(self.client.calls, [])

    def test_invalid_huge_metadata_cannot_reflect_unbounded_output(self):
        value = request()
        value["operation_id"] = "x" * 100000
        value["operation"] = ["secret"] * 100000
        value["source"] = {"secret": "y" * 100000}
        result = self.dispatch(value)
        self.assertLess(len(json.dumps(result)), 1024)
        self.assertNotIn("secret", json.dumps(result))

    def test_shared_deadline_reaches_resolve_snapshot_and_run(self):
        value = actions.validate(request())
        value["timeout"] = 1
        seen = []
        original_snapshot = self.client.snapshot
        def snapshot():
            seen.append(self.client.deadline)
            return original_snapshot()
        self.client.snapshot = snapshot
        guarded = actions.GuardedClient(self.client, value, lambda: None)
        guarded.verify()
        guarded.snapshot()
        self.assertEqual(seen, [guarded.deadline] * 3)
        self.assertEqual(self.client.deadline, float("inf"))
        guarded.deadline = time.monotonic() - 1
        with self.assertRaises(control.ControlError):
            guarded.snapshot()
        self.assertEqual(len(seen), 3)

    def test_hanging_client_help_respects_action_deadline(self):
        executable = Path(self.temp.name) / "codex-help-fixture"
        executable.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n")
        executable.chmod(0o700)
        value = request("agent.launch")
        value["params"]["prompt"] = "Review this code"
        value["timeout"] = 1
        started = time.monotonic()
        with mock.patch("agent_programs.resolve_agent_command", return_value=str(executable)):
            result = self.dispatch(value)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(self.client.panes), 2)
        self.assertIn("help", result["error"])

    def test_grok_repository_check_receives_remaining_deadline(self):
        def fail(*args, **kwargs):
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 1)
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        with mock.patch.object(control.subprocess, "run", side_effect=fail):
            with self.assertRaises(control.ControlError):
                control.grok_trust_is_exact(Path("/tmp"), deadline=time.monotonic() + 1)
        self.assertEqual(len(self.client.panes), 2)

    def test_folder_trust_requires_explicit_existing_setup(self):
        value = request("agent.launch")
        value["params"].update(agent="claude", trust_folder=True)
        value["timeout"] = 1
        with mock.patch("agent_programs.resolve_agent_command", return_value="/bin/claude"), \
                mock.patch("agent_trust.trust") as trust:
            result = self.dispatch(value)
        trust.assert_not_called()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("folder trust", result["error"])

    def test_receipts_private_and_symlink_state_refused(self):
        self.assertEqual(self.dispatch(request())["status"], "created")
        path = Path(self.temp.name) / "kilix/agent-actions"
        self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        for child in path.iterdir():
            self.assertEqual(child.stat().st_mode & 0o777, 0o600)
        unsafe = Path(self.temp.name) / "other"
        unsafe.mkdir()
        (unsafe / "kilix").symlink_to(Path(self.temp.name) / "kilix")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(unsafe)}):
            refused = self.dispatch(request(operation_id="different"))
        self.assertEqual(refused["status"], "blocked")
        self.assertEqual(len(self.client.panes), 3)

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(control.ControlError):
            json.loads('{"schema":"first","schema":"second"}', object_pairs_hook=actions.unique_object)


if __name__ == "__main__":
    unittest.main()
