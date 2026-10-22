"""Own one private bus using a kernel boundary and exact primary pidfds.

The subreaper retains adoption accounting, and the SDK retains independent
cgroup authority for hard owner loss. Completion requires a bound receipt plus
kernel/primary exit evidence. An installed per-instance system-manager ancestor provisions the boundary
before the gated owner may create any descendant, and survives SDK/owner loss.
"""
import array
import ctypes
import json
import os
from pathlib import Path
import select
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
from threading import Lock, get_ident
import time

if __package__:
    from .process_pin import PidfdProcess, RemoteProcess
    from .bus_supervision import SupervisedPlatform as PlatformScope, Handles
    from .bus_ownership import (CgroupBoundary, InstanceBoundary, CleanupIncomplete, CompletionSocket,
                                LinuxProcesses, OwnerCleanup, SDKCleanup,
                                completion, stop_exact, release, CheckedDescriptor)
else:
    from process_pin import PidfdProcess, RemoteProcess
    from bus_supervision import SupervisedPlatform as PlatformScope, Handles
    from bus_ownership import (CgroupBoundary, InstanceBoundary, CleanupIncomplete, CompletionSocket,
                               LinuxProcesses, OwnerCleanup, SDKCleanup,
                               completion, stop_exact, release, CheckedDescriptor)


def stop_owned(fd):
    ops = LinuxProcesses()
    stop_exact(fd, ops, ops.now() + 1)


def bind_parent(parent):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != parent:
        os._exit(1)


def send_pidfd(control, data, fd):
    if control.sendmsg([data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))]) != len(data):
        raise RuntimeError("cannot transfer private bus ownership")


def receive_pidfd(control, size):
    handles = Handles(os.close, os if callable(getattr(os, "fstat", None)) else None)
    try:
        data, received, malformed, flags = handles.packet(control, size, ("pidfd", size))
        handles.register(received)
        handles.capture(received)
        if not data and not received:
            handles.pending = None
            return data, None
        if malformed or flags & (socket.MSG_CTRUNC | socket.MSG_TRUNC) or len(received) != 1:
            handles.close()
            raise RuntimeError("invalid private bus ownership transfer")
        fd = received[0]
        # The actual saved return carries its original lease and registry.
        # A caller can retain these without recapturing by the numeric value.
        fd.acquisition_lease, fd.acquisition_owner = handles.leases[fd], handles
        handles.pending = None
        return data, fd
    except BaseException as error:
        error.resource = handles
        raise


def release_received(fd):
    lease = getattr(fd, "acquisition_lease", None)
    if lease is None:
        return os.close(fd)  # Explicit inherited/typed-adapter ownership only.
    try:
        lease.close()
    finally:
        owner = getattr(fd, "acquisition_owner", None)
        if lease.fd is None and owner is not None:
            owner.transfer(fd)


