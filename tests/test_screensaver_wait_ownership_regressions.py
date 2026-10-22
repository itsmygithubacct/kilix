"""Inert regressions for wait-owner collection and descriptor publication seams."""
import json
import ast
import array
import errno
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
from kilix_sdk import bus_ownership as ownership
from kilix_sdk import bus_platform as platform
from kilix_sdk import bus_supervision as supervision
from kilix_sdk import bus_supervisor as supervisor_module
import stream as stream_module


class FakeFilesystem:
    def __init__(self):
        self.closed = []

    def fstat(self, fd):
        return types.SimpleNamespace(st_dev=7, st_ino=int(fd), st_mode=0o100600)

    def close(self, fd):
        self.closed.append(int(fd))


class CountingHandles(supervision.Handles):
    def __init__(self, filesystem):
        super().__init__(filesystem.close, filesystem)
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        return super().close()


class FakeManager:
    def __init__(self):
        self.scope_calls = []
        self.close_calls = 0

    def scope(self, name, deadline):
        self.scope_calls.append((name, deadline))
        return None

    def unit(self, *args):
        return None

    def close(self):
        self.close_calls += 1


class FakeProcessOps:
    def __init__(self, pid, collected=False):
        self.pid = pid
        self.collected = collected

    def pidfd_pid(self, _fd):
        return self.pid

    def ready(self, _fd, _timeout=0):
        return True

    def reaped(self, _fd):
        return self.collected

    def now(self):
        return 0.0


class FakeScopeTransport:
    def __init__(self, scope):
        self.scope = scope
        self.stopped = False
        self.retire_poll = 0
        self.closed = 0
        self.cgroup = "/user.slice/" + scope.name
        self.object_path = "/org/freedesktop/systemd1/unit/" + scope.name.replace("-", "_").replace(".", "_")

    def attest(self, _deadline):
        return None

    def create(self, *_args):
        return None

    def member(self, _pid, _deadline):
        return self.object_path

    def unit(self, _name, _deadline):
        if self.stopped:
            self.retire_poll += 1
            if self.retire_poll > 1:
                return None
            state, cgroup = "inactive", ""
        else:
            state, cgroup = "active", self.cgroup
        return {
            "Id": self.scope.name,
            "Description": self.scope.description,
            "Transient": True,
            "Delegate": True,
            "KillMode": "control-group",
            "SendSIGKILL": True,
            "TimeoutStopUSec": 1500000,
            "CollectMode": "inactive-or-failed",
            "InvocationID": b"i" * 16,
            "ControlGroup": cgroup,
            "ActiveState": state,
            "object_path": self.object_path,
        }

    def stop(self, _name, _deadline):
        self.stopped = True

    def close(self):
        self.closed += 1


class FakeDelegationOS:
    O_RDONLY, O_DIRECTORY, O_NOFOLLOW, O_CLOEXEC = 0, 1, 2, 4

    def __init__(self):
        self.next_fd = 89100
        self.labels = {}
        self.closed = []

    def open(self, path, _flags, **_kwargs):
        fd = self.next_fd
        self.next_fd += 1
        self.labels[fd] = path
        return fd

    def read(self, fd, _size):
        return b"202\n" if self.labels[fd] == "cgroup.procs" else b""

    def fstat(self, fd):
        return types.SimpleNamespace(st_dev=1, st_ino=fd, st_mode=0o40755, st_uid=0)

    def close(self, fd):
        self.closed.append(int(fd))


def platform_scope_fixture():
    filesystem = FakeDelegationOS()
    delegation = platform.LinuxDelegation()
    delegation._closer = ownership.RetainedCloser(filesystem.close, filesystem)
    scope = platform.PlatformScope("f" * 32, filesystem=delegation)
    scope.manager_root = "/user.slice"
    transport = FakeScopeTransport(scope)
    scope.transport = transport
    scope._scope_retired = False
    boundary = types.SimpleNamespace(retire=lambda: None)
    delegation.boundary = lambda _root, _instance: boundary
    return filesystem, delegation, scope, transport, boundary


def platform_for_wait_transaction(filesystem):
    value = object.__new__(supervision.SupervisedPlatform)
    value.closed = False
    value.launch_attempted = False
    value.receipt = None
    value.boundary = None
    value._boundary_retired = False
    value.custodian = None
    value.started = False
    value.pidfd = None
    value.control = None
    value.wait_receive = supervision.WaitReceive()
    value.pending_wait_packet = None
    value.pending_wait_collected = False
    value.wait_receive_required = False
    value.wait_owner = None
    value.wait_handoff = None
    value.wait_pidfd = None
    value.handles = CountingHandles(filesystem)
    value.manager = FakeManager()
    value.ops = FakeProcessOps(202)
    value.token = "a" * 32
    value.instance = "worker-" + value.token
    return value


