"""Select an initial desktop from real broker-child environments, fail closed."""
import io
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pty_broker import load_module


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux /proc child association')
class StartupSessionTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.token = 'a' * 32
        self.children = []

    def tearDown(self):
        for child in self.children:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)

    def child(self, token=None):
        env = os.environ.copy()
        env.pop('KITTY_PTY_BROKER_STARTUP_SESSION', None)
        if token is not None:
            env['KITTY_PTY_BROKER_STARTUP_SESSION'] = token
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], env=env)
        self.children.append(child)
        return {'id': 'pane-' + str(child.pid), 'child_pid': child.pid, 'attached': False}

    def test_matches_live_initial_child_and_ignores_unmarked_panes(self):
        ordinary = self.child()
        initial = self.child(self.token)
        different_login = self.child('b' * 32)
        self.assertIs(self.module.startup_session([ordinary, different_login, initial], self.token), initial)
        self.assertIsNone(self.module.startup_session([ordinary, different_login], self.token))

    def test_ambiguous_initial_children_do_not_replace_the_requested_program(self):
        initial = self.child(self.token)
        duplicate = self.child(self.token)
        self.assertIsNone(self.module.startup_session([initial, duplicate], self.token))

    def test_attached_dead_and_invalid_child_records_are_not_selected(self):
        initial = self.child(self.token)
        records = [dict(initial, attached=True), dict(initial, id='../escape'),
                   dict(initial, child_pid=True), dict(initial, child_pid=-1),
                   dict(initial, child_pid=str(initial['child_pid']))]
        self.assertIsNone(self.module.startup_session(records, self.token))
        self.children[0].terminate()
        self.children[0].wait(timeout=2)
        self.assertIsNone(self.module.startup_session([initial], self.token))

    def test_invalid_login_tokens_do_not_inspect_processes(self):
        with patch.object(self.module, '_startup_token_of_child') as read:
            for token in ('', 'A' * 32, 'a' * 31, 'a' * 33, 'a' * 32 + '\n'):
                self.assertIsNone(self.module.startup_session([{'id': 'pane', 'child_pid': 1}], token))
            read.assert_not_called()

    def test_foreign_uid_cannot_supply_a_startup_association(self):
        initial = self.child(self.token)
        with patch.object(self.module.os, 'stat', return_value=SimpleNamespace(st_uid=os.getuid()+1)), \
                patch('builtins.open') as read:
            self.assertIsNone(self.module.startup_session([initial], self.token))
            read.assert_not_called()

    def test_process_identity_and_environment_bounds(self):
        def stat_text(tick, state='S'):
            return '1 (process name) ' + ' '.join([state] + ['0'] * 18 + [str(tick)])

        for before, data, after in (
            (stat_text(10), b'KITTY_PTY_BROKER_STARTUP_SESSION=' + self.token.encode() + b'\0', stat_text(11)),
            (stat_text(10), b'KITTY_PTY_BROKER_STARTUP_SESSION=' + self.token.encode() + b'\0', stat_text(10, 'Z')),
            (stat_text(10), b'x' * 262145, stat_text(10)),
            (stat_text(10), (b'KITTY_PTY_BROKER_STARTUP_SESSION=' + self.token.encode() + b'\0') * 2, stat_text(10)),
            (stat_text(10), b'KITTY_PTY_BROKER_STARTUP_SESSION=\xff\0', stat_text(10)),
        ):
            with self.subTest(size=len(data), after=after), \
                    patch.object(self.module.os, 'stat', return_value=SimpleNamespace(st_uid=os.getuid())), \
                    patch('builtins.open', side_effect=[io.StringIO(before), io.BytesIO(data), io.StringIO(after)]):
                self.assertEqual(self.module._startup_token_of_child(1), '')


if __name__ == '__main__':
    unittest.main()
