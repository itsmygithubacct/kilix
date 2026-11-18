"""Queued systemd activation must not grant authority or fail prematurely."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_sdk import bus_supervision as supervision
from kilix_sdk.bus_ownership import CleanupIncomplete
from kilix_sdk.bus_platform import PlatformUnsupported
from kilix_sdk.bus_supervisor import WaitOwnerBootstrap


TOKEN = 'a' * 32
INSTANCE = 'fixture-' + TOKEN
SERVICE = supervision.PREFIX + INSTANCE + '.service'
CONTROLLER = ((b'C' * 16).hex(), 1000000100, '/system.slice/' + SERVICE)


class QueuedManager:
    """Start enqueues a job; later observations expose its independent progress."""
    def __init__(self, record, states):
        self.record, self.states = record, list(states)
        self.started = False
        self.clock = 10.0
        self.deadlines = []
        self.observed = []
        self.before_query = lambda: None
        self.connect = Mock()
        self._safe_directory = Mock()

    def now(self):
        return self.clock

    def sleep(self, delay):
        self.clock += delay

    def start(self, name, deadline):
        self.started = True
        self.deadlines.append(deadline)

    place_self = start

    def unit(self, name, deadline):
        self.deadlines.append(deadline)
        self.before_query()
        # Like the real transport, every call uses the original bounded budget.
        if self.clock >= deadline:
            raise TimeoutError('manager activation deadline expired')
        if not self.started:
            return None
        state = self.states[0]
        if len(self.states) > 1:
            self.states.pop(0)
        self.observed.append(state)
        return None if state is None else dict(self.record, ActiveState=state)

    scope = unit


class WaitOwnerActivation(unittest.TestCase):
    def fixture(self, states, **overrides):
        name = supervision.wait_name(INSTANCE)
        record = dict(Id=name, Transient=True, Delegate=True,
                      KillMode='control-group', SendSIGKILL=True,
                      CollectMode='inactive-or-failed',
                      Description='Kilix exact wait owner ' + INSTANCE,
                      InvocationID=b'W' * 16, ControlGroup='/system.slice/' + name)
        record.update(overrides)
        manager = QueuedManager(record, states)
        native = SimpleNamespace(
            instance=INSTANCE, identity=CONTROLLER, manager=manager,
            ops=SimpleNamespace(now=manager.now, sleep=manager.sleep,
                                ready=Mock(return_value=False)),
            establish_parent=Mock(), establish_subreaper=Mock(),
            self_pid=lambda: 1000000101, verify_placement=Mock(),
            wait_identity=None, controller_fd=object())
        transaction = WaitOwnerBootstrap(native)

        def no_birth():
            self.assertFalse(transaction.committed)
            self.assertIsNone(native.wait_identity)
            with self.assertRaises(CleanupIncomplete):
                transaction.birth_authority()

        manager.before_query = no_birth
        native.verify_placement.side_effect = lambda identity: no_birth()
        return transaction, native, manager

    def test_queued_inactive_scope_can_activate_before_original_deadline(self):
        states = ['inactive', None, 'activating', 'inactive', 'active']
        transaction, native, manager = self.fixture(states)
        identity = transaction.establish(timeout=1)
        self.assertEqual(manager.observed, states)
        self.assertEqual(set(manager.deadlines), {11.0})
        self.assertEqual(identity, ((b'W' * 16).hex(), 1000000101,
                                   '/system.slice/' + supervision.wait_name(INSTANCE)))
        native.establish_parent.assert_called_once_with()
        native.establish_subreaper.assert_called_once_with()
        native.verify_placement.assert_called_once_with(identity)
        self.assertTrue(transaction.committed)
        self.assertEqual(native.wait_identity, identity)
        transaction.birth_authority()
        native.ops.ready.return_value = True
        with self.assertRaisesRegex(CleanupIncomplete, 'controller-lost'):
            transaction.birth_authority()

    def test_failed_scope_still_refuses_birth_immediately(self):
        transaction, native, manager = self.fixture(['failed', 'active'])
        with self.assertRaisesRegex(RuntimeError, 'scope failed before birth'):
            transaction.establish(timeout=1)
        self.assertEqual(manager.observed, ['failed'])
        self.assertEqual(manager.clock, 10.0)
        native.verify_placement.assert_not_called()
        self.assertTrue(transaction.attempted)
        with self.assertRaises(CleanupIncomplete):
            transaction.birth_authority()

    def test_permanently_inactive_scope_expires_without_birth(self):
        transaction, native, manager = self.fixture(['inactive'])
        with self.assertRaises(TimeoutError):
            transaction.establish(timeout=.025)
        self.assertEqual(set(manager.deadlines), {10.025})
        self.assertLess(manager.clock, 10.036)
        native.verify_placement.assert_not_called()
        self.assertTrue(transaction.attempted)
        self.assertFalse(transaction.committed)
        with self.assertRaises(CleanupIncomplete):
            transaction.birth_authority()

    def test_delayed_active_scope_still_requires_exact_identity_and_placement(self):
        for overrides in ({'Delegate': False}, {'InvocationID': b'C' * 16},
                          {'ControlGroup': CONTROLLER[2]}):
            with self.subTest(overrides=overrides):
                transaction, native, manager = self.fixture(['inactive', 'active'], **overrides)
                with self.assertRaises(PlatformUnsupported):
                    transaction.establish(timeout=1)
                self.assertEqual(manager.observed, ['inactive', 'active'])
                native.verify_placement.assert_not_called()
                with self.assertRaises(CleanupIncomplete):
                    transaction.birth_authority()
        transaction, native, _manager = self.fixture(['inactive', 'active'])
        native.verify_placement.side_effect = RuntimeError('wrong physical scope')
        with self.assertRaisesRegex(RuntimeError, 'wrong physical scope'):
            transaction.establish(timeout=1)
        with self.assertRaises(CleanupIncomplete):
            transaction.birth_authority()


class SupervisorActivation(unittest.TestCase):
    def fixture(self, states, **overrides):
        argv = ['/usr/bin/python3', '-I',
                supervision.installed_program(Path(supervision.__file__).parent), INSTANCE]
        record = dict(Id=SERVICE, FragmentPath=supervision.FRAGMENT,
                      User='root', Type='exec', ExitType='main', Restart='no',
                      Delegate=True, KillMode='control-group',
                      CollectMode='inactive-or-failed', SendSIGKILL=True,
                      ExecStart=[('/usr/bin/python3', argv)],
                      InvocationID=b'C' * 16, MainPID=CONTROLLER[1],
                      ControlGroup=CONTROLLER[2])
        record.update(overrides)
        manager = QueuedManager(record, states)
        p = object.__new__(supervision.SupervisedPlatform)
        p.instance, p.name, p.token = INSTANCE, SERVICE, TOKEN
        p.manager = manager
        p.ops = SimpleNamespace(now=manager.now, sleep=manager.sleep)
        p.identity = p.pidfd = p.control = p.wait_owner = p.wait_pidfd = None
        p.started = p.wait_receive_required = False
        p._installed = Mock()
        p._connect_control = Mock()
        p.handles = Mock()
        p._authenticate_wait_packet = Mock(return_value=('authenticated-wait-owner', object(), object()))

        def no_launch():
            self.assertIsNone(p.identity)
            p.handles.allocate.assert_not_called()
            p._connect_control.assert_not_called()
            p._authenticate_wait_packet.assert_not_called()

        manager.before_query = no_launch
        return p, manager

    def prepare(self, p, manager, timeout=1):
        with patch('kilix_sdk.bus_platform.time.monotonic', manager.now), \
                patch.object(supervision, 'receive', return_value=({}, [object()])) as receive:
            p.prepare(timeout)
        return receive

    def test_queued_service_waits_for_active_before_handshake(self):
        states = ['inactive', None, 'activating', 'inactive', 'active']
        p, manager = self.fixture(states)
        receive = self.prepare(p, manager)
        self.assertEqual(manager.observed, states)
        self.assertEqual(set(manager.deadlines), {11.0})
        self.assertEqual(p.identity, CONTROLLER)
        p._installed.assert_called_once_with()
        p._connect_control.assert_called_once_with(supervision.RUNTIME + '/' + INSTANCE, 11.0)
        receive.assert_called_once()
        p._authenticate_wait_packet.assert_called_once_with(11.0)
        self.assertEqual(p.wait_owner, 'authenticated-wait-owner')
        self.assertTrue(p.started and p.wait_receive_required)

    def test_failed_service_still_refuses_handshake_immediately(self):
        p, manager = self.fixture(['failed', 'active'])
        with self.assertRaisesRegex(PlatformUnsupported, 'supervisor startup failed') as error:
            self.prepare(p, manager)
        self.assertIs(error.exception.resource, p)
        self.assertEqual(manager.observed, ['failed'])
        self.assertEqual(manager.clock, 10.0)
        p._connect_control.assert_not_called()
        self.assertTrue(p.started and p.wait_receive_required)

    def test_permanently_inactive_service_expires_without_handshake(self):
        p, manager = self.fixture(['inactive'])
        with self.assertRaisesRegex(PlatformUnsupported, 'deadline expired') as error:
            self.prepare(p, manager, timeout=.025)
        self.assertIsInstance(error.exception.__cause__, TimeoutError)
        self.assertIs(error.exception.resource, p)
        self.assertEqual(set(manager.deadlines), {10.025})
        self.assertLess(manager.clock, 10.036)
        p.handles.allocate.assert_not_called()
        p._connect_control.assert_not_called()
        self.assertTrue(p.started and p.wait_receive_required)

    def test_delayed_active_service_still_requires_fixed_root_delivery(self):
        for overrides in ({'User': 'fixture'}, {'ExecStart': []}, {'MainPID': 0},
                          {'ControlGroup': '/system.slice/unrelated.service'}):
            with self.subTest(overrides=overrides):
                p, manager = self.fixture(['inactive', 'active'], **overrides)
                with self.assertRaises(PlatformUnsupported):
                    self.prepare(p, manager)
                self.assertEqual(manager.observed, ['inactive', 'active'])
                p.handles.allocate.assert_not_called()
                p._connect_control.assert_not_called()


if __name__ == '__main__':
    unittest.main()