class WaitOwnershipRegressions(unittest.TestCase):
    def test_receive_started_before_publication_blocks_terminal_retirement(self):
        fs = FakeFilesystem()
        value = platform_for_wait_transaction(fs)
        fd = 88000
        value.identity = ("b" * 32, 101, "/system.slice/supervisor.service")
        payload = json.dumps({"version": supervision.VERSION, "op": "wait-owner",
                              "instance": value.token, "supervisor": list(value.identity),
                              "wait_owner": ["c" * 32, 202, "/system.slice/wait-owner.scope"]}).encode()

        class Control:
            def recvmsg(self, *_args):
                rights = array.array("i", [fd]).tobytes()
                return (payload, [(supervision.socket.SOL_SOCKET,
                                   supervision.socket.SCM_RIGHTS, rights)], 0, None)

        original_packet = value.handles.packet
        observed = []

        def interrupt_after_delivery(control, size, contract):
            packet = original_packet(control, size, contract)
            observed.append((contract[2].started, packet))
            raise KeyboardInterrupt("cancel after recvmsg publication, before receive retention")

        value.handles.packet = interrupt_after_delivery
        with self.assertRaises(KeyboardInterrupt):
            supervision.receive(Control(), 1, value.handles, transaction=value.wait_receive)

        self.assertEqual(len(observed), 1)
        self.assertTrue(observed[0][0])
        self.assertIsNotNone(value.handles.pending)
        self.assertIsNotNone(value.handles.pending["raw"])
        self.assertIsNotNone(value.handles.pending["packet"])
        with self.assertRaises(ownership.CleanupIncomplete) as caught:
            value.retire()

        self.assertFalse(value.closed)
        self.assertEqual(value.handles.close_calls, 0)
        self.assertEqual(value.manager.close_calls, 0)
        self.assertIsNotNone(value.handles.pending)
        self.assertIsNotNone(value.wait_receive.packet,
                             repr((caught.exception.problems, value.handles.pending)))
        self.assertTrue(value.wait_receive.captured)
        self.assertEqual(len(value.wait_receive.leases), 1)
        self.assertEqual(fs.closed, [])

        value.ops.collected = True
        value.retire()
        self.assertTrue(value.closed)
        self.assertTrue(value.pending_wait_collected)
        self.assertEqual(fs.closed, [fd])
        self.assertEqual(value.manager.close_calls, 1)

    def test_receive_capture_cancellation_retains_ambiguity_without_recapture(self):
        fs = FakeFilesystem()
        value = platform_for_wait_transaction(fs)
        fd = 88003
        value.identity = ("b" * 32, 101, "/system.slice/supervisor.service")
        payload = json.dumps({"version": supervision.VERSION, "op": "wait-owner",
                              "instance": value.token, "supervisor": list(value.identity),
                              "wait_owner": ["c" * 32, 202, "/system.slice/wait-owner.scope"]}).encode()

        class Control:
            def recvmsg(self, *_args):
                rights = array.array("i", [fd]).tobytes()
                return (payload, [(supervision.socket.SOL_SOCKET,
                                   supervision.socket.SCM_RIGHTS, rights)], 0, None)

        real_fstat = fs.fstat
        fstat_calls = []

        def interrupt_first_capture(number):
            fstat_calls.append(number)
            raise KeyboardInterrupt("cancel during original wait-FD incarnation capture")

        fs.fstat = interrupt_first_capture
        with self.assertRaises(KeyboardInterrupt):
            supervision.receive(Control(), 1, value.handles, transaction=value.wait_receive)
        lease = value.wait_receive.leases[fd]
        self.assertTrue(value.wait_receive.capture_started)
        self.assertFalse(value.wait_receive.captured)
        self.assertIsNone(lease.identity)

        with self.assertRaises(ownership.CleanupIncomplete):
            value.retire()
        value.ops.collected = True
        with self.assertRaises(ownership.CleanupIncomplete):
            value.retire()
        self.assertEqual(fstat_calls, [fd])
        self.assertIs(value.wait_receive.leases[fd], lease)
        self.assertFalse(value.closed)
        self.assertEqual(fs.closed, [])
        self.assertEqual(value.manager.close_calls, 0)

    def test_exited_wait_pidfd_without_collection_keeps_original_lease(self):
        fs = FakeFilesystem()
        value = platform_for_wait_transaction(fs)
        value.wait_receive = None
        value.identity = ("b" * 32, 101, "/system.slice/supervisor.service")
        value.ops = FakeProcessOps(202, collected=False)
        value.pending_wait_collected = False
        fd = 88001
        value.handles.add(fd)
        packet = {
            "version": supervision.VERSION,
            "op": "wait-owner",
            "instance": value.token,
            "supervisor": list(value.identity),
            "wait_owner": ["c" * 32, 202, "/system.slice/wait-owner.scope"],
        }
        value.pending_wait_packet = (json.dumps(packet).encode(), (fd,), False, 0)

        with self.assertRaises(ownership.CleanupIncomplete):
            value._collect_pending_wait(10.0)

        self.assertFalse(value.pending_wait_collected)
        self.assertIn(fd, value.handles.fds)
        self.assertEqual(value.handles.leases[fd].fd, fd)
        self.assertEqual(value.manager.scope_calls, [])

        value.ops.collected = True
        value._collect_pending_wait(10.0)
        self.assertTrue(value.pending_wait_collected)
        self.assertEqual(len(value.manager.scope_calls), 1)

    def test_repeated_socket_promotion_reuses_one_close_lease(self):
        fs = FakeFilesystem()
        handles = supervision.Handles(fs.close, fs)

        class Socket:
            def fileno(self):
                return 88002

        class Wrapper:
            def __init__(self, sock, lease):
                self.socket, self.lease = sock, lease

        sock = Socket()
        publish = handles.publish_pending
        interrupted = []

        def lose_promotion_return():
            recipient = publish()
            if not interrupted:
                interrupted.append(True)
                raise KeyboardInterrupt("cancel after saved socket promotion, before caller return")
            return recipient

        handles.publish_pending = lose_promotion_return
        with self.assertRaises(KeyboardInterrupt):
            handles.promote_socket(sock, Wrapper)
        first = handles._socket_promotions[sock]["recipient"]
        begin = handles.begin
        reentry = []

        def record_begin(*args, **kwargs):
            reentry.append(args)
            return begin(*args, **kwargs)

        handles.begin = record_begin
        second = handles.promote_socket(sock, Wrapper)
        self.assertIs(first, second)
        self.assertIs(first.lease, second.lease)

        handles.transfer_object(first)
        third = handles.promote_socket(sock, Wrapper)
        self.assertIs(first, third)
        self.assertEqual(reentry, [])
        first.lease.close()
        second.lease.close()
        third.lease.close()
        self.assertEqual(fs.closed, [88002])

    def test_delegation_root_waits_for_exact_recipient_acknowledgement(self):
        class FakeOS:
            O_RDONLY, O_DIRECTORY, O_NOFOLLOW, O_CLOEXEC = 0, 1, 2, 4

            def __init__(self):
                self.next_fd = 89000
                self.labels = {}
                self.opened = []
                self.closed = []

            def open(self, path, _flags, **_kwargs):
                fd = self.next_fd
                self.next_fd += 1
                self.labels[fd] = path
                self.opened.append(fd)
                return fd

            def read(self, fd, _size):
                return b"202\n" if self.labels[fd] == "cgroup.procs" else b""

            def fstat(self, fd):
                return types.SimpleNamespace(st_dev=1, st_ino=fd, st_mode=0o40755,
                                             st_uid=0)

            def close(self, fd):
                self.closed.append(int(fd))

        fs = FakeOS()
        delegate = platform.LinuxDelegation()
        delegate._closer = ownership.RetainedCloser(fs.close, fs)
        recipient = object()
        other = object()
        with mock.patch.object(platform, "os", fs), mock.patch.object(
                platform.CgroupBoundary, "_filesystem", staticmethod(lambda _fd: None)):
            root = delegate.open("/slice/owned.scope", 202, recipient=recipient)
            with self.assertRaises(ownership.CleanupIncomplete):
                delegate.close_pending()
            self.assertIn(root, delegate.pending)
            self.assertNotIn(int(root), fs.closed)
            count = len(fs.opened)
            with self.assertRaises(ownership.CleanupIncomplete):
                delegate.open("/slice/other.scope", 202, recipient=other)
            self.assertEqual(len(fs.opened), count)
            self.assertIs(delegate.open("/slice/owned.scope", 202, recipient=recipient), root)
            with self.assertRaises(ownership.CleanupIncomplete):
                delegate.acknowledge(other, root)
            self.assertIn(root, delegate.pending)
            self.assertNotIn(int(root), fs.closed)
            self.assertIs(delegate.acknowledge(recipient, root), root)
            self.assertNotIn(root, delegate.pending)
            self.assertIs(delegate.acknowledge(recipient, root), root)
            self.assertNotIn(int(root), fs.closed)
            delegate.close(root)
        self.assertEqual(fs.closed.count(int(root)), 1)

    def test_native_ancestor_uses_stable_root_recipient_and_ack(self):
        class FakeDelegation:
            def __init__(self):
                self.opens = []
                self.acks = []
                self.root = object()
                self.boundary_value = types.SimpleNamespace(
                    outer=types.SimpleNamespace(join_fd=89003),
                    inner=types.SimpleNamespace(join_fd=89004))

            def open(self, path, pid, *, recipient):
                self.opens.append((path, pid, recipient))
                return self.root

            def acknowledge(self, recipient, fd):
                self.acks.append((recipient, fd))
                return fd

            def boundary(self, fd, _instance):
                self.boundary_fd = fd
                return self.boundary_value

        value = object.__new__(supervisor_module.NativeAncestor)
        value.session_path = None
        value.root = None
        value.root_recipient = object()
        value.delegation = FakeDelegation()
        value.identity = ("d" * 32, 203, "/system.slice/supervisor.service")
        value.token = "e" * 32
        value.uid = 1000
        with mock.patch.object(supervisor_module, "os", types.SimpleNamespace(
                fchown=lambda *_args: None, fchmod=lambda *_args: None)):
            boundary = value.allocate()
            value.allocate()
        self.assertIs(boundary, value.boundary)
        self.assertEqual(value.delegation.opens,
                         [(value.identity[2], value.identity[1], value.root_recipient)])
        self.assertEqual(value.delegation.acks,
                         [(value.root_recipient, value.delegation.root)] * 2)
        self.assertIs(value.root, value.delegation.root)

    def test_platform_scope_attach_acknowledges_before_publishing_boundary(self):
        fs, delegate, scope, _transport, boundary = platform_scope_fixture()
        with mock.patch.object(platform, "os", fs), mock.patch.object(
                platform.CgroupBoundary, "_filesystem", staticmethod(lambda _fd: None)):
            result = scope.attach(types.SimpleNamespace(pid=202), 88020,
                                  types.SimpleNamespace(ready=lambda *_args: False))
            self.assertIs(result, boundary)
            self.assertFalse(scope._root_handoff_started)
            self.assertIsNone(delegate._handoff)
            root = scope.root
            scope.retire()
        self.assertTrue(scope.closed)
        self.assertEqual(fs.closed.count(int(root)), 1)

    def test_platform_scope_cancelled_root_handoffs_retire_in_both_windows(self):
        for window in ("A-open-return-before-store", "B-store-before-ack"):
            with self.subTest(window=window):
                fs, delegate, scope, _transport, _boundary = platform_scope_fixture()
                original_open = delegate.open
                original_ack = delegate.acknowledge
                fired = []

                def cancel_after_open(*args, **kwargs):
                    root = original_open(*args, **kwargs)
                    if not fired:
                        fired.append("open")
                        raise KeyboardInterrupt("cancel after delegated root open")
                    return root

                def cancel_before_ack(recipient, fd):
                    if not fired:
                        fired.append("ack")
                        raise KeyboardInterrupt("cancel after root store, before acknowledgement")
                    return original_ack(recipient, fd)

                if window.startswith("A-"):
                    delegate.open = cancel_after_open
                else:
                    delegate.acknowledge = cancel_before_ack
                with mock.patch.object(platform, "os", fs), mock.patch.object(
                        platform.CgroupBoundary, "_filesystem", staticmethod(lambda _fd: None)):
                    with self.assertRaises(KeyboardInterrupt):
                        scope.attach(types.SimpleNamespace(pid=202), 88020,
                                     types.SimpleNamespace(ready=lambda *_args: False))
                    handoff = delegate._handoff
                    self.assertIsNotNone(handoff)
                    root = handoff["fd"]
                    if window.startswith("A-"):
                        self.assertIsNone(scope.root)
                    else:
                        self.assertIs(scope.root, root)
                    scope.retire()
                self.assertTrue(scope.closed)
                self.assertFalse(scope._root_handoff_started)
                self.assertIsNone(delegate._handoff)
                self.assertEqual(fs.closed.count(int(root)), 1)

    def test_cgroup_mkdir_unknown_outcome_discharges_by_exact_rmdir(self):
        class FakeOS:
            def __init__(self):
                self.created = set()
                self.closed = []
                self.rmdir_calls = []
                self.block_first_rmdir = True

            def mkdir(self, name, mode, *, dir_fd):
                self.created.add((dir_fd, name))
                raise KeyboardInterrupt("cancel after mkdir effect, before return")

            def rmdir(self, name, *, dir_fd):
                self.rmdir_calls.append((dir_fd, name))
                if self.block_first_rmdir:
                    self.block_first_rmdir = False
                    raise OSError(errno.ENOTEMPTY, "leaf is still present")
                if (dir_fd, name) not in self.created:
                    raise FileNotFoundError(errno.ENOENT, "leaf already absent")
                self.created.remove((dir_fd, name))

            def fstat(self, fd):
                return types.SimpleNamespace(st_dev=1, st_ino=fd, st_mode=0o40755)

            def close(self, fd):
                self.closed.append(int(fd))

        fs = FakeOS()
        with mock.patch.object(ownership, "os", fs), mock.patch.object(
                ownership.CgroupBoundary, "_filesystem", staticmethod(lambda _fd: None)):
            caught = None
            try:
                ownership.CgroupBoundary.create_at(89010, "owned.scope")
            except BaseException as error:
                caught = error
            self.assertIsInstance(caught, ownership.CleanupIncomplete)
            allocation = caught.resource
            self.assertEqual(allocation.creation_state, "pending")
            self.assertEqual(fs.created, {(89010, "owned.scope")})
            self.assertNotIn(89010, fs.closed)
            fs.created.clear()  # The manager proved the scope absent before boundary retirement.
            allocation.retire()
            self.assertEqual(allocation.creation_state, "absent")
            self.assertEqual(fs.rmdir_calls,
                             [(89010, "owned.scope"), (89010, "owned.scope")])
            self.assertEqual(fs.closed, [89010])

    def test_cgroup_mkdir_unknown_outcome_discharges_only_on_a_removed_leaf(self):
        class FakeOS:
            def __init__(self, error):
                self.error = error
                self.closed = []

            def mkdir(self, name, mode, *, dir_fd):
                raise KeyboardInterrupt("cancel after mkdir effect, before return")

            def rmdir(self, name, *, dir_fd):
                raise OSError(self.error, "scripted rmdir outcome")

            def fstat(self, fd):
                return types.SimpleNamespace(st_dev=1, st_ino=fd, st_mode=0o40755)

            def close(self, fd):
                self.closed.append(int(fd))

        # ENOENT, or ENODEV (kernfs, removed cgroup node), mean no live leaf
        # remains; any other refusal keeps the leaf pending.
        for error, discharged in ((errno.ENOENT, True), (errno.ENODEV, True),
                                  (errno.EACCES, False), (errno.EBUSY, False)):
            with self.subTest(error=errno.errorcode[error]):
                fs = FakeOS(error)
                with mock.patch.object(ownership, "os", fs), mock.patch.object(
                        ownership.CgroupBoundary, "_filesystem", staticmethod(lambda _fd: None)):
                    try:
                        ownership.CgroupBoundary.create_at(89030, "owned.scope")
                    except BaseException as caught:
                        allocation = caught.resource
                    if discharged:
                        allocation.retire()
                        self.assertEqual(allocation.creation_state, "absent")
                        self.assertEqual(fs.closed, [89030])
                    else:
                        with self.assertRaises(ownership.CleanupIncomplete):
                            allocation.retire()
                        self.assertEqual(allocation.creation_state, "pending")
                        self.assertNotIn(89030, fs.closed)

    def test_native_ancestor_cancelled_root_handoffs_retire_in_both_windows(self):
        class FakeDelegation:
            def __init__(self):
                self.root = object()
                self.pending = None
                self.acks = []
                self.closed = []

            def open(self, path, pid, *, recipient, **_kwargs):
                if self.pending is None:
                    self.pending = (recipient, self.root)
                elif self.pending[0] is not recipient:
                    raise ownership.CleanupIncomplete("wrong root recipient", resource=self)
                return self.pending[1]

            def acknowledge(self, recipient, fd):
                if self.pending != (recipient, fd):
                    raise ownership.CleanupIncomplete("wrong root handoff", resource=self)
                self.acks.append((recipient, fd))
                self.pending = None
                return fd

            def close(self, fd):
                self.closed.append(fd)

            def close_pending(self):
                if self.pending is not None:
                    raise ownership.CleanupIncomplete("unacknowledged root", resource=self)

        class Handles:
            fds = []

            def close(self):
                return None

        for window in ("A-open-return-before-store", "B-store-before-ack"):
            with self.subTest(window=window):
                value = object.__new__(supervisor_module.NativeAncestor)
                value.session_path = None
                value.root = value.partial = value.boundary = None
                value.root_recipient = object()
                value.delegation = FakeDelegation()
                value.identity = ("d" * 32, 203, "/system.slice/supervisor.service")
                value.token = "e" * 32
                value.uid = 1000
                value.handles = Handles()
                value.primary_exec_status = None
                value._primary_wait_attempted = False
                value.reconcile_births = lambda: None
                value.restore_credentials = lambda: None
                original_open = value.delegation.open
                original_ack = value.delegation.acknowledge
                fired = []

                def cancel_after_open(*args, **kwargs):
                    root = original_open(*args, **kwargs)
                    if not fired:
                        fired.append("open")
                        raise KeyboardInterrupt("cancel after native root open")
                    return root

                def cancel_before_ack(recipient, fd):
                    if not fired:
                        fired.append("ack")
                        raise KeyboardInterrupt("cancel before native root acknowledgement")
                    return original_ack(recipient, fd)

                if window.startswith("A-"):
                    value.delegation.open = cancel_after_open
                else:
                    value.delegation.acknowledge = cancel_before_ack
                with self.assertRaises(KeyboardInterrupt):
                    value.allocate()
                root = value.delegation.pending[1]
                if window.startswith("A-"):
                    self.assertIsNone(value.root)
                else:
                    self.assertIs(value.root, root)
                value.retire()
                self.assertIsNone(value.root)
                self.assertIsNone(value.delegation.pending)
                self.assertEqual(value.delegation.acks, [(value.root_recipient, root)])
                self.assertEqual(value.delegation.closed, [root])

    def test_stream_unknown_pipe_return_blocks_retry(self):
        value = object.__new__(stream_module.StreamSupervisor)
        value._pending_launch_allocations = []
        value._pending_launch_fds = []
        destination = []
        calls = []

        def allocator():
            calls.append("pipe2")
            return (89011, 89012)

        source = Path(stream_module.__file__).read_text().splitlines()
        line = next(index for index, text in enumerate(source, 1)
                    if 'journal["raw"] =' in text)
        code = stream_module.StreamSupervisor._acquire_stream_fds.__code__
        fired = []

        def trace(frame, event, _argument):
            if frame.f_code is code and event == "line" and frame.f_lineno == line and not fired:
                fired.append(frame.f_lineno)
                raise KeyboardInterrupt("cancel after pipe2 return, before pair journal")
            return trace

        sys.settrace(trace)
        try:
            with self.assertRaises(KeyboardInterrupt):
                value._acquire_stream_fds(allocator, many=True, destination=destination)
        finally:
            sys.settrace(None)

        self.assertEqual(calls, ["pipe2"])
        self.assertEqual(fired, [line])
        self.assertEqual(len(value._pending_launch_allocations), 1)
        self.assertIsNone(value._pending_launch_allocations[0]["raw"])
        retried = []
        with self.assertRaises(ownership.CleanupIncomplete):
            value._acquire_stream_fds(lambda: retried.append(True) or 89013,
                                      destination=destination)
        self.assertEqual(retried, [])

    def test_stream_allocation_journal_precedes_allocator_effect(self):
        value = object.__new__(stream_module.StreamSupervisor)
        value._pending_launch_allocations = []
        value._pending_launch_fds = []
        destination = []
        effects = []

        def cancel_after_effect():
            effects.append("pipe2-effect")
            raise KeyboardInterrupt("allocator effect completed before its result was saved")

        with self.assertRaises(KeyboardInterrupt):
            value._acquire_stream_fds(cancel_after_effect, many=True, destination=destination)
        self.assertEqual(effects, ["pipe2-effect"])
        self.assertEqual(len(value._pending_launch_allocations), 1)
        self.assertIsNone(value._pending_launch_allocations[0]["raw"])
        retried = []
        with self.assertRaises(ownership.CleanupIncomplete):
            value._acquire_stream_fds(lambda: retried.append(True) or (89016, 89017),
                                      many=True, destination=destination)
        self.assertEqual(retried, [])

    def test_stream_allocator_oserror_removes_journal_before_retry(self):
        fs = FakeFilesystem()
        value = object.__new__(stream_module.StreamSupervisor)
        value._pending_launch_allocations = []
        value._pending_launch_fds = []
        destination = []
        calls = []

        def allocator():
            calls.append("open")
            if len(calls) == 1:
                raise OSError(errno.EMFILE, "known allocation failure")
            return 89018

        with mock.patch.object(stream_module, "os", fs):
            with self.assertRaises(OSError):
                value._acquire_stream_fds(allocator, destination=destination)
            self.assertEqual(value._pending_launch_allocations, [])
            try:
                leases = value._acquire_stream_fds(allocator, destination=destination)
                retry_error = None
            except ownership.CleanupIncomplete as error:
                leases, retry_error = [], error
        self.assertIsNone(retry_error)
        self.assertEqual(calls, ["open", "open"])
        self.assertEqual([lease.fd for lease in leases], [89018])
        self.assertEqual(value._pending_launch_allocations, [])

    def test_stream_pipe_pair_is_published_as_one_owned_set(self):
        class FakeOS(FakeFilesystem):
            pass

        fs = FakeOS()
        value = object.__new__(stream_module.StreamSupervisor)
        value._pending_launch_allocations = []
        value._pending_launch_fds = []
        destination = []
        with mock.patch.object(stream_module, "os", fs):
            leases = value._acquire_stream_fds(lambda: (89014, 89015), many=True,
                                               destination=destination)
        self.assertEqual([lease.fd for lease in leases], [89014, 89015])
        self.assertEqual(destination, leases)
        self.assertEqual(value._pending_launch_allocations, [])