class PrivateBus:
    def __init__(self, supervisor, env):
        self.control = self.completion_control = None
        self.owner_process = self.owner_pidfd = None
        self.guardian_pid = self.pidfd = None
        self.app_fd = self.gate_write = None
        self.primary_info = self.primary_sender = self.primary_process = None
        self._pending_primary = None
        self._unverified_primary = None
        self.closed = False
        self._closing = self._cleanup_verified = False
        self.instance = secrets.token_hex(16)
        self.ops = LinuxProcesses()
        self.boundary = self.cleanup_state = self.platform = None
        self._lock = threading.RLock()
        self._watch_stop = threading.Event()
        self._watcher = None
        self._watch_fd = None
        self._watch_errors = []
        self._watch_errors_reported = 0
        self._finalizer = None
        self._temporary = {}
        self._acquisitions = Handles(os.close, os if callable(getattr(os, "fstat", None)) else None)
        self._identity_receive = None
        self._identity_leaf = None
        self._pidfd_lease = self._watch_lease = None
        self._startup_parent = None
        self._construction_faults = []
        self._primary_request_id = None
        self._primary_request_data = None
        self._primary_status_read = None
        self._primary_status_write = None
        try:
            self.platform = PlatformScope(self.instance)
            if hasattr(self.platform, "set_custodian"):
                self.platform.set_custodian(self)
            self.platform.prepare()
            self.control, child = self._acquisitions.allocate_objects(socket.socketpair, many=True)
            self._temporary["child"] = child
            info, info_child = self._acquisitions.allocate_objects(socket.socketpair, type=socket.SOCK_SEQPACKET, many=True)
            self._temporary.update(info=info, info_child=info_child)
            self.completion_control, finish_child = self._acquisitions.allocate_objects(socket.socketpair, type=socket.SOCK_SEQPACKET, many=True)
            self._temporary["finish_child"] = finish_child
            bootstrap, bootstrap_child = self._acquisitions.allocate_objects(socket.socketpair, type=socket.SOCK_SEQPACKET, many=True)
            self._temporary.update(bootstrap=bootstrap, bootstrap_child=bootstrap_child)
            startup_fd = self._acquisitions.allocate(os.pidfd_open, os.getpid())
            self._startup_parent = self._acquisitions.leases[startup_fd]
            self._startup_parent.capture()
            parent_fd = self._startup_parent.fd
            self.owner_process, self.owner_pidfd, self.boundary = self.platform.launch_owner(
                supervisor, env, parent_fd, child, info_child, finish_child, bootstrap_child,
                str(Path(__file__).resolve()))
            if hasattr(self.platform, "claim_main"):
                self.platform.claim_main(self)
            self.cleanup_state = self.platform.cleanup(self.instance, self.boundary, self.ops, self.owner_process,
                                                       self.owner_pidfd, CompletionSocket(self.completion_control))
            self._start_watcher()
            send_boundary(bootstrap, self.instance, self.boundary)
            self._drop_temporary("bootstrap")
            for key in ("child", "info_child", "finish_child"):
                self._drop_temporary(key)
            deadline = time.monotonic() + 6
            if not select.select([info], [], [], max(0, deadline - time.monotonic()))[0]:
                raise RuntimeError("private app bus owner did not become ready")
            identity, self.pidfd = self._receive_identity(info, 64)
            if self.pidfd is None:
                raise RuntimeError("private app bus owner startup failed")
            self.guardian_pid = int(identity)
            payload = b""
            while b"\n" not in payload:
                timeout = deadline - time.monotonic()
                if timeout <= 0 or not select.select([self.control], [], [], timeout)[0]:
                    raise RuntimeError("private app bus did not become ready")
                data = self.control.recv(4096)
                if not data or len(payload) + len(data) > 8192:
                    raise RuntimeError("private app bus startup failed")
                payload += data
            ready = json.loads(payload)
            self.address, self.daemon_pid = ready["address"], int(ready["pid"])
            if not isinstance(self.address, str) or not self.address.startswith("unix:"):
                raise RuntimeError("invalid private app bus address")
            self._release_startup()
        except BaseException as startup_error:
            self._construction_faults.append(startup_error)
            # Every temporary and persistent authority is already on self.
            # A release error/cancellation cannot escape in a separate finally.
            try:
                self.close()
            except BaseException as cleanup_error:
                self._construction_faults.append(cleanup_error)
                if not isinstance(startup_error, Exception):
                    startup_error.resource = self
                    startup_error.cleanup_errors = tuple(self._construction_faults)
                    raise startup_error
                raise CleanupIncomplete("startup", problems=self._construction_faults,
                                        resource=self) from startup_error
            startup_error.resource = self
            startup_error.cleanup_errors = tuple(self._construction_faults)
            raise

    def _receive_identity(self, control, size):
        if self._identity_receive is None:
            self._identity_receive = ()
            try:
                self._identity_receive = (receive_pidfd(control, size),)
            except BaseException as error:
                self._identity_leaf = getattr(error, "resource", None)
                raise
        if not self._identity_receive:
            raise CleanupIncomplete("sdk-identity-receive-outcome", resource=self)
        data, fd = self._identity_receive[0]
        self._pidfd_lease = getattr(fd, "acquisition_lease", None)
        self._identity_leaf = getattr(fd, "acquisition_owner", self._identity_leaf)
        return data, fd

    def _drop_temporary(self, key):
        control = self._temporary.get(key)
        if control is None:
            return
        try:
            control.close()
        except BaseException:
            # Python sockets invalidate their own descriptor before the close
            # syscall. A cancelled/late closed object cannot be retried by number.
            if control.fileno() == -1:
                self._temporary.pop(key, None)
            raise
        else:
            self._temporary.pop(key, None)

    def _release_startup(self):
        errors = []
        for key in list(getattr(self, "_temporary", {})):
            try:
                self._drop_temporary(key)
            except BaseException as error:
                errors.append(error)
        parent = getattr(self, "_startup_parent", None)
        if parent is not None:
            try:
                parent.close()
            except BaseException as error:
                errors.append(error)
            if parent.fd is None:
                self._startup_parent = None
        unverified = getattr(self, "_unverified_primary", None)
        if unverified is not None:
            try:
                unverified.close()
            except BaseException as error:
                errors.append(error)
            if unverified.fd is None:
                self._unverified_primary = None
        if errors:
            self._construction_faults.extend(errors)
            cancellation = next((error for error in errors if not isinstance(error, Exception)), None)
            if cancellation is not None:
                cancellation.resource = self
                raise cancellation
            raise CleanupIncomplete("constructor-releases", problems=errors, resource=self)

    def _start_watcher(self):
        # One fixed SDK thread, with a duplicate exact owner pidfd. It creates
        # no helper process hierarchy and has bounded recovery attempts.
        acquisitions = getattr(self, "_acquisitions", None)
        if acquisitions is None:
            self._acquisitions = acquisitions = Handles(self.ops.close)
        fd = acquisitions.allocate(os.dup, self.owner_pidfd)
        self._watch_lease = acquisitions.leases[fd]
        self._watch_fd = fd
        acquisitions.transfer(fd)
        try:
            self._watcher = threading.Thread(target=self._watch_owner, args=(fd,),
                                            name="private-bus-" + self.instance, daemon=True)
            self._watcher.start()
        except BaseException as start_error:
            self._watch_stop.set()
            # Cancellation can interrupt Thread.start while a native thread
            # already exists but has not yet registered its start event. Keep
            # both authorities in that ambiguous case; close must join it.
            if (not isinstance(start_error, Exception) or self._watcher.is_alive() or
                self._watcher.ident is not None):
                raise
            self._watcher = None
            try:
                self._release_watcher()
            except Exception as error:
                self._watch_errors.append(error)
                self._watch_errors_reported = len(self._watch_errors)
                raise CleanupIncomplete("watcher-start", problems=(start_error, error), resource=self) from start_error
            raise

    def _release_watcher(self):
        if self._watch_fd is not None:
            lease = getattr(self, "_watch_lease", None)
            if lease is None:
                release(self._watch_fd, self.ops.close, lambda: setattr(self, "_watch_fd", None))
            else:
                fd = self._watch_fd
                try:
                    lease.close()
                finally:
                    if lease.fd is None:
                        self._acquisitions.transfer(fd)
                        self._watch_fd = None

    def _watch_owner(self, fd):
        # Ownership is on the session, including when thread creation or its
        # final release fails. A descriptor number released by Linux is never
        # retained for retry; a pre-operation adapter failure keeps authority.
        synchronous = self._watcher is None
        with self._lock:
            if self._watch_fd is None:
                self._watch_fd = fd
            elif self._watch_fd != fd:
                raise RuntimeError("watcher descriptor does not match its owner")
        try:
            while not self._watch_stop.is_set():
                if self.ops.ready(fd, .05):
                    with self._lock:
                        if not self.closed:
                            self.cleanup_state.observe_exit()
                    return
        finally:
            with self._lock:
                try:
                    self._release_watcher()
                except Exception as error:
                    self._watch_errors.append(error)
                    if synchronous:
                        # A synchronous caller directly observes this error.
                        # Background errors must instead be reported by close.
                        self._watch_errors_reported = len(self._watch_errors)
                        raise

    @property
    def watcher_release_errors(self):
        return tuple(str(error) for error in self._watch_errors)

    def _close_receipt(self):
        receipt = self.cleanup_state.receipt if self.cleanup_state is not None else None
        if self._watch_errors:
            return dict(receipt or {}, watcher_release_errors=self.watcher_release_errors)
        return receipt

    def environment(self, env):
        result = dict(env)
        for key in ("DBUS_STARTER_ADDRESS", "DBUS_STARTER_BUS_TYPE"):
            result.pop(key, None)
        result.update(DBUS_SESSION_BUS_ADDRESS=self.address, DBUS_SESSION_BUS_PID=str(self.daemon_pid))
        return result

    def launch_primary(self, command, *, env, cwd, stream_fds):
        """Ask the independent root ancestor to create the primary behind its gate."""
        with self._lock:
            if self._closing or self.closed or self.cleanup_state is None or self.platform.receipt is not None:
                raise RuntimeError("private bus unavailable or already closing")
            command = [os.fsdecode(os.fspath(arg)) for arg in command]
            env = dict(env)
            cwd = os.fsdecode(os.path.abspath(os.fspath(cwd))) if cwd is not None else None
            request_data = (tuple(command), tuple(sorted(env.items())), cwd)
            if self.primary_process is not None:
                if self._primary_request_data != request_data:
                    raise RuntimeError("primary retry changed its retained launch request")
                return self.primary_process
            if self._pending_primary is None:
                self._pending_primary = request_data
                self._primary_request_data = request_data
                self._primary_request_id = secrets.token_hex(16)
                read_fd, write_fd = self._acquisitions.allocate(os.pipe2, os.O_CLOEXEC | os.O_NONBLOCK, many=True)
                self._primary_status_read = self._acquisitions.leases[read_fd]
                self._primary_status_write = self._acquisitions.leases[write_fd]
                self._primary_status_read.capture()
                self._primary_status_write.capture()
            if self._primary_request_data != request_data:
                raise RuntimeError("primary retry changed its retained launch request")
            self._cleanup_verified = False
            status_read = self._primary_status_read.fd
            status_write = self._primary_status_write.fd
            try:
                pid, fd = self.platform.launch_primary(command, env, cwd,
                    tuple(stream_fds) + (status_write,), self._primary_request_id)
            except BaseException as error:
                error.resource = self
                raise
            process = RemoteProcess(pid, fd, status_read, command, ops=self.ops)
            self.primary_process = process
            self.cleanup_state.primary = fd
            self.platform.primary_process = process
            self._acquisitions.transfer(status_read)
            self._primary_status_read = None
            try:
                self._primary_status_write.close()
            except BaseException as error:
                if self._primary_status_write.fd is None:
                    self._primary_status_write = None
                error.resource = self
                raise
            self._primary_status_write = None
            self._pending_primary = None
            return process

    def receive_primary(self, process):
        """Compatibility callback accepts only a primary born by the root service."""
        if not isinstance(process, RemoteProcess) or process is not self.primary_process:
            raise CleanupIncomplete("primary-not-ancestor-born", resource=self)
        return process

    def bind(self, app):
        # Keep SDK primary authority even after both subprocesses acknowledge.
        with self._lock:
            if self._closing or self.closed or self.cleanup_state is None or getattr(self, "_primary_bound", False):
                raise RuntimeError("private bus unavailable or already bound")
            if app is not self.primary_process:
                raise CleanupIncomplete("primary-exact-birth-authority", resource=self)
            fd = app.fd
            self._primary_bound = True
            try:
                if self.closed or self.cleanup_state.fault or self.ops.ready(self.owner_pidfd):
                    raise RuntimeError("private bus owner exited before primary handoff")
                send_pidfd(self.control, b"A", fd)
                ready, _, _ = select.select([self.control, self.pidfd], [], [], 3)
                if self.pidfd in ready or self.control not in ready or self.control.recv(1) != b"A":
                    raise RuntimeError("private app bus did not acknowledge primary ownership")
                self.platform.commit_primary(self._primary_request_id)
                app.enable_user_signals()
                self._cleanup_verified = False
            except BaseException as handoff_error:
                try:
                    self.close()
                except Exception as cleanup_error:
                    raise CleanupIncomplete("primary-handoff", problems=(handoff_error, cleanup_error),
                                            resource=self) from handoff_error
                raise
            finally:
                self.close_gate()

    def close_gate(self):
        errors = []
        for key in ("app_fd", "gate_write"):
            fd = getattr(self, key)
            if fd is not None:
                try:
                    release(fd, self.ops.close, lambda: setattr(self, key, None))
                except Exception as error:
                    errors.append(error)
        if errors:
            raise CleanupIncomplete("primary-gate", problems=errors, resource=self)

    def close(self):
        caller = get_ident()
        attempt = (caller, Lock())
        try:
            # The builtin lock context releases on frame unwind, including a
            # cancellation in the publication finally below. It never needs a
            # Python state assignment to prove that an attempt has ended.
            with attempt[1]:
                with self._lock:
                    active = self._finalizer
                    if (isinstance(active, tuple) and len(active) == 2 and
                        type(active[1]) is type(attempt[1]) and not active[1].locked()):
                        self._finalizer = None  # Exact builtin unwind proof, including completed close.
                    if self.closed:
                        return self._close_receipt()
                    active = self._finalizer
                    if self._finalizer is not None:
                        live = (not isinstance(active, tuple) or len(active) != 2 or
                                type(active[1]) is not type(attempt[1]) or active[1].locked())
                        if live:
                            active_caller = active[0] if isinstance(active, tuple) else active
                            if active_caller == caller:
                                return {"state": "incomplete", "phase": "close-in-progress"}
                            raise CleanupIncomplete("close-in-progress", pending=("finalizer",), resource=self)
                    self._finalizer = attempt
                return self._close_attempt()
        finally:
            with self._lock:
                if self._finalizer is attempt:
                    self._finalizer = None

    def _close_attempt(self):
        with self._lock:
            self._closing = True
            errors = []
            try:
                self._release_startup()
            except Exception as error:
                errors.append(error)
            try:
                self.close_gate()
            except Exception as error:
                errors.append(error)
            # Queue intentional close with the independent ancestor BEFORE
            # owner EOF. Otherwise a normal owner exit can race its close RPC
            # and be misclassified as unrequested owner loss.
            if self.platform is not None and hasattr(self.platform, "request_close"):
                try:
                    self.platform.request_close()
                except Exception as error:
                    errors.append(error)
            if self.control is not None:
                try:
                    self.control.close()
                    self.control = None
                except Exception as error:
                    errors.append(error)
            try:
                if self.cleanup_state is not None and not self._cleanup_verified:
                    self.cleanup_state.finish()
                    self._cleanup_verified = True
                elif self.cleanup_state is None and self.owner_process is not None:
                    self._stop_bootstrap()
            except Exception as error:
                errors.append(error)
            if getattr(self, "_pending_primary", None) is not None and self.primary_process is None:
                receipt = getattr(self.platform, "receipt", None)
                reconciled = (isinstance(receipt, dict) and
                              (receipt.get("bound") is True or
                               receipt.get("bound") is False and self.platform.primary_fd is None or
                               receipt.get("no_descendant_birth") is True))
                if reconciled:
                    self._pending_primary = None
                else:
                    errors.append(CleanupIncomplete("primary-birth-identity",
                        pending=("ancestor-primary-request",), resource=self))
            if errors:
                raise CleanupIncomplete("close", problems=errors, resource=self)
            if self.platform is not None:
                self.platform.retire()
            elif self.boundary is not None:
                self.boundary.retire()
            for key in ("pidfd", "owner_pidfd"):
                fd = getattr(self, key)
                if fd is not None:
                    lease = getattr(self, "_" + key + "_lease", None)
                    if lease is None:
                        release(fd, self.ops.close, lambda: setattr(self, key, None))
                    else:
                        try:
                            lease.close()
                        finally:
                            if lease.fd is None:
                                setattr(self, key, None)
            if self.cleanup_state is not None and self.cleanup_state.primary is not None:
                if getattr(self, "primary_process", None) is not None:
                    try:
                        self.primary_process.release()
                    finally:
                        self.cleanup_state.primary = self.primary_process.fd
                        if self.cleanup_state.primary is None:
                            self.platform.primary_fd = None
                elif self.platform is not None and self.platform.primary_fd is not None:
                    def release_unpublished_primary():
                        self.platform.primary_fd = None
                        self.cleanup_state.primary = None
                    release(self.platform.primary_fd, self.ops.close, release_unpublished_primary)
                else:
                    release(self.cleanup_state.primary, self.ops.close,
                            lambda: setattr(self.cleanup_state, "primary", None))
            elif self.platform is not None and self.platform.primary_fd is not None:
                release(self.platform.primary_fd, self.ops.close,
                        lambda: setattr(self.platform, "primary_fd", None))
            if self._primary_status_write is not None:
                try:
                    self._primary_status_write.close()
                except BaseException as error:
                    errors.append(error)
                if self._primary_status_write.fd is None:
                    self._primary_status_write = None
            if self._primary_status_read is not None:
                try:
                    self._primary_status_read.close()
                except BaseException as error:
                    errors.append(error)
                if self._primary_status_read.fd is None:
                    self._primary_status_read = None
            if errors:
                raise CleanupIncomplete("primary-resource-release", problems=errors, resource=self)
            for key in ("primary_info", "primary_sender"):
                value = getattr(self, key, None)
                if value is not None:
                    value.close()
                    setattr(self, key, None)
            if self.completion_control is not None:
                self.completion_control.close()
                self.completion_control = None
            self._watch_stop.set()
            watcher = self._watcher
        # The watcher may need the lock while leaving. Join outside it; a
        # retained watcher is a close failure, never a silently dropped fd.
        if watcher is not None:
            watcher.join(timeout=.2)
            if watcher.is_alive():
                raise CleanupIncomplete("sdk-watcher", pending=("watcher",), resource=self)
        with self._lock:
            if self._watcher is watcher:
                self._watcher = None
            try:
                self._release_watcher()
            except Exception as error:
                self._watch_errors.append(error)
            unreported = self._watch_errors[self._watch_errors_reported:]
            self._watch_errors_reported = len(self._watch_errors)
            if unreported or self._watch_fd is not None:
                raise CleanupIncomplete("watcher-release", pending=("watcher-fd",) if self._watch_fd is not None else (),
                                        problems=unreported, resource=self)
            acquisitions = getattr(self, "_acquisitions", None)
            if acquisitions is not None:
                acquisitions.close()
            identity_leaf = getattr(self, "_identity_leaf", None)
            if isinstance(identity_leaf, Handles):
                identity_leaf.close()
            if getattr(self, "_identity_receive", None) == ():
                raise CleanupIncomplete("sdk-identity-receive-outcome", resource=self)
            self.closed = True
            return self._close_receipt()

    def _stop_bootstrap(self):
        # An independent service is never an SDK child or numeric retry target.
        platform = getattr(self.owner_process, "platform", None)
        if platform is not None:
            platform.retire()
            return
        # Existing pidfd authority is immutable. Without it, Popen may have been
        # reaped concurrently by StreamSupervisor, XApp, poll(), or another
        # waiter. There is NO valid numeric reacquisition in this retry path.
        if self.owner_pidfd is None:
            if getattr(self.owner_process, "returncode", None) is not None:
                return
            raise CleanupIncomplete("bootstrap-owner-identity", pending=("exact-owner-pidfd",), resource=self)
        stop_exact(self.owner_pidfd, self.ops, self.ops.now() + 1.5)
        if self.owner_process.poll() is None:
            raise CleanupIncomplete("bootstrap-owner-wait", resource=self)


