"""Deterministic socket publication race; no installed service or owner bus."""
import errno
from pathlib import Path
import socket
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_sdk.bus_supervision import SupervisedPlatform
from kilix_sdk.bus_platform import PlatformUnsupported


class ControlReadiness(unittest.TestCase):
    def fixture(self, errors=(), peer=(4321, 0, 0), dead=False):
        clock = [10.0]
        platform = object.__new__(SupervisedPlatform)
        platform.control = Mock()
        platform.control.connect.side_effect = list(errors) + [None]
        platform.control.getsockopt.return_value = struct.pack('3i', *peer)
        platform.pidfd = 99
        platform.identity = ('exact-invocation', 4321, '/exact-service')
        platform.name = 'exact-service'
        platform.ops = SimpleNamespace(ready=Mock(return_value=dead),
                                       sleep=lambda delay: clock.__setitem__(0, clock[0] + delay))
        platform.manager = SimpleNamespace(unit=Mock(return_value={'exact': True}))
        platform._service = Mock()
        return platform, clock

    def test_bind_chmod_listen_gap_retries_then_authenticates_exact_peer(self):
        p, clock = self.fixture([FileNotFoundError(), PermissionError(errno.EACCES, 'before chmod'),
                                 ConnectionRefusedError()])
        with patch('kilix_sdk.bus_platform.time.monotonic', side_effect=lambda: clock[0]):
            p._connect_control('/fixture', 11.0)
        self.assertEqual(p.control.connect.call_count, 4)
        self.assertTrue(all(c.args == ('/fixture/control',) for c in p.control.connect.call_args_list))
        p.control.getsockopt.assert_called_once_with(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        p.manager.unit.assert_called_once_with('exact-service', 11.0)
        p._service.assert_called_once_with({'exact': True})
        budgets = [c.args[0] for c in p.control.settimeout.call_args_list]
        self.assertEqual(budgets[0], 1.0)
        self.assertLess(budgets[-1], budgets[0])
        self.assertEqual(budgets, sorted(budgets, reverse=True))

    def test_permanent_denial_expires_original_deadline(self):
        p, clock = self.fixture()
        p.control.connect.side_effect = PermissionError(errno.EACCES, 'permanent denial')
        deadline = 10.025
        with patch('kilix_sdk.bus_platform.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                p._connect_control('/fixture', deadline)
        self.assertAlmostEqual(clock[0], deadline)
        self.assertEqual(p.control.connect.call_count, 3)
        p.control.getsockopt.assert_not_called()
        p._service.assert_not_called()

    def test_dead_supervisor_during_denial_stops_immediately(self):
        p, clock = self.fixture([PermissionError(errno.EACCES, "before chmod")], dead=True)
        with patch('kilix_sdk.bus_platform.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(RuntimeError, 'exited'):
                p._connect_control('/fixture', 11.0)
        self.assertEqual(p.control.connect.call_count, 1)
        self.assertEqual(clock[0], 10.0)
        p.control.getsockopt.assert_not_called()

    def test_peer_authentication_after_retry_rejects_wrong_uid_pid_or_dead_peer(self):
        for peer, dead in [((4321, 1000, 1000), False), ((4322, 0, 0), False), ((4321, 0, 0), True)]:
            with self.subTest(peer=peer, dead=dead):
                p, clock = self.fixture([PermissionError(errno.EACCES, "before chmod")], peer=peer)
                p.ops.ready.side_effect = [False, dead] if peer == (4321, 0, 0) else [False]
                with patch('kilix_sdk.bus_platform.time.monotonic', side_effect=lambda: clock[0]):
                    with self.assertRaises(PlatformUnsupported):
                        p._connect_control('/fixture', 11.0)
                p._service.assert_not_called()

    def test_replaced_service_is_not_accepted_after_peer_authentication(self):
        p, clock = self.fixture([PermissionError(errno.EACCES, "before chmod")])
        p._service.side_effect = PlatformUnsupported('invocation replaced')
        with patch('kilix_sdk.bus_platform.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(PlatformUnsupported, 'invocation replaced'):
                p._connect_control('/fixture', 11.0)

    def test_unexpected_error_does_not_retry(self):
        p, clock = self.fixture([OSError(errno.EIO, 'unexpected')])
        with patch('kilix_sdk.bus_platform.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(OSError) as error:
                p._connect_control('/fixture', 11.0)
        self.assertEqual(error.exception.errno, errno.EIO)
        self.assertEqual(p.control.connect.call_count, 1)
        p.control.getsockopt.assert_not_called()

    def test_permission_error_other_than_eacces_does_not_retry(self):
        p, clock = self.fixture([PermissionError(errno.EPERM, 'policy denial')])
        with patch('kilix_sdk.bus_platform.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(PermissionError) as error:
                p._connect_control('/fixture', 11.0)
        self.assertEqual(error.exception.errno, errno.EPERM)
        self.assertEqual(p.control.connect.call_count, 1)
        p.control.getsockopt.assert_not_called()

    def test_expired_deadline_never_connects(self):
        p, clock = self.fixture()
        with patch('kilix_sdk.bus_platform.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                p._connect_control('/fixture', 10.0)
        p.control.connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