def native_ancestor(delegation):
    value = object.__new__(supervisor_module.NativeAncestor)
    value.session_path = None
    value.root = value.partial = value.boundary = None
    value.root_recipient = object()
    value.delegation = delegation
    value.identity = ("d" * 32, 202, "/system.slice/supervisor.service")
    value.token = "e" * 32
    value.uid = 1000
    value.handles = types.SimpleNamespace(fds=[], close=lambda: None)
    value.primary_exec_status = None
    value._primary_wait_attempted = False
    value.reconcile_births = lambda: None
    value.restore_credentials = lambda: None
    return value


def real_delegation(filesystem):
    delegation = platform.LinuxDelegation()
    delegation._closer = ownership.RetainedCloser(filesystem.close, filesystem)
    return delegation


class OpenRefusingDelegation:
    """Claim-less delegation whose open refuses, as ENOENT or a membership refusal."""
    def __init__(self, error):
        self.error = error
        self.opens = 0

    def open(self, *_args, **_kwargs):
        self.opens += 1
        raise self.error

    def acknowledge(self, _recipient, fd):
        return fd

    def close(self, _fd):
        return None

    def close_pending(self):
        return None


class ClaimingDelegation:
    """Claim-capable delegation with scripted open outcomes."""
    def __init__(self):
        self.opens = 0
        self.retained = None
        self.acks = []
        self.closed = []
        self.script = []

    def open(self, _path, _pid, *, recipient, **_kwargs):
        self.opens += 1
        action = self.script.pop(0) if self.script else "ok"
        if action == "cancel-before-handoff":
            raise KeyboardInterrupt("cancelled before any root was handed over")
        if action == "retain-then-raise":
            self.retained = (recipient, object())
            raise RuntimeError("failed after the root was handed over")
        self.retained = (recipient, self.retained[1] if self.retained else object())
        return self.retained[1]

    def claim(self, recipient):
        if self.retained is not None and self.retained[0] is recipient:
            return self.retained[1]
        return None

    def acknowledge(self, recipient, fd):
        if self.retained != (recipient, fd):
            raise ownership.CleanupIncomplete("wrong root handoff", resource=self)
        self.acks.append(fd)
        return fd

    def close(self, fd):
        self.closed.append(fd)
        self.retained = None

    def close_pending(self):
        return None