def send_boundary(control, instance, boundary):
    data = json.dumps({"instance": instance, "boundary": list(boundary.identity)}).encode()
    if control.sendmsg([data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", boundary.fds()))]) != len(data):
        raise RuntimeError("short private-bus boundary handoff")


def bootstrap_owner(parent, parent_fd, control_fd, info_fd, finish_fd, bootstrap_fd, instance, *, ancestor=None):
    """One gated SDK child; retain the actual whole boundary delivery."""
    handles = Handles(os.close, os if callable(getattr(os, "fstat", None)) else None)
    control = handles.allocate_objects(socket.socket, fileno=bootstrap_fd)
    handed_off = False
    try:
        bind_parent(parent if ancestor is None else ancestor)
        ready, _, _ = select.select([control, parent_fd], [], [], 6)
        if parent_fd in ready or control not in ready:
            raise RuntimeError("private-bus delegation handoff expired")
        data, received, malformed, flags = handles.packet(control, 1024, "boundary", cloexec=socket.MSG_CMSG_CLOEXEC)
        handles.register(received)
        handles.capture(received)
        if malformed or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(received) != 8:
            raise RuntimeError("invalid private-bus boundary handoff")
        boundary = InstanceBoundary(CgroupBoundary(*received[:4]), CgroupBoundary(*received[4:]))
        if json.loads(data) != {"instance": instance, "boundary": list(boundary.identity)}:
            raise RuntimeError("unbound private-bus delegation handoff")
        control.close()
        libc = ctypes.CDLL(None, use_errno=True)
        if ancestor is None and libc.prctl(1, 0, 0, 0, 0):
            raise OSError(ctypes.get_errno(), "cannot retain bus cleanup on SDK loss")
        # The returned descriptor tokens and actual boundary are retained
        # before the consuming owner is entered. A lost leaf return remains
        # unknown, never another owner birth or a completed local handoff.
        handoff = {"kind": "owner-handoff", "raw": None, "tokens": tuple(received),
                   "recipient": boundary}
        handles.pending = handoff  # Before retiring any producer ownership.
        handed_off = True
        handles.transfer_many(received)
        if ancestor is None:
            handoff["raw"] = (bus_owner(parent, parent_fd, control_fd, info_fd, finish_fd, *received, instance),)
        else:
            handoff["raw"] = (bus_owner(parent, parent_fd, control_fd, info_fd, finish_fd, *received, instance, ancestor=ancestor),)
        handles.pending = None
        return handoff["raw"][0]
    except BaseException as error:
        error.resource = handles
        raise
    finally:
        control.close()
        if not handed_off:
            handles.close()


def bus_owner(parent, parent_fd, control_fd, info_fd, finish_fd,
              directory_fd, join_fd, kill_fd, events_fd,
              inner_directory_fd, inner_join_fd, inner_kill_fd, inner_events_fd, instance, *, ancestor=None):
    """Retain one boundary/registry until completion, even across expired attempts."""
    boundary = InstanceBoundary(CgroupBoundary(directory_fd, join_fd, kill_fd, events_fd),
                                CgroupBoundary(inner_directory_fd, inner_join_fd, inner_kill_fd, inner_events_fd))
    cleanup = OwnerCleanup(instance, boundary, LinuxProcesses(), drain_boundary=boundary.inner)
    acquisitions = Handles(os.close, os if callable(getattr(os, "fstat", None)) else None)
    receive_journal = None
    process_birth = None
    info = acquisitions.allocate_objects(socket.socket, fileno=info_fd)
    finish = acquisitions.allocate_objects(socket.socket, fileno=finish_fd)
    finish.setblocking(False)
    watch, child_watch = acquisitions.allocate_objects(socket.socketpair, type=socket.SOCK_SEQPACKET, many=True)
    guardian_fd = None
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, stop)
    failure = None
    try:
        if os.getppid() != (parent if ancestor is None else ancestor):
            raise RuntimeError("private bus owner parent changed")
        # This migration must precede ANY child birth: SDK recovery covers
        # guardians even before their inner-group migration completes.
        if os.write(join_fd, b"0") != 1:
            raise RuntimeError("cannot enter private bus instance boundary")
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0):
            raise OSError(ctypes.get_errno(), "cannot own private bus children")
        process_birth = ()
        process_birth = (subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--guardian",
            str(control_fd), str(parent_fd), str(os.getpid()), str(child_watch.fileno()), str(inner_join_fd)],
            pass_fds=(control_fd, parent_fd, child_watch.fileno(), inner_join_fd), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),)
        process = process_birth[0]
        guardian_fd = acquisitions.allocate(os.pidfd_open, process.pid)
        child_watch.close()
        os.close(control_fd)
        control_fd = None
        send_pidfd(info, str(process.pid).encode(), guardian_fd)
        info.close()
        while not stopping:
            ready, _, _ = select.select([parent_fd, guardian_fd, watch], [], [], .05)
            if parent_fd in ready or guardian_fd in ready:
                break
            if watch in ready:
                if receive_journal is not None:
                    raise CleanupIncomplete("owner-receive-already-attempted", resource=acquisitions)
                receive_journal = ()
                receive_journal = (receive_pidfd(watch, 1),)
                data, fd = receive_journal[0]
                if data != b"A" or cleanup.primary is not None:
                    if fd is not None:
                        release_received(fd)
                    break
                cleanup.primary = fd
                watch.sendall(b"A")
    except Exception as error:
        failure = error
    finally:
        # Each attempt is bounded, but an incomplete attempt keeps this owner,
        # its adopted-child pidfds and the independently pinned SDK boundary.
        # A single NACK and a final ACK cannot fill an unbounded transport queue.
        notified = False
        while True:
            try:
                receipt = cleanup.attempt()
                if (process_birth == () or receive_journal == ()
                    or acquisitions.pending is not None and acquisitions.pending["raw"] is None):
                    raise CleanupIncomplete("owner-acquisition-outcome", resource=acquisitions)
                if failure is not None:
                    receipt = completion(instance, boundary, cleanup.primary is not None,
                                         complete=False, problems=(failure,))
                payload = json.dumps(receipt).encode()
                if finish.send(payload) != len(payload):
                    raise RuntimeError("short cleanup acknowledgement")
                break
            except CleanupIncomplete as error:
                if not notified:
                    notified = True
                    nack = completion(instance, boundary, cleanup.primary is not None,
                                      complete=False, problems=(error,), pending=list(cleanup.drain.owned)[:64])
                    try:
                        finish.send(json.dumps(nack).encode())
                    except OSError:
                        pass
                time.sleep(.05)
            except OSError:
                # Cleanup proof is already complete; missing ACK will be
                # detected by the SDK, which retains its own boundary authority.
                failure = failure or RuntimeError("cleanup acknowledgement lost")
                break
        for fd in (cleanup.primary, guardian_fd, parent_fd, control_fd, *boundary.fds()):
            if fd is not None:
                lease = getattr(fd, "acquisition_lease", None)
                if fd in acquisitions.fds:
                    acquisitions.release_one(fd)
                elif lease is not None:
                    release_received(fd)
                else:
                    release_received(fd)
        acquisitions.close()
        child_watch.close()
        watch.close()
        info.close()
        finish.close()
    return 1 if failure is not None else 0


