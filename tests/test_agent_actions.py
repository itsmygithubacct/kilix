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
    def test_capabilities_advertise_codex_and_claude_delivery(self):
        self.assertEqual(actions.capabilities()['delivery_agents'], ['codex', 'claude'])
        self.assertIn('finish_staged_message_id',
                      actions.capabilities()['operations']['agent.deliver']['optional'])
        self.assertIn('previous_completed_suggestion_id',
                      actions.capabilities()['operations']['agent.deliver']['optional'])
        self.assertIn('envelope', actions.capabilities()['operations']['agent.deliver']['optional'])

    def test_envelope_is_an_opt_in_boolean_only(self):
        value = request('agent.deliver', 'env-op')
        value['params']['envelope'] = True
        self.assertIs(actions.validate(copy.deepcopy(value))['params']['envelope'], True)
        for bad in ('yes', 1, None):
            broken = copy.deepcopy(value)
            broken['params']['envelope'] = bad
            with self.subTest(bad=bad), self.assertRaises(control.ControlError):
                actions.validate(broken)

    def test_claude_structured_delivery_and_status_are_observation_only_after_submit(self):
        from test_agent_delivery import ClaudeTerminal
        self.client = ClaudeTerminal()
        value = request('agent.deliver', 'claude-action')
        result = self.dispatch(value)
        self.assertEqual(result['status'], 'submitted')
        self.assertTrue(result['delivery_verified'])
        self.assertEqual(result['evidence']['verification'], 'claude-screen-v1')
        calls = len(self.client.calls)
        recovered = self.dispatch(request('operation.status', 'claude-action'))
        self.assertEqual(recovered['status'], 'submitted')
        self.assertEqual(len(self.client.calls), calls)
        self.assertEqual(len(self.client.inputs), 2)

    def test_claude_dry_run_and_defer_refusal_send_no_input(self):
        from test_agent_delivery import ClaudeTerminal
        self.client = ClaudeTerminal()
        value = request('agent.deliver', 'claude-preview')
        value['dry_run'] = True
        result = self.dispatch(value)
        self.assertEqual(result['status'], 'planned')
        self.assertTrue(result['identity_verified'])
        value['params']['mode'] = 'defer'
        self.assertEqual(self.dispatch(value)['status'], 'blocked')
        self.assertEqual(self.client.inputs, [])

    def test_previous_suggestion_parameter_requires_fresh_steer_and_cli_is_explicit(self):
        value = request("agent.deliver", "new-message")
        value["params"] = {"text": "New body.",
                           "previous_completed_suggestion_id": "old-message"}
        normalized = actions.validate(value)
        self.assertEqual(normalized["params"]["previous_completed_suggestion_id"],
                         "old-message")
        invalid = []
        defer = copy.deepcopy(value)
        defer["params"]["mode"] = "defer"
        invalid.append(defer)
        staged = copy.deepcopy(value)
        staged["params"]["finish_staged_message_id"] = "staged-message"
        invalid.append(staged)
        dry = copy.deepcopy(value)
        dry["dry_run"] = True
        invalid.append(dry)
        same_id = copy.deepcopy(value)
        same_id["params"]["previous_completed_suggestion_id"] = "new-message"
        invalid.append(same_id)
        null_id = copy.deepcopy(value)
        null_id["params"]["previous_completed_suggestion_id"] = None
        invalid.append(null_id)
        for candidate in invalid:
            with self.subTest(params=candidate["params"], dry_run=candidate.get("dry_run")):
                with self.assertRaises(control.ControlError):
                    actions.validate(candidate)
        parsed = control.parser().parse_args([
            "deliver", "2", "--expect-broker", TARGET_BROKER,
            "--message-id", "new-message", "--text", "New body.",
            "--previous-completed-suggestion-id", "old-message"])
        self.assertEqual(parsed.previous_completed_suggestion_id, "old-message")

    def test_missing_action_scope_lookup_creates_no_state(self):
        source = {"pane_id": 1, "broker": SOURCE_BROKER}
        target = {"pane_id": 2, "broker": TARGET_BROKER}
        self.assertFalse(actions.previous_completed_suggestion_scope(
            self.client, "absent-prior", source, target))
        self.assertFalse((Path(self.temp.name) / "kilix").exists())

    def test_stdin_errors_explain_input_and_units_without_connecting(self):
        for raw in (b"", b" \n", json.dumps({**request(), "timeout": 30000}).encode()):
            with io.TextIOWrapper(io.BytesIO(raw)) as stdin, \
                    mock.patch("sys.stdin", stdin), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                    mock.patch("agent_control.Client") as client:
                self.assertEqual(actions.main(["--request-json", "-"]), 1)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["status"], "blocked")
            self.assertIn("seconds" if raw.strip() else "pipe JSON", result["error"])
            client.assert_not_called()

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

    def seed_completed_suggestion(self, message_id="previous-message"):
        from test_agent_delivery import ClaudeTerminal, claude_screen
        self.client = ClaudeTerminal()
        prior = request("agent.deliver", message_id)
        prior["params"]["text"] = "Please  review  the exact old suggestion."
        self.assertEqual(self.dispatch(prior)["status"], "submitted")
        wire = f"[kilix-message:{message_id}] {prior['params']['text']}"
        self.client.current = claude_screen(
            "\x1b[2m" + wire, history=self.client.history,
            footer=self.client.footer, prompt_gap="\u00a0")
        return wire

    def suggestion_request(self, previous_id="previous-message", new_id="new-message"):
        value = request("agent.deliver", new_id)
        value["params"] = {"text": "Send only this new body.",
                           "previous_completed_suggestion_id": previous_id}
        return value

    def change_action_receipt(self, message_id, change):
        with actions.ledger(self.client, message_id) as store:
            record = store.read()
            change(record)
            store.write(record)

    def change_delivery_receipt(self, message_id, change):
        from types import SimpleNamespace
        args = SimpleNamespace(pane=2, expect_broker=TARGET_BROKER,
                               message_id=message_id)
        with delivery.ledger(self.client, args) as store:
            record = store.read()
            change(record)
            store.write(record)

    def test_named_completed_faint_suggestion_is_replaced_and_submitted_once(self):
        self.seed_completed_suggestion()
        source = actions.current_cli_source_identity(self.client)
        self.assertTrue(actions.previous_completed_suggestion_scope(
            self.client, "previous-message", source,
            {"pane_id": 2, "broker": TARGET_BROKER}))
        root = Path(self.temp.name) / "kilix" / "agent-delivery"
        target_hash = actions.hashlib.sha256(
            (self.client.command[self.client.command.index("--to") + 1] +
             "\0" + TARGET_BROKER).encode()).hexdigest()
        prior_hash = actions.hashlib.sha256(b"previous-message").hexdigest()
        prior_path = root / f"{target_hash}-{prior_hash}.json"
        prior_bytes = prior_path.read_bytes()
        value = self.suggestion_request()
        with mock.patch.object(delivery, "ledger", wraps=delivery.ledger) as target_ledger:
            result = self.dispatch(value)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(self.client.inputs, [
            b"[kilix-message:previous-message] Please  review  the exact old suggestion.", b"\r",
            b"[kilix-message:new-message] Send only this new body.", b"\r",
        ])
        self.assertEqual(target_ledger.call_count, 1)
        self.assertEqual(prior_path.read_bytes(), prior_bytes)

    def test_suggestion_scope_requires_same_owned_source_and_provider(self):
        cases = (
            ("source", lambda r: r["receipt"].update(
                source={"pane_id": 3, "broker": SOURCE_BROKER})),
            ("provider", lambda r: r["receipt"]["evidence"].update(
                verification="codex-screen-v1")),
        )
        for label, mutate in cases:
            with self.subTest(case=label):
                old_id = f"previous-{label}"
                self.seed_completed_suggestion(old_id)
                self.change_action_receipt(old_id, mutate)
                before = list(self.client.inputs)
                result = self.dispatch(self.suggestion_request(
                    previous_id=old_id, new_id=f"new-{label}"))
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(self.client.inputs, before)

    def test_suggestion_requires_exact_digest_process_and_submitted_phase(self):
        cases = (
            ("digest", lambda r: r.update(digest="0" * 64)),
            ("process", lambda r: r.update(process_identity="0" * 64)),
            ("phase", lambda r: r.update(phase="deferred")),
        )
        for label, mutate in cases:
            with self.subTest(case=label):
                old_id = f"previous-{label}"
                self.seed_completed_suggestion(old_id)
                self.change_delivery_receipt(old_id, mutate)
                before = list(self.client.inputs)
                result = self.dispatch(self.suggestion_request(
                    previous_id=old_id, new_id=f"new-{label}"))
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(self.client.inputs, before)

    def test_suggestion_mismatch_mixed_style_and_wrapping_preserve_composer(self):
        from test_agent_delivery import claude_screen, wrapped_wire
        self.seed_completed_suggestion()
        original_inputs = list(self.client.inputs)
        old_wire = "[kilix-message:previous-message] Please  review  the exact old suggestion."
        cases = (
            ("wrong-id", "\x1b[2m[kilix-message:other-id] body"),
            ("mixed-style", "\x1b[2m" + old_wire[:8] + "\x1b[0m" + old_wire[8:]),
            ("wrapped", "\x1b[2m" + wrapped_wire(old_wire)),
            ("bright-draft", old_wire),
        )
        for label, value in cases:
            with self.subTest(case=label):
                self.client.current = claude_screen(
                    value, history=self.client.history, footer=self.client.footer,
                    prompt_gap="\u00a0")
                before_screen = self.client.current
                result = self.dispatch(self.suggestion_request(
                    new_id=f"new-{label}"))
                self.assertIn(result["status"], ("blocked", "uncertain"))
                self.assertEqual(self.client.current, before_screen)
                self.assertEqual(self.client.inputs, original_inputs)

    def test_unknown_suggestion_receipt_is_read_only_and_not_created(self):
        from test_agent_delivery import ClaudeTerminal, claude_screen
        self.client = ClaudeTerminal()
        old_wire = "[kilix-message:missing-old] prior body"
        self.client.current = claude_screen(
            "\x1b[2m" + old_wire, prompt_gap="\u00a0")
        result = self.dispatch(self.suggestion_request(
            previous_id="missing-old", new_id="new-missing"))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.client.inputs, [])
        operation_root = Path(self.temp.name) / "kilix" / "agent-actions"
        self.assertEqual(len(list(operation_root.glob("*.json"))), 1)
        delivery_root = Path(self.temp.name) / "kilix" / "agent-delivery"
        self.assertFalse(delivery_root.exists())

    def test_bright_new_wire_failure_stays_pasting_and_retries_observe_only(self):
        self.seed_completed_suggestion()
        faint_before = self.client.current
        self.client.on_paste = lambda c: setattr(c, "current", faint_before)
        # Use the lower-level path twice to verify the receipt itself prevents
        # a second paste after the replacement was attempted but not observed.
        from types import SimpleNamespace
        delivery_args = SimpleNamespace(
            pane=2, expect_broker=TARGET_BROKER, message_id="new-message",
            text="Send only this new body.", file=None, timeout=1, mode="steer",
            finish_staged=False, previous_completed_suggestion_id="previous-message",
            _previous_completed_suggestion_scope_verified=True,
            _previous_completed_suggestion_source_identity={
                "pane_id": 1, "broker": SOURCE_BROKER})
        before = list(self.client.inputs)
        first = delivery.deliver(self.client, delivery_args)
        self.assertEqual(first["status"], "uncertain")
        after_first = list(self.client.inputs)
        self.assertEqual(after_first, before + [
            b"[kilix-message:new-message] Send only this new body."])
        self.assertEqual(self.client.current, faint_before)
        second = delivery.deliver(self.client, delivery_args)
        self.assertEqual(second["status"], "uncertain")
        self.assertEqual(self.client.inputs, after_first)
        self.assertNotIn(b"\r", self.client.inputs[len(before):])

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

    def test_literal_help_in_child_or_title_still_records_mutation_intent(self):
        value = request()
        value['params'].update(argv=['/bin/printf', '--help'], title='--help')
        original_run = self.client.run

        def run(args, payload=None):
            result = original_run(args, payload)
            if args[0] == 'launch' and '--' in args:
                raise control.ControlError('lost launch response')
            return result

        self.client.run = run
        result = self.dispatch(value)
        self.assertEqual(result['status'], 'uncertain')
        repeated = self.dispatch(value)
        self.assertTrue(repeated['duplicate'])
        self.assertEqual(repeated['status'], 'uncertain')
        self.assertEqual(len(self.client.panes), 3)

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

    def test_structured_finish_staged_uses_previous_id_and_a_new_action_receipt(self):
        from test_agent_delivery import Terminal
        self.client = Terminal()
        message_id = "previous-stage"
        body = "Please inspect the exact prepared result."
        stage_args = control.parser().parse_args([
            "deliver", "2", "--expect-broker", TARGET_BROKER,
            "--message-id", message_id, "--text", body, "--timeout", "1"
        ])
        self.client.on_paste = lambda _client: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            delivery.deliver(self.client, stage_args)
        self.client.on_paste = None

        value = request("agent.deliver", "finish-action-1")
        value["params"].update(text=body, finish_staged_message_id=message_id)
        result = self.dispatch(value)
        self.assertEqual(result["status"], "submitted")
        self.assertTrue(result["delivery_verified"])
        self.assertEqual(self.client.inputs, [
            f"[kilix-message:{message_id}] {body}".encode(), b"\r"
        ])
        calls = len(self.client.calls)
        repeated = self.dispatch(value)
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(len(self.client.calls), calls)

    def test_structured_finish_staged_rejects_bad_id_and_dry_run_before_io(self):
        invalid = request("agent.deliver", "finish-action-invalid")
        invalid["params"]["finish_staged_message_id"] = "../bad"
        with mock.patch.object(control, "Client") as client:
            self.assertEqual(actions.dispatch(invalid)["status"], "blocked")
        client.assert_not_called()

        preview = request("agent.deliver", "finish-action-preview")
        preview["params"]["finish_staged_message_id"] = "previous-stage"
        preview["dry_run"] = True
        with mock.patch.object(control, "Client") as client:
            result = actions.dispatch(preview)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("cannot be previewed", result["error"])
        client.assert_not_called()

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