class RootRetirementRegressions(unittest.TestCase):
    """r2 review findings R2-1 (double close) and R2-2 (open-failure deadlock)."""

    def attach(self, scope):
        return scope.attach(types.SimpleNamespace(pid=202), 88020,
                            types.SimpleNamespace(ready=lambda *_args: False))

    def test_native_retire_is_idempotent_for_the_root_fd(self):
        fs = FakeDelegationOS()
        delegate = real_delegation(fs)
        value = native_ancestor(delegate)
        with mock.patch.object(platform, "os", fs), mock.patch.object(
                platform.CgroupBoundary, "_filesystem", staticmethod(lambda _fd: None)):
            value.root = delegate.open(value.identity[2], value.identity[1],
                                       recipient=value.root_recipient)
            delegate.acknowledge(value.root_recipient, value.root)
            root = int(value.root)
            self.assertIsNotNone(delegate.claim(value.root_recipient))
            for _ in range(3):
                value.retire()
        self.assertEqual(fs.closed.count(root), 1)
        self.assertIsNone(value.root)
        self.assertIsNone(delegate.claim(value.root_recipient))

    def test_platform_scope_retire_after_late_transport_failure_closes_root_once(self):
        fs, delegate, scope, transport, _boundary = platform_scope_fixture()
        real_close = transport.close
        failures = []

        def fail_first_close():
            real_close()
            if not failures:
                failures.append(True)
                raise RuntimeError("transient transport close failure")

        transport.close = fail_first_close
        with mock.patch.object(platform, "os", fs), mock.patch.object(
                platform.CgroupBoundary, "_filesystem", staticmethod(lambda _fd: None)):
            self.attach(scope)
            root = int(scope.root)
            with self.assertRaises(ownership.CleanupIncomplete):
                scope.retire()
            self.assertFalse(scope.closed)
            scope.retire()
            scope.retire()
        self.assertTrue(scope.closed)
        self.assertEqual(fs.closed.count(root), 1)

    def test_delegation_claim_ends_when_the_root_is_released(self):
        class LateErrorOS(FakeDelegationOS):
            late = set()

            def close(self, fd):
                super().close(fd)
                if int(fd) in self.late:
                    raise ownership.DescriptorReleased("late close error after release")

        for late_error in (False, True):
            with self.subTest(late_error=late_error):
                fs = LateErrorOS()
                delegate = real_delegation(fs)
                recipient = object()
                with mock.patch.object(platform, "os", fs), mock.patch.object(
                        platform.CgroupBoundary, "_filesystem", staticmethod(lambda _fd: None)):
                    root = delegate.open("/slice/owned.scope", 202, recipient=recipient)
                    delegate.acknowledge(recipient, root)
                    self.assertIs(delegate.claim(recipient), root)
                    if late_error:
                        fs.late.add(int(root))
                        with self.assertRaises(ownership.DescriptorReleased):
                            delegate.close(root)
                    else:
                        delegate.close(root)
                self.assertIsNone(delegate.claim(recipient))
                self.assertEqual(fs.closed.count(int(root)), 1)

    def test_native_ordinary_open_failure_then_retire_completes(self):
        for error in (OSError(errno.ENOENT, "gone"),
                      platform.PlatformUnsupported("delegation does not contain only the gated owner")):
            for pinned_session in (False, True):
                with self.subTest(error=type(error).__name__, pinned_session=pinned_session):
                    self.native_open_refused(error, pinned_session)

    def native_open_refused(self, error, pinned_session):
        delegate = OpenRefusingDelegation(error)
        value = native_ancestor(delegate)
        if pinned_session:
            value.session_path = "/user.slice/session.scope"
            value.sdk_pid = 202
            value.sdk_cgroup = "/user.slice/session.scope/app.scope"
            value.check_sdk_cgroup = lambda: None
        with self.assertRaises(type(error)):
            value.allocate()
        for _ in range(3):
            value.retire()
        self.assertEqual(delegate.opens, 1)
        self.assertFalse(value._root_handoff_started)

    def test_native_open_failure_with_real_delegation_releases_every_fd_once(self):
        fs = FakeDelegationOS()
        fs.read = lambda fd, _size: b"999\n" if fs.labels[fd] == "cgroup.procs" else b""
        delegate = real_delegation(fs)
        value = native_ancestor(delegate)
        with mock.patch.object(platform, "os", fs), mock.patch.object(
                platform.CgroupBoundary, "_filesystem", staticmethod(lambda _fd: None)):
            with self.assertRaises(platform.PlatformUnsupported):
                value.allocate()
            opened = len(fs.labels)
            for _ in range(3):
                value.retire()
            self.assertEqual(len(fs.labels), opened)  # retire never reopens
        self.assertEqual(sorted(fs.closed), sorted(fs.labels))
        self.assertEqual(len(set(fs.closed)), len(fs.closed))
        self.assertEqual(delegate.pending, [])

    def test_platform_scope_ordinary_open_failure_then_retire_completes(self):
        for error in (OSError(errno.ENOENT, "gone"),
                      platform.PlatformUnsupported("delegation does not contain only the gated owner")):
            with self.subTest(error=type(error).__name__):
                delegate = OpenRefusingDelegation(error)
                scope = platform.PlatformScope("f" * 32, filesystem=delegate)
                scope.manager_root = "/user.slice"
                transport = FakeScopeTransport(scope)
                scope.transport = transport
                scope._scope_retired = False
                with self.assertRaises(platform.PlatformUnsupported):
                    self.attach(scope)
                for _ in range(3):
                    scope.retire()
                self.assertTrue(scope.closed)
                self.assertEqual(delegate.opens, 1)
                self.assertEqual(transport.closed, 1)

    def test_platform_scope_open_failure_with_real_delegation_releases_every_fd_once(self):
        fs, delegate, scope, transport, _boundary = platform_scope_fixture()
        fs.read = lambda fd, _size: b"999\n" if fs.labels[fd] == "cgroup.procs" else b""
        with mock.patch.object(platform, "os", fs), mock.patch.object(
                platform.CgroupBoundary, "_filesystem", staticmethod(lambda _fd: None)):
            with self.assertRaises(platform.PlatformUnsupported):
                self.attach(scope)
            opened = len(fs.labels)
            scope.retire()
            scope.retire()
            self.assertEqual(len(fs.labels), opened)
        self.assertTrue(scope.closed)
        self.assertEqual(sorted(fs.closed), sorted(fs.labels))
        self.assertEqual(transport.closed, 1)

    def test_cancelled_open_without_handoff_retires_without_reopening(self):
        delegate = ClaimingDelegation()
        delegate.script = ["cancel-before-handoff"]
        value = native_ancestor(delegate)
        with self.assertRaises(KeyboardInterrupt):
            value.allocate()
        self.assertTrue(value._root_handoff_started)
        value.retire()
        value.retire()
        self.assertEqual(delegate.opens, 1)
        self.assertFalse(value._root_handoff_started)
        self.assertEqual(delegate.closed, [])

        delegate = ClaimingDelegation()
        delegate.script = ["cancel-before-handoff"]
        scope = platform.PlatformScope("f" * 32, filesystem=delegate)
        scope.manager_root = "/user.slice"
        scope.transport = FakeScopeTransport(scope)
        scope._scope_retired = False
        with self.assertRaises(KeyboardInterrupt):
            self.attach(scope)
        self.assertTrue(scope._root_handoff_started)
        scope.retire()
        self.assertTrue(scope.closed)
        self.assertEqual(delegate.opens, 1)
        self.assertEqual(delegate.closed, [])

    def test_ordinary_failure_after_a_retained_handoff_still_retires_the_root(self):
        delegate = ClaimingDelegation()
        delegate.script = ["retain-then-raise"]
        value = native_ancestor(delegate)
        with self.assertRaises(RuntimeError):
            value.allocate()
        self.assertTrue(value._root_handoff_started)
        retained = delegate.retained[1]
        value.retire()
        value.retire()
        self.assertEqual(delegate.opens, 1)
        self.assertEqual(delegate.closed, [retained])

        delegate = ClaimingDelegation()
        delegate.script = ["retain-then-raise"]
        scope = platform.PlatformScope("f" * 32, filesystem=delegate)
        scope.manager_root = "/user.slice"
        scope.transport = FakeScopeTransport(scope)
        scope._scope_retired = False
        with self.assertRaises(platform.PlatformUnsupported):
            self.attach(scope)
        self.assertTrue(scope._root_handoff_started)
        retained = delegate.retained[1]
        scope.retire()
        scope.retire()
        self.assertTrue(scope.closed)
        self.assertEqual(delegate.opens, 1)
        self.assertEqual(delegate.closed, [retained])

    def test_retire_never_reclaims_a_released_root_even_if_the_delegation_still_offers_it(self):
        class StickyDelegation(ClaimingDelegation):
            def close(self, fd):
                self.closed.append(fd)  # Keeps offering the released token.

        delegate = StickyDelegation()
        value = native_ancestor(delegate)
        value.root = delegate.open("/system.slice/supervisor.service", 202,
                                   recipient=value.root_recipient)
        delegate.acknowledge(value.root_recipient, value.root)
        root = value.root
        for _ in range(3):
            value.retire()
        self.assertEqual(delegate.closed, [root])

        delegate = StickyDelegation()
        scope = platform.PlatformScope("f" * 32, filesystem=delegate)
        scope.manager_root = "/user.slice"
        transport = FakeScopeTransport(scope)
        scope.transport = transport
        scope._scope_retired = False
        scope.root = delegate.open("/user.slice/x.scope", 202, recipient=scope._root_recipient)
        delegate.acknowledge(scope._root_recipient, scope.root)
        root = scope.root
        failures = []
        real_close = transport.close

        def fail_first_close():
            real_close()
            if not failures:
                failures.append(True)
                raise RuntimeError("transient transport close failure")

        transport.close = fail_first_close
        with self.assertRaises(ownership.CleanupIncomplete):
            scope.retire()
        scope.retire()
        scope.retire()
        self.assertTrue(scope.closed)
        self.assertEqual(delegate.closed, [root])

    def test_released_descriptor_number_is_not_registered_again_as_an_integer(self):
        fs = FakeFilesystem()
        handles = supervision.Handles(fs.close, fs)
        token = ownership.DescriptorIncarnation(89500)
        handles.add(token)
        handles.close()
        self.assertEqual(fs.closed, [89500])
        with self.assertRaises(ownership.CleanupIncomplete):
            handles.add(89500)  # The number alone cannot prove a new incarnation.
        handles.close()
        self.assertEqual(fs.closed, [89500])


