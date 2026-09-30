"""Startup discovery never sends input, guesses targets or hides stack selection."""
import copy
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import agent_context as context
import agent_control as control


class ContextTests(unittest.TestCase):
    def setUp(self):
        component = {"status": "observed", "root": "/source", "revision": "a" * 40,
                     "revision_status": "observed", "version": "0.2.2",
                     "action_backend": {"status": "observed", "schema": "kilix.actions/v1",
                                        "operations": {"pane.open": {}}}}
        self.facts = {"kilix": {"invoked_source": component,
                                "path_selected": dict(component, root="/installed", revision="b" * 40),
                                "same_root": False},
                      "needle": {"status": "missing"},
                      "action_module_override": {"status": "unset"},
                      "needle_kilix_selection": {"resolved_command": "/installed/kilix"}}
        self.panes = [{"id": 1, "env": {"KITTY_PTY_BROKER_SESSION": "a" * 16,
                                         "SECRET": "credential"}, "title": "private prompt"},
                      {"id": 2, "env": {"KITTY_PTY_BROKER_SESSION": "b" * 16}}]
        self.client = mock.Mock(spec=["caller", "snapshot"])
        self.client.caller = 1
        self.client.snapshot.return_value = self.panes

    def collect(self, **kwargs):
        return context.collect(client_factory=lambda caller: self.client,
                               discover=lambda **kw: copy.deepcopy(self.facts), **kwargs)

    def test_one_read_only_snapshot_and_explicit_target(self):
        result = self.collect(target=2, expected="b" * 16)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["caller"]["pane_id"], 1)
        self.assertEqual(result["target"]["broker"], "b" * 16)
        self.client.snapshot.assert_called_once_with()
        output = context.encode(result)
        self.assertNotIn("credential", output)
        self.assertNotIn("private prompt", output)
        self.assertNotIn("SECRET", output)

    def test_selection_mismatch_is_visible_not_an_override(self):
        result = self.collect()
        installation = result["installation"]
        self.assertFalse(installation["same_root"])
        self.assertFalse(installation["needle_host_matches_invoked"])
        self.assertEqual(installation["invoked"]["revision"], "a" * 40)
        self.assertEqual(installation["path_selected"]["revision"], "b" * 40)
        self.assertEqual(installation["invoked"]["command"], "/source/kilix")
        self.assertEqual(result["target"], {"status": "not_requested"})

    def test_stale_or_absent_target_never_substitutes_a_pane(self):
        for target, expected, status in [(2, "c" * 16, "stale"), (99, None, "absent")]:
            result = self.collect(target=target, expected=expected)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["target"]["status"], status)
            self.assertNotIn("broker", result["target"])

    def test_duplicate_broker_and_pane_are_ambiguous(self):
        self.panes[1]["env"]["KITTY_PTY_BROKER_SESSION"] = "a" * 16
        self.assertEqual(self.collect()["caller"]["status"], "ambiguous")
        self.panes.append(copy.deepcopy(self.panes[0]))
        self.assertEqual(self.collect()["caller"]["status"], "ambiguous")

    def test_missing_or_invalid_caller_is_not_inferred_from_target(self):
        for caller in (0, 99, True, 2**100):
            self.client.caller = caller
            result = self.collect(target=2)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["target"]["status"], "observed")

    def test_connection_failure_keeps_installation_facts_and_redacts_error(self):
        with mock.patch.object(control, "Client", side_effect=control.ControlError("secret-path")):
            result = context.collect(discover=lambda **kw: self.facts, target=2)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["connection_status"], "unavailable")
        self.assertEqual(result["installation"]["invoked"]["revision"], "a" * 40)
        self.assertNotIn("secret-path", context.encode(result))

    def test_explicit_caller_is_labelled_and_conflicts_remain_partial(self):
        self.assertEqual(self.collect(caller_pane=1)["identity_basis"], "explicit_caller")
        with mock.patch.object(control, "Client", side_effect=control.ControlError(
                "--caller-pane conflicts with the inherited caller identity")):
            result = context.collect(caller_pane=2, discover=lambda **kw: self.facts)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["caller"], {"status": "unavailable"})
        self.assertEqual(result["identity_basis"], "explicit_caller")

    def test_output_bound_includes_ascii_expansion_and_newline(self):
        self.facts["kilix"]["invoked_source"]["root"] = "/" + "\U0001f600" * 1024
        output = context.encode(self.collect())
        self.assertLessEqual(len(output.encode()), 4096)
        self.assertTrue(json.loads(output)["installation"]["invoked"]["command_omitted"])
        output = context.encode({"extra": "\U0001f600" * 4096})
        self.assertLessEqual(len(output.encode()), 4096)
        self.assertEqual(json.loads(output)["error"], "response_limit")

    def test_cli_reports_partial_with_nonzero_exit(self):
        with mock.patch.object(context, "collect", return_value={"status": "partial"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(control.main(["context"]), 1)
        self.assertEqual(json.loads(out.getvalue())["status"], "partial")

    def test_cli_rejects_unpaired_or_malformed_expected_identity_before_discovery(self):
        for args in (["context", "--expect-broker", "a" * 16],
                     ["context", "--expect-broker", ""],
                     ["context", "--target", "2", "--expect-broker", "invalid"]):
            with mock.patch.object(context, "collect") as collect, \
                    mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                control.main(args)
            collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
