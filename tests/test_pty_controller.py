"""Owned disposable fixtures for the trusted selection boundary."""
import copy
import io
import contextlib
import os
import unittest
from unittest import mock

import test_pty_selector as fixtures
import kilix_pty_controller as control
import kilix_pty_selector as api


class ControllerTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.SelectorEnds.setUpClass.__func__)
    setUp = fixtures.SelectorEnds.setUp
    tearDown = fixtures.SelectorEnds.tearDown
    request = fixtures.SelectorEnds.request
    sends = fixtures.SelectorEnds.sends
    def controller(self, now=None):
        return control.Controller(self.broker, str(self.state),
                                  **({"clock": lambda: now[0]} if now is not None else {}))

    def test_controller_success_and_replay(self):
        self.fx.start("selected")
        self.fx.start("bystander")
        controller = self.controller()
        request = self.request({"id": "selected"})
        self.assertTrue(controller.select(request)["issued"])
        code, result = controller.dispatch(request)
        self.assertEqual((code, result["result"]), (0, "verified_absent"))
        calls = len(self.broker.calls)
        self.assertTrue(controller.dispatch(request)[1]["duplicate"])
        self.assertEqual(len(self.broker.calls), calls)
        self.assertTrue(self.fx.alive("bystander"))

    def test_absent_mismatched_expired_revoked_authority(self):
        self.fx.start("selected")
        request = self.request({"id": "selected", "attached": False})
        now = [100.0]
        c = self.controller(now)
        self.assertEqual(c.dispatch(request)[1]["reason"], "authority_required")
        for changed in ({"id": "selected"}, {"id_prefix": "selected"}, {"id": "other"}):
            self.assertTrue(c.select(request)["issued"])
            self.assertEqual(c.dispatch(self.request(changed))[1]["reason"], "authority_mismatch")
            self.assertEqual(c.dispatch(request)[1]["reason"], "authority_required")
        self.assertTrue(c.select(request, lifetime_seconds=1)["issued"])
        now[0] += 1
        self.assertEqual(c.dispatch(request)[1]["reason"], "authority_expired")
        self.assertTrue(c.select(request)["issued"])
        c.revoke()
        self.assertEqual(c.dispatch(request)[1]["reason"], "authority_required")
        self.assertFalse(self.sends())
        self.assertTrue(self.fx.alive("selected"))

    def test_ambiguous_user_selection_cannot_be_narrowed_by_actor(self):
        self.fx.start("pair.one")
        self.fx.start("pair.two")
        c = self.controller()
        self.assertFalse(c.select(self.request({"id_prefix": "pair."}))["issued"])
        self.assertEqual(c.dispatch(self.request({"id": "pair.one"}))[1]["reason"], "authority_required")
        self.assertFalse(self.sends())
        self.assertTrue(self.fx.alive("pair.one"))
        self.assertTrue(self.fx.alive("pair.two"))

    def test_selection_is_copied_and_operation_key_is_scoped(self):
        self.fx.start("selected")
        request = self.request({"id": "selected"})
        c = self.controller()
        self.assertTrue(c.select(request)["issued"])
        request["selector"]["id"] = "other"
        self.assertEqual(c.dispatch(request)[1]["reason"], "authority_mismatch")
        request = self.request({"id": "selected"})
        self.assertTrue(c.select(request)["issued"])
        request["operation_id"] = "another"
        self.assertEqual(c.dispatch(request)[1]["reason"], "authority_mismatch")
        self.assertFalse(self.sends())

    def test_expiry_during_resolution_and_binding_change_refuse(self):
        self.fx.start("selected")
        request = self.request({"id": "selected"})
        now = [100.0]
        c = self.controller(now)
        self.assertTrue(c.select(request, lifetime_seconds=1)["issued"])
        def expire(broker, args):
            if args[0] == "resolve":
                now[0] += 2
        self.broker.hook = expire
        self.assertEqual(c.dispatch(request)[1]["reason"], "authority_expired")
        self.broker.hook = None
        request["operation_id"] = "changed-binding"
        self.assertTrue(c.select(request)["issued"])
        # Benign observation change, no historical wrong-target reproduction.
        self.fx.attach("selected")
        self.assertEqual(c.dispatch(request)[1]["reason"], "authority_mismatch")
        self.assertFalse(self.sends())
        self.assertTrue(self.fx.alive("selected"))

    def test_cancellation_consumes_and_restart_does_not_issue(self):
        self.fx.start("selected")
        request = self.request({"id": "selected"})
        c = self.controller()
        self.assertTrue(c.select(request)["issued"])
        with mock.patch.object(api, "execute", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                c.dispatch(request)
        self.assertEqual(c.dispatch(request)[1]["reason"], "authority_consumed")
        self.assertEqual(self.controller().dispatch(request)[1]["reason"], "authority_required")
        self.assertFalse(self.sends())

    def test_untrusted_cli_and_json_refuse_even_with_yes(self):
        self.fx.start("selected")
        request = self.request({"id": "selected"})
        self.assertEqual(api.untrusted_execute(self.broker, request, True, str(self.state))[1]["reason"],
                         "authority_required")
        with mock.patch.dict(os.environ, {"KILIX_STATE_DIRECTORY": str(self.state)}), contextlib.redirect_stdout(io.StringIO()) as out:
            code = api.command(self.broker, "end", ["--selector-json", '{"id":"selected"}',
                                                  "--operation-id", "cli", "--yes", "--json"])
        self.assertEqual(code, 3)
        self.assertIn("authority_required", out.getvalue())
        self.assertFalse(self.sends())
        self.assertTrue(self.fx.alive("selected"))

    def test_context_changes_and_stale_incarnation_refuse(self):
        self.fx.start("selected")
        request = self.request({"id": "selected"})
        c = self.controller()
        self.assertTrue(c.select(request)["issued"])
        with mock.patch.dict(os.environ, {"KITTY_PTY_BROKER_SESSION": "different-caller"}):
            self.assertEqual(c.dispatch(request)[1]["reason"], "authority_mismatch")
        self.assertTrue(c.select(request)["issued"])
        other = self.fx.root / "other-runtime"
        other.mkdir(mode=0o700)
        with mock.patch.object(self.broker, "runtime", str(other)):
            self.assertEqual(c.dispatch(request)[1]["reason"], "authority_mismatch")
        self.assertTrue(c.select(request)["issued"])
        real_document = api.broker_document
        def changed_incarnation(*args, **kwargs):
            doc, code, error = real_document(*args, **kwargs)
            if doc is not None and args[2] == "resolve":
                doc = copy.deepcopy(doc)
                doc["resolved"]["started_millis"] += 1
            return doc, code, error
        with mock.patch.object(api, "broker_document", side_effect=changed_incarnation):
            self.assertEqual(c.dispatch(request)[1]["reason"], "authority_mismatch")
        self.assertFalse(self.sends())
        self.assertTrue(self.fx.alive("selected"))

    def test_invalid_lifetime_revokes_previous_selection(self):
        self.fx.start("selected")
        request = self.request({"id": "selected"})
        c = self.controller()
        for lifetime in (0, -1, True, float("nan"), float("inf"), 61):
            self.assertTrue(c.select(request)["issued"])
            with self.assertRaises(ValueError):
                c.select(request, lifetime_seconds=lifetime)
            self.assertEqual(c.dispatch(request)[1]["reason"], "authority_required")
        self.assertFalse(self.sends())


if __name__ == "__main__":
    unittest.main()
