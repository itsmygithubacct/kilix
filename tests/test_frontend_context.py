"""Broker reattachment routes remain scoped to a live same-user frontend."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_sdk import frontend_context as context
from kilix_sdk import panes


class FrontendContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.runtime = self.root / 'broker'
        self.runtime.mkdir(mode=0o700)
        self.broker = self.root / 'kitty-pty-broker'
        self.kitty = self.root / 'kitty'
        for path in (self.broker, self.kitty):
            path.write_text('private process fixture')
            path.chmod(0o700)
        self.sid = '0123456789abcdef'
        self.env = {
            'KITTY_PID': '10', 'KITTY_WINDOW_ID': '1',
            'KITTY_LISTEN_ON': 'unix:@kilix-10', 'KITTY_PUBLIC_KEY': 'old-key',
            'KILIX_RC_PASSWORD_FILE': '/old-private-password-file',
            'KITTY_PTY_BROKER_SESSION': self.sid,
            'KITTY_PTY_BROKER_RUNTIME': str(self.runtime),
            'KITTY_PTY_BROKER_EXECUTABLE': str(self.broker),
            'UNRELATED': 'caller-value',
        }
        self.peer = dict(self.env, KITTY_PID='20', KITTY_WINDOW_ID='7',
                         KITTY_LISTEN_ON='unix:@kilix-20', KITTY_PUBLIC_KEY='new-key',
                         KILIX_RC_PASSWORD_FILE='/new-private-password-file',
                         UNRELATED='never-copy', SECRET_OTHER='never-copy')
        self.process('20', self.kitty, ['kitty'], {}, parent=1)
        self.attach = self.process('40', self.broker,
                                  [str(self.broker), '--runtime-dir', str(self.runtime),
                                   'attach', self.sid], self.peer, parent=20)
        patcher = patch.object(context, '_PROC', self.proc)
        patcher.start()
        self.addCleanup(patcher.stop)

    def process(self, pid, executable, argv, environment, parent):
        path = self.proc / pid
        path.mkdir()
        (path / 'exe').symlink_to(executable)
        fields = ['S', str(parent)] + ['0'] * 17 + ['12345']
        (path / 'stat').write_text(pid + ' (fixture with spaces) ' + ' '.join(fields))
        (path / 'cmdline').write_bytes(b'\0'.join(os.fsencode(arg) for arg in argv) + b'\0')
        (path / 'environ').write_bytes(b'\0'.join(
            (key + '=' + value).encode() for key, value in environment.items()) + b'\0')
        return path

    def set_peer(self, **values):
        self.peer.update(values)
        (self.attach / 'environ').write_bytes(b'\0'.join(
            (key + '=' + value).encode() for key, value in self.peer.items()) + b'\0')

    def refused(self):
        original = dict(self.env)
        self.assertFalse(context.refresh(self.env))
        self.assertEqual(self.env, original)

    def test_recovered_route_updates_only_authentication_and_window_context(self):
        self.assertTrue(context.refresh(self.env))
        self.assertEqual({key: self.env[key] for key in context._ROUTE},
                         {key: self.peer[key] for key in context._ROUTE})
        self.assertEqual(self.env['UNRELATED'], 'caller-value')
        self.assertNotIn('SECRET_OTHER', self.env)
        self.assertFalse(context.refresh(self.env), 'current live host should take fast path')

    def test_live_original_host_is_not_rebound(self):
        self.process('10', self.kitty, ['kitty'], {}, parent=1)
        with patch.object(context, '_candidate', side_effect=AssertionError('unnecessary scan')):
            self.refused()

    def test_pane_transport_keeps_password_authentication_after_rebinding(self):
        calls = []
        def capture(argv, **kwargs):
            calls.append((argv, {key: os.environ.get(key) for key in context._ROUTE}))
            return unittest.mock.Mock(returncode=0, stdout='[]', stderr='')
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(panes, 'KITTEN', '/fixture/kitten'), \
                patch.object(panes.subprocess, 'run', side_effect=capture):
            self.assertEqual(panes.load_state(), [])
        self.assertEqual(calls[0][0], ['/fixture/kitten', '@', '--password-file',
                                      '/new-private-password-file', 'ls'])
        self.assertEqual({key: calls[0][1][key] for key in context._ROUTE},
                         {key: self.peer[key] for key in context._ROUTE})

    def test_tty_transport_uses_current_key_and_window_without_stale_socket(self):
        calls = []
        def capture(argv, **kwargs):
            calls.append((argv, kwargs['env']))
            return unittest.mock.Mock(returncode=0, stdout='', stderr='')
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(panes.subprocess, 'run', side_effect=capture):
            panes._run(['resize-os-window', '--self'], via_tty=True, authenticated=False)
        self.assertNotIn('--password-file', calls[0][0])
        self.assertNotIn('KITTY_LISTEN_ON', calls[0][1])
        self.assertEqual(calls[0][1]['KITTY_PUBLIC_KEY'], 'new-key')
        self.assertEqual(calls[0][1]['KITTY_WINDOW_ID'], '7')

    def test_other_session_cannot_supply_route(self):
        self.set_peer(KITTY_PTY_BROKER_SESSION='another-session')
        self.refused()

    def test_other_runtime_cannot_supply_route(self):
        self.set_peer(KITTY_PTY_BROKER_RUNTIME='/different-runtime')
        self.refused()

    def test_route_host_must_be_attach_helpers_actual_parent(self):
        self.set_peer(KITTY_PID='30')
        self.refused()

    def test_parent_must_be_a_live_kitty_process(self):
        shutil.rmtree(self.proc / '20')
        self.refused()

    def test_runtime_must_be_private(self):
        self.runtime.chmod(0o755)
        self.refused()

    def test_runtime_symlink_is_refused(self):
        link = self.root / 'runtime-link'
        link.symlink_to(self.runtime)
        self.env['KITTY_PTY_BROKER_RUNTIME'] = str(link)
        self.refused()

    def test_foreign_process_owner_is_refused(self):
        with patch.object(context.os, 'geteuid', return_value=os.geteuid() + 1):
            self.refused()

    def test_broker_executable_must_match(self):
        (self.attach / 'exe').unlink()
        (self.attach / 'exe').symlink_to(self.kitty)
        self.refused()

    def test_writable_broker_executable_is_refused(self):
        self.broker.chmod(0o722)
        self.refused()

    def test_command_must_attach_exact_runtime_and_session(self):
        (self.attach / 'cmdline').write_bytes(b'kitty-pty-broker\0attach\0other\0')
        self.refused()

    def test_ambiguous_attach_peers_are_refused(self):
        self.process('41', self.broker,
                     [str(self.broker), '--runtime-dir', str(self.runtime), 'attach', self.sid],
                     self.peer, parent=20)
        self.refused()

    def test_nonlocal_route_and_invalid_window_are_refused(self):
        for changes in ({'KITTY_LISTEN_ON': 'tcp:foreign:1234'},
                        {'KITTY_WINDOW_ID': '0'}, {'KITTY_PUBLIC_KEY': ''}):
            with self.subTest(changes=changes):
                old = dict(self.peer)
                self.set_peer(**changes)
                self.refused()
                self.peer = old
                self.set_peer()

    def test_peer_identity_change_during_read_is_refused(self):
        identity = context._identity
        count = 0
        def changing(path):
            nonlocal count
            value = identity(path)
            if path == self.attach:
                count += 1
                if count > 1:
                    return value[0], value[1] + 1
            return value
        with patch.object(context, '_identity', side_effect=changing):
            self.refused()


if __name__ == '__main__':
    unittest.main()