class StreamUncalledAllocationRegressions(unittest.TestCase):
    """r2 review R2-5: an interrupt before the allocator ran must not trap spawn/cleanup."""

    def interrupted_before_allocator(self):
        value = object.__new__(stream_module.StreamSupervisor)
        value._pending_launch_allocations = []
        value._pending_launch_fds = []
        source = Path(stream_module.__file__).read_text().splitlines()
        line = next(index for index, text in enumerate(source, 1)
                    if 'journal["called"] = True' in text)
        code = stream_module.StreamSupervisor._acquire_stream_fds.__code__
        fired = []

        def trace(frame, event, _argument):
            if frame.f_code is code and event == "line" and frame.f_lineno == line and not fired:
                fired.append(frame.f_lineno)
                raise KeyboardInterrupt("cancel after the journal append, before the allocator call")
            return trace

        calls = []
        sys.settrace(trace)
        try:
            with self.assertRaises(KeyboardInterrupt):
                value._acquire_stream_fds(lambda: calls.append("pipe2") or (89020, 89021),
                                          many=True, destination=[])
        finally:
            sys.settrace(None)
        self.assertEqual(calls, [])
        self.assertEqual(len(value._pending_launch_allocations), 1)
        return value

    def test_interrupt_before_the_allocator_is_discharged_on_the_next_acquisition(self):
        fs = FakeFilesystem()
        value = self.interrupted_before_allocator()
        with mock.patch.object(stream_module, "os", fs):
            leases = value._acquire_stream_fds(lambda: 89022, destination=[])
        self.assertEqual([lease.fd for lease in leases], [89022])
        self.assertEqual(value._pending_launch_allocations, [])

    def test_interrupt_before_the_allocator_does_not_block_cleanup_collection(self):
        value = self.interrupted_before_allocator()
        recovered = []
        self.assertEqual(value._collect_pending_stream_leases(recovered), [])
        self.assertEqual(recovered, [])
        self.assertEqual(value._pending_launch_allocations, [])

    def test_interrupt_after_the_allocator_was_entered_still_blocks(self):
        value = object.__new__(stream_module.StreamSupervisor)
        value._pending_launch_allocations = []
        value._pending_launch_fds = []

        def allocator():
            raise KeyboardInterrupt("cancelled inside the allocator call")

        with self.assertRaises(KeyboardInterrupt):
            value._acquire_stream_fds(allocator, destination=[])
        self.assertTrue(value._pending_launch_allocations[0]["called"])
        with self.assertRaises(ownership.CleanupIncomplete):
            value._ensure_no_pending_launch_acquisition()
        self.assertEqual(len(value._collect_pending_stream_leases([])), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