def guardian(control_fd, parent_fd, owner, watch_fd, join_fd):
    if os.getppid() != owner:
        return 1
    bind_parent(owner)
    # Write zero: the kernel migrates the calling process, never a reusable PID.
    # This precedes daemon birth; every descendant inherits this membership.
    try:
        if os.write(join_fd, b"0") != 1:
            raise RuntimeError("cannot enter private bus boundary")
    finally:
        os.close(join_fd)
    acquisitions = Handles(os.close, os if callable(getattr(os, "fstat", None)) else None)
    receive_journal = None
    bus_birth = None
    control = acquisitions.allocate_objects(socket.socket, fileno=control_fd)
    watch = acquisitions.allocate_objects(socket.socket, fileno=watch_fd)
    bus = None
    bus_fd = app_fd = None
    stopping = False
    def stopped(*_):
        nonlocal stopping
        stopping = True
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, stopped)
    try:
        daemon = shutil.which("dbus-daemon")
        if daemon is None:
            raise RuntimeError("pane apps need dbus-daemon")
        own_pid = os.getpid()
        bus_birth = ()
        bus_birth = (subprocess.Popen([daemon, "--session", "--nofork", "--print-address"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            close_fds=True, preexec_fn=lambda: bind_parent(own_pid)),)
        bus = bus_birth[0]
        bus_fd = acquisitions.allocate(os.pidfd_open, bus.pid)
        deadline = time.monotonic() + 5
        address = b""
        while b"\n" not in address:
            if stopping or time.monotonic() >= deadline or select.select([parent_fd, bus_fd], [], [], 0)[0]:
                raise RuntimeError("private app bus startup interrupted")
            if select.select([bus.stdout], [], [], .05)[0]:
                part = os.read(bus.stdout.fileno(), 4096)
                if not part or len(address) + len(part) > 8192:
                    raise RuntimeError("invalid private bus daemon startup")
                address += part
        control.sendall((json.dumps({"address": address.decode().strip(), "pid": bus.pid}) + "\n").encode())
        deadline = time.monotonic() + 6
        while not stopping:
            watched = [control, parent_fd, bus_fd] + ([app_fd] if app_fd is not None else [])
            ready, _, _ = select.select(watched, [], [], .05)
            if control in ready:
                if receive_journal is not None:
                    raise CleanupIncomplete("guardian-receive-already-attempted", resource=acquisitions)
                receive_journal = ()
                receive_journal = (receive_pidfd(control, 1),)
                data, fd = receive_journal[0]
                if data != b"A" or app_fd is not None:
                    if fd is not None:
                        release_received(fd)
                    break
                app_fd = fd
                send_pidfd(watch, b"A", app_fd)
                handoff, _, _ = select.select([watch, parent_fd, bus_fd], [], [], 3)
                if parent_fd in handoff or bus_fd in handoff or watch not in handoff or watch.recv(1) != b"A":
                    break
                # Both surviving owner and guardian own this exact primary
                # before the SDK opens the exec gate.
                control.sendall(b"A")
            if parent_fd in ready or bus_fd in ready or (app_fd is not None and app_fd in ready):
                break
            if app_fd is None and time.monotonic() >= deadline:
                break
        return 0
    finally:
        stop_owned(app_fd)
        stop_owned(bus_fd)
        if bus is not None:
            bus.wait(timeout=1)
            bus.stdout.close()
        for fd in (app_fd, bus_fd, parent_fd):
            if fd is not None:
                lease = getattr(fd, "acquisition_lease", None)
                if fd in acquisitions.fds:
                    acquisitions.release_one(fd)
                elif lease is not None:
                    release_received(fd)
                else:
                    release_received(fd)
        acquisitions.close()
        if bus_birth == () or receive_journal == ():
            raise CleanupIncomplete("guardian-acquisition-outcome", resource=acquisitions)
        control.close()
        watch.close()


def launch_primary(parent, gate_fd, command, identity_fd=None):
    raise RuntimeError("primary must be created and pinned by the independent ancestor")


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["--supervised-owner"] and len(sys.argv) == 10:
            raise SystemExit(bootstrap_owner(*map(int, sys.argv[3:9]), sys.argv[9], ancestor=int(sys.argv[2])))
        if sys.argv[1:2] == ["--bootstrap-owner"] and len(sys.argv) == 9:
            raise SystemExit(bootstrap_owner(*map(int, sys.argv[2:8]), sys.argv[8]))
        if sys.argv[1:2] == ["--guardian"] and len(sys.argv) == 7:
            raise SystemExit(guardian(*map(int, sys.argv[2:])))
        raise RuntimeError("invalid private bus invocation")
    except (OSError, ValueError, RuntimeError) as exc:
        print("kilix: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
