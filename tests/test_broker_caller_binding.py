"""Q172-01: a substituted live sibling SID must not become this caller."""
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

# The optional path lets reviewers run these same controls against the parent
# source without changing the expected negative or any product behavior.
sys.path.insert(0, os.environ.get('Q172_SOURCE_CONFIG',
                               str(Path(__file__).resolve().parents[1] / 'config')))
import agent_control as control
from kilix_sdk import caller_context, frontend_context, panes
from test_agent_control_refresh import Fixture, SID

SIBLING = 'fedcba9876543210'


class BrokerCallerBindingTests(Fixture):
    def setUp(self):
        super().setUp()
        self.env = {**self.stale, **self.broker_identity, 'KILIX_KITTEN': str(self.new_kitten)}
        self.process(100, self.kitty, ['fake-agent'], self.env, parent=200)
        (self.proc / '100/comm').write_text('python3\n')
        self.owner = self.process(200, self.broker, [str(self.broker), '--runtime-dir',
            str(self.runtime), 'run', '--id', SID, '--journal-limit', '65536', '--', 'fake-agent'],
            self.env, parent=1)
        (self.owner / 'comm').write_text('kitty-pty-broke\n')
        self.process(41, self.broker, [str(self.broker), '--runtime-dir', str(self.runtime),
                                      'attach', SIBLING], dict(self.fresh,
            KITTY_PTY_BROKER_SESSION=SIBLING, KITTY_WINDOW_ID='4'), parent=20)
        for patcher in (mock.patch.object(caller_context, '_PROC', self.proc),
                        mock.patch.object(caller_context.os, 'getpid', return_value=100)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_matching_own_broker_still_rebinds_to_its_exact_replacement(self):
        values = self.values(self.env)
        self.assertEqual(self.route(values), self.fresh_route())
        self.assertEqual(values['KITTY_PTY_BROKER_SESSION'], SID)

    def test_live_sibling_substitution_is_refused_before_candidate_or_rpc(self):
        poisoned = dict(self.env, KITTY_PTY_BROKER_SESSION=SIBLING)
        with mock.patch.dict(os.environ, poisoned, clear=True), \
                mock.patch.object(frontend_context, '_candidate') as candidate, \
                mock.patch.object(control, 'kitten_path') as kitten:
            with self.assertRaisesRegex(control.ControlError, 'conflicts with the caller broker'):
                control.Client()
        candidate.assert_not_called()
        kitten.assert_not_called()

    def test_stripped_environment_cannot_recover_a_conflicting_sibling_claim(self):
        poisoned = dict(self.env, KITTY_PTY_BROKER_SESSION=SIBLING)
        (self.proc / '100/environ').write_bytes(
            b''.join(f'{key}={value}\0'.encode() for key, value in poisoned.items()))
        with self.assertRaisesRegex(control.ControlError, 'conflicts with the caller broker'):
            self.values({}, ppid=100)

    def test_sdk_refuses_same_existing_sibling_before_transport(self):
        poisoned = dict(self.env, KITTY_PTY_BROKER_SESSION=SIBLING)
        with mock.patch.dict(os.environ, poisoned, clear=True), \
                mock.patch.object(panes.subprocess, 'run') as run:
            with self.assertRaises(panes.PaneError):
                panes.load_state()
        run.assert_not_called()

    def test_explicit_refresh_mapping_cannot_override_own_process_broker(self):
        poisoned = dict(self.env, KITTY_PTY_BROKER_SESSION=SIBLING)
        before = dict(poisoned)
        with mock.patch.dict(os.environ, self.env, clear=True), \
                self.assertRaises(caller_context.CallerContextError):
            frontend_context.refresh(poisoned)
        self.assertEqual(poisoned, before)

    def test_live_engine_fast_path_cannot_bypass_conflicting_broker(self):
        self.process(10, self.kitty, ['kitty'], {}, parent=1)
        with mock.patch.dict(os.environ, dict(self.env, KITTY_PTY_BROKER_SESSION=SIBLING), clear=True), \
                mock.patch.object(frontend_context, '_kitty_alive') as alive, \
                self.assertRaises(caller_context.CallerContextError):
            frontend_context.refresh()
        alive.assert_not_called()

    def test_runtime_substitution_is_refused_too(self):
        with mock.patch.dict(os.environ, dict(self.env, KITTY_PTY_BROKER_RUNTIME='/different-runtime'), clear=True), \
                self.assertRaises(caller_context.CallerContextError):
            caller_context.require_pane_local()

    def test_nearest_broker_owns_nested_callers_without_borrowing_outer_identity(self):
        (self.owner / 'stat').write_text('200 (broker) ' + ' '.join(['S', '300'] + ['0'] * 17 + ['777']))
        outer = self.process(300, self.broker, [str(self.broker), '--runtime-dir', '/outer-runtime',
            'run', '--id', SIBLING, '--', 'outer-agent'], {}, parent=1)
        (outer / 'comm').write_text('kitty-pty-broke\n')
        with mock.patch.dict(os.environ, self.env, clear=True):
            caller_context.require_pane_local()

    def test_matching_broker_does_not_bypass_a_higher_shared_server(self):
        (self.owner / 'stat').write_text('200 (broker) ' + ' '.join(['S', '300'] + ['0'] * 17 + ['777']))
        server = self.process(300, self.kitty, ['codex', 'app-server'], {}, parent=1)
        (server / 'comm').write_text('codex\n')
        with mock.patch.dict(os.environ, self.env, clear=True), \
                self.assertRaisesRegex(caller_context.CallerContextError, 'shared Codex'):
            caller_context.require_pane_local()

    def test_timeout_and_logging_options_do_not_hide_the_own_broker(self):
        args = [str(self.broker), '--timeout', '1', '--runtime-dir', str(self.runtime),
                'run', '--transcript', '/fixture/log', '--id', SID, '--transcript-limit',
                '4096', '--transcript-graphics', 'elide', '--', 'fake-agent']
        (self.owner / 'cmdline').write_bytes(b'\0'.join(os.fsencode(a) for a in args) + b'\0')
        with mock.patch.dict(os.environ, dict(self.env, KITTY_PTY_BROKER_SESSION=SIBLING), clear=True), \
                self.assertRaises(caller_context.CallerContextError):
            caller_context.require_pane_local()

    def test_command_payload_cannot_supply_a_fake_identity(self):
        args = [str(self.broker), '--runtime-dir', str(self.runtime), 'run', '--id', SID,
                '--', 'fake-agent', '--id', SIBLING]
        (self.owner / 'cmdline').write_bytes(b'\0'.join(os.fsencode(a) for a in args) + b'\0')
        with mock.patch.dict(os.environ, dict(self.env, KITTY_PTY_BROKER_SESSION=SIBLING), clear=True), \
                self.assertRaises(caller_context.CallerContextError):
            caller_context.require_pane_local()

    def test_unverifiable_matching_run_identity_fails_closed(self):
        (self.owner / 'cmdline').write_bytes(os.fsencode(self.broker) + b'\0run\0--id\0' + SID.encode() + b'\0--\0agent\0')
        with mock.patch.dict(os.environ, self.env, clear=True), \
                self.assertRaises(caller_context.CallerContextError):
            caller_context.require_pane_local()


if __name__ == '__main__':
    unittest.main()
