"""Explicit model/effort requests must survive launch, plan and recovery."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import agent_actions as actions
import agent_control as control
import test_agent_actions as action_tests
import test_agent_control as control_tests
request = action_tests.request


class EffortActionsTests(unittest.TestCase):
    setUp = action_tests.ActionsTests.setUp
    dispatch = action_tests.ActionsTests.dispatch

    def test_requested_models_and_efforts_reach_exact_cli_arguments(self):
        configurations = [("gpt-6.1-sol", e) for e in ("low", "medium", "high", "xhigh")]
        configurations += [("gpt-6-astra", e) for e in ("low", "medium", "high")]
        for i, (model, effort) in enumerate(configurations):
            with self.subTest(model=model, effort=effort):
                self.client.panes = self.client.panes[:2]
                value = request("agent.launch", f"effort-{i}")
                value["params"].update(model=model, reasoning_effort=effort)
                with mock.patch("agent_programs.resolve_agent_command", return_value="/bin/codex"):
                    result = self.dispatch(value)
                self.assertEqual(result["status"], "created")
                launch = [args for args, _ in self.client.calls if args[0] == "launch" and "--help" not in args][-1]
                self.assertEqual(launch[launch.index("--") + 1:],
                    ["/bin/codex", "--model", model, "-c", f'model_reasoning_effort="{effort}"'])
                self.assertEqual(result["evidence"]["requested"],
                    {"agent": "codex", "model": model, "reasoning_effort": effort})
                self.assertFalse(result["agent_startup_verified"])
                self.assertFalse(result["completion_verified"])
                status = request("operation.status", f"effort-{i}")
                self.assertEqual(self.dispatch(status), result)
                self.assertTrue(self.dispatch(value)["duplicate"])

    def test_invalid_or_wrong_agent_effort_refused_before_connection(self):
        for effort in (None, True, 1, [], {}, "", "HIGH", "high;exec", 'high"\nother=true'):
            value = request("agent.launch")
            value["params"]["reasoning_effort"] = effort
            with mock.patch.object(control, "Client") as client:
                result = actions.dispatch(value)
            self.assertEqual(result["status"], "blocked")
            client.assert_not_called()
        value = request("agent.launch")
        value["params"].update(agent="claude", reasoning_effort="high")
        with self.assertRaisesRegex(control.ControlError, "only for codex"):
            actions.validate(value)
        value = request("pane.open")
        value["params"]["reasoning_effort"] = "high"
        with self.assertRaises(control.ControlError):
            actions.validate(value)

    def test_same_id_cannot_change_requested_effort(self):
        value = request("agent.launch")
        value["params"].update(model="gpt-6.1-sol", reasoning_effort="low")
        with mock.patch("agent_programs.resolve_agent_command", return_value="/bin/codex"):
            self.assertEqual(self.dispatch(value)["status"], "created")
        value["params"]["reasoning_effort"] = "high"
        refused = self.dispatch(value)
        self.assertEqual(refused["status"], "blocked")
        self.assertIn("different request", refused["error"])
        self.assertEqual(len(self.client.panes), 3)

    def test_plan_reports_requested_configuration_without_launch_or_persistence(self):
        value = request("agent.launch")
        value.update(dry_run=True)
        value["params"].update(model="gpt-6.1-sol", reasoning_effort="xhigh")
        with mock.patch("agent_programs.resolve_agent_command", return_value="/bin/codex"):
            result = self.dispatch(value)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["evidence"]["requested"]["reasoning_effort"], "xhigh")
        self.assertEqual(len(self.client.panes), 2)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_old_receipt_without_requested_settings_is_still_readable(self):
        value = request("agent.launch")
        with mock.patch("agent_programs.resolve_agent_command", return_value="/bin/codex"):
            self.dispatch(value)
        with actions.ledger(self.client, value["operation_id"]) as store:
            record = store.read()
            record["receipt"]["evidence"].pop("requested")
            store.write(record)
        self.assertEqual(self.dispatch(request("operation.status"))["status"], "created")

    def test_tampered_effort_receipt_is_refused(self):
        value = request("agent.launch")
        with mock.patch("agent_programs.resolve_agent_command", return_value="/bin/codex"):
            self.dispatch(value)
        with actions.ledger(self.client, value["operation_id"]) as store:
            record = store.read()
            record["receipt"]["evidence"]["requested"]["reasoning_effort"] = []
            store.write(record)
        self.assertEqual(self.dispatch(request("operation.status"))["status"], "blocked")


class EffortArgvTests(unittest.TestCase):
    setUp = control_tests.AgentArgvTests.setUp
    argv = control_tests.AgentArgvTests.argv

    def test_resume_keeps_effort_before_session_and_prompt(self):
        actual, _ = self.argv("codex", "--resume", "abc-123", "--model", "gpt-6-astra",
                              "--reasoning-effort", "high", "--prompt", "continue implementation")
        self.assertEqual(actual, ["/bin/codex", "resume", "--model", "gpt-6-astra", "-c",
                                 'model_reasoning_effort="high"', "abc-123", "continue implementation"])

    def test_effort_does_not_enable_yolo_or_other_clients(self):
        actual, _ = self.argv("codex", "--reasoning-effort", "low", yolo_setting=True)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", actual)
        with self.assertRaisesRegex(control.ControlError, "only for codex"):
            self.argv("grok", "--reasoning-effort", "high")


if __name__ == "__main__":
    unittest.main()
