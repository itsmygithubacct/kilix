"""Fixed system-service controller and separate per-instance wait-owner child.

This file is run only by the fixed installed template. Native effects are not
executed by source tests; the same controller and selected adapter methods are
exercised with memory adapters. No SDK code is imported by this root
process. The only caller code is exec'd after irrevocably dropping credentials.
"""
import array
import ctypes
import errno
import fcntl
import os
from pathlib import Path
import pwd
import re
import resource
import select
import signal
import socket
import stat
import struct
import sys
import time

if not __package__:
    # -I excludes the script directory. Install only into this fixed root-owned
    # location, and verify every component before allowing a local import.
    base = "/usr/libexec/kilix-private-bus-supervisor"
    directory = str(Path(__file__).parent)
    if __name__ == "__main__":
        if (os.getuid() != 0 or os.geteuid() != 0 or not directory.startswith(base + "/") or len(Path(directory).name) != 64
            or any(ch not in "0123456789abcdef" for ch in Path(directory).name)):
            raise SystemExit("supervisor requires its fixed installed root service")
        for path in ("/usr", "/usr/libexec", base, directory):
            st = os.stat(path, follow_symlinks=False)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
                raise SystemExit("unsafe installed supervisor directory")
        sys.path.insert(0, directory)
    from bus_supervision import (AncestorCleanup, Handles, VERSION, RUNTIME, decode_instance,
                                 validate_hello, receive, send, WaitScopeManager, wait_name, wait_identity)
    from bus_ownership import LinuxProcesses, CleanupIncomplete, release, CheckedDescriptor, descriptor_incarnation
    from bus_platform import LinuxDelegation, components
else:
    from .bus_supervision import (AncestorCleanup, Handles, VERSION, RUNTIME, decode_instance,
                                  validate_hello, receive, send, WaitScopeManager, wait_name, wait_identity)
    from .bus_ownership import LinuxProcesses, CleanupIncomplete, release, CheckedDescriptor, descriptor_incarnation
    from .bus_platform import LinuxDelegation, components


def physical_session_root(cgroup):
    """Match logind's first-unit rule on a kernel-read unified cgroup path."""
    parts = components(cgroup)
    for index, part in enumerate(parts):
        if part.endswith(".slice"):
            continue
        if re.fullmatch(r"session-[A-Za-z0-9_-]+\.scope", part):
            return "/" + "/".join(parts[:index + 1])
        return None
    return None


def prctl(option, value):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(option, value, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "private-bus supervisor prctl failed")


class NativeAncestor:
    """All effectful kernel/filesystem/launch operations in one adapter."""
    def __init__(self, instance):
        self.login, self.token = decode_instance(instance)
        self.account = pwd.getpwnam(self.login)
        if self.account.pw_uid <= 0 or pwd.getpwuid(self.account.pw_uid).pw_name != self.login:
            raise ValueError("supervisor login is not a canonical nonroot account")
        self.instance = instance
        self.uid = self.account.pw_uid
        self.ops = LinuxProcesses()
        self._signal_exact = self.ops.signal
        self.ops.signal = self.signal_owned
        self._reap_exact = self.ops.reap
        self.ops.reap = self.reap_owned
        self._wait_exact = self.ops.wait_exact
        self.ops.wait_exact = self.wait_exact_owned
        self.handles = Handles(self.ops.close, os)
        self.delegation = LinuxDelegation()
        self.root = self.partial = self.boundary = None
        self.root_recipient = object()
        self._root_handoff_started = False
        self._root_released = False
        self._root_handoff_path = self._root_handoff_pid = None
        self._root_handoff_member_cgroup = None
        self.listener = self.control = None
        self.sdk_fd = self.owner_fd = None
        self.sdk_pid = self.sdk_cgroup = self.session_path = None
        self.process = None
        self.birth_pid = None
        self.birth_waited = False
        self.birth_attempted = False
        self.birth_gate_committed = False
        self.primary_attempted = False
        self.primary_request = None
        self.primary_payload = None
        self.primary_pid = None
        self.primary_fd = None
        self.primary_gate_fd = None
        self.primary_exec_error_fd = None
        self.primary_status_fd = None
        self.primary_gate_attempted = False
        self.primary_gate_committed = False
        self.primary_exec_complete = False
        self.primary_exec_error = None
        self.primary_exec_status = None
        self.primary_waited = False
        self._primary_wait_attempted = False
        self._primary_wait_result = None
        self._primary_status_state = "pending"
        self._primary_status_lease = None
        self.stopping = False
        self.identity = None

    def fork_child(self, field):
        births = getattr(self, "_births", None)
        if births is None:
            self._births = births = {}
        if field in births:
            raise CleanupIncomplete("ancestor-birth-pending", resource=self)
        births[field] = {"result": None}
        births[field]["result"] = (os.fork(),)
        return births[field]["result"][0]

    def reconcile_births(self):
        for field, journal in getattr(self, "_births", {}).items():
            if journal["result"] is None:
                raise CleanupIncomplete("ancestor-birth-outcome", resource=self)
            pid = journal["result"][0]
            if pid > 0:
                existing = getattr(self, field, None)
                if existing is not None and existing != pid:
                    raise CleanupIncomplete("ancestor-birth-conflict", resource=self)
                setattr(self, field, pid)

    def restore_credentials(self):
        credentials = os.getresuid()
        if credentials == (self.uid, self.uid, 0):
            os.setresuid(0, 0, 0)
            credentials = os.getresuid()
        if credentials != (0, 0, 0):
            raise CleanupIncomplete("ancestor-credential-authority", resource=self)

    def signal_owned(self, fd, sig):
        # A formerly unprivileged primary may exec a setuid program after its
        # handoff. Never lend root's CAP_KILL or real UID 0 to that caller.
        # This fixed ancestor has one thread and executes no caller code here.
        self.restore_credentials()
        if self.birth_pid is None and self.birth_attempted and not self.birth_gate_committed:
            # Cancellation can lose fork's returned PID before assignment.
            # The unopened gate proves that any root direct child still runs
            # this fixed prelude. Acquire/reap is owned by this sole ancestor.
            pid = self.ops.pidfd_pid(fd)
            fields = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                          if ":" in line)
            if (pid > 1 and int(fields.get("PPid", "-1")) == os.getpid()
                and fields.get("Uid", "").split() == ["0"] * 4 and self.ops.pidfd_pid(fd) == pid):
                self._signal_exact(fd, sig)
                return
        if (self.birth_pid is not None and not self.birth_waited
            and self.ops.pidfd_pid(fd) == self.birth_pid):
            # Only THIS fixed fork's unreaped child can still be privileged
            # before credential drop/migration. Never a caller-submitted PID.
            # Parent owns every wait and sets birth_waited before FD release.
            self._signal_exact(fd, sig)
            return
        if (self.primary_pid is not None and not self.primary_waited and not self.primary_exec_complete
            and self.ops.pidfd_pid(fd) == self.primary_pid):
            # Before successful exec this is only this service's fixed gated
            # prelude. Caller-controlled code has not run and the child is an
            # unreaped direct child with an exact pidfd.
            self._signal_exact(fd, sig)
            return
        try:
            os.setresuid(self.uid, self.uid, 0)
            fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines()
                          if ":" in line)
            if os.getresuid() != (self.uid, self.uid, 0) or int(fields.get("CapEff", "-1"), 16) != 0:
                raise CleanupIncomplete("ancestor-signal-credentials", resource=self)
            self._signal_exact(fd, sig)  # Kernel checks the caller UID at delivery.
        finally:
            self.restore_credentials()

    def reap_owned(self, pid):
        if pid == self.primary_pid:
            self.collect_primary()
            return self.primary_exec_status is not None
        result = self._reap_exact(pid)
        if result and pid == self.birth_pid:
            self.birth_waited = True
        return result

    def wait_exact_owned(self, fd):
        if fd == self.primary_fd:
            self.collect_primary()
            return self._primary_wait_result
        info = self._wait_exact(fd)
        return info

    def setup(self):
        if not hasattr(self, "handles"):
            self.handles = Handles(getattr(os, "close", None), os)
        if os.getuid() != 0 or os.geteuid() != 0 or os.getppid() != 1:
            raise RuntimeError("supervisor is not a system-manager child")
        # Must precede EVERY possible cleanup-owner birth.
        prctl(36, 1)  # PR_SET_CHILD_SUBREAPER
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)  # No implicit/independent reaper.
        value = os.environ.get("INVOCATION_ID", "")  # PID 1 supplies this to its fixed ExecStart.
        if len(value) != 32 or any(ch not in "0123456789abcdef" for ch in value) or not any(bytes.fromhex(value)):
            raise ValueError("missing system-manager invocation")
        lines = Path("/proc/self/cgroup").read_text().splitlines()
        unified = [line[3:] for line in lines if line.startswith("0::")]
        if len(unified) != 1:
            raise RuntimeError("supervisor requires its unified cgroup")
        group = unified[0]
        if not group.endswith("/kilix-private-bus-supervisor@" + self.instance + ".service"):
            # The allowed instance alphabet requires no systemd escaping.
            raise RuntimeError("supervisor is outside its exact service cgroup")
        self.identity = (value, os.getpid(), group)
        root = RUNTIME + "/" + self.instance
        for path in ("/run", RUNTIME, root):
            st = os.stat(path, follow_symlinks=False)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
                raise RuntimeError("unsafe supervisor runtime directory")
        self.listener = self.handles.allocate_objects(socket.socket, socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
        self.handles.transfer_object(self.listener)
        self.listener.bind(root + "/control")  # Fresh runtime directory; never unlink an existing endpoint.
        os.chmod(root + "/control", 0o666)  # Authentication is SO_PEERCRED, not a caller path.
        self.listener.listen(4)
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sig, self._stop)

    def _stop(self, *_):
        self.stopping = True

    def accept(self, deadline):
        while not self.stopping and self.ops.now() < deadline:
            if not select.select([self.listener], [], [], min(.05, deadline - self.ops.now()))[0]:
                continue
            accepted = self.handles.allocate_objects(self.listener.accept, many=True)
            control, _ = accepted
            pid, uid, _ = struct.unpack("3i", control.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != self.uid or pid <= 1:
                control.close()
                continue
            self.control = control
            self.handles.transfer_object(control)
            control.settimeout(max(.001, deadline - self.ops.now()))
            return pid, uid
        raise TimeoutError("supervisor startup expired without an authenticated SDK")

    def pidfd_pid(self, fd):
        fields = dict(line.split(":", 1) for line in Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
                      if ":" in line)
        pid = int(fields.get("Pid", "-1").strip())
        if pid <= 1:
            raise ValueError("descriptor is not a live exact process pidfd")
        return pid

    def authenticate_sdk(self, fd, pid):
        if self.pidfd_pid(fd) != pid or self.ops.ready(fd):
            raise ValueError("SDK pidfd does not match authenticated socket credentials")
        self.sdk_fd = fd
        self.sdk_pid = pid
        fields = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                      if ":" in line)
        if fields.get("Uid", "").split() != [str(self.uid)] * 4:
            raise ValueError("SDK peer does not have the canonical caller credentials")
        self.sdk_cgroup = self._sdk_cgroup()
        self.session_path = physical_session_root(self.sdk_cgroup)

    def _sdk_cgroup(self):
        if self.sdk_pid is None or self.ops.ready(self.sdk_fd):
            raise CleanupIncomplete("sdk-session-authority", resource=self)
        data = Path(f"/proc/{self.sdk_pid}/cgroup").read_text()
        if len(data) > 4096:
            raise ValueError("SDK cgroup record is oversized")
        paths = [line[3:] for line in data.splitlines() if line.startswith("0::")]
        if len(paths) != 1:
            raise ValueError("SDK has no unique unified cgroup membership")
        components(paths[0])
        if self.ops.ready(self.sdk_fd):
            raise CleanupIncomplete("sdk-session-authority", resource=self)
        return paths[0]

    def check_sdk_cgroup(self):
        if self._sdk_cgroup() != self.sdk_cgroup:
            raise CleanupIncomplete("sdk-session-moved", resource=self)

    def validate_channels(self, fds):
        for fd, expected in zip(fds, (socket.SOCK_STREAM, socket.SOCK_SEQPACKET,
                                      socket.SOCK_SEQPACKET, socket.SOCK_SEQPACKET), strict=True):
            control = self.handles.attach_socket(fd, socket.socket, OwnedSocket)
            try:
                if control.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != expected:
                    raise ValueError("wrong owner channel type")
            finally:
                control.socket.detach()  # The shared original lease still owns the FD.
                self.handles.transfer_object(control)

    def allocate(self):
        session = getattr(self, "session_path", None)
        if getattr(self, "root", None) is None:
            if session is not None:
                self.check_sdk_cgroup()
                if callable(getattr(self.delegation, "acknowledge", None)):
                    self._root_handoff_started = True
                    self._root_handoff_path, self._root_handoff_pid = session, self.sdk_pid
                    self._root_handoff_member_cgroup = self.sdk_cgroup
                    try:
                        self.root = self.delegation.open(session, self.sdk_pid,
                                                         member_cgroup=self.sdk_cgroup,
                                                         recipient=self.root_recipient)
                    except Exception:
                        self._discharge_failed_open()
                        raise
                else:
                    self.root = self.delegation.open(session, self.sdk_pid,
                                                     member_cgroup=self.sdk_cgroup)
            elif callable(getattr(self.delegation, "acknowledge", None)):
                self._root_handoff_started = True
                self._root_handoff_path, self._root_handoff_pid = self.identity[2], self.identity[1]
                self._root_handoff_member_cgroup = None
                try:
                    self.root = self.delegation.open(self.identity[2], self.identity[1],
                                                     recipient=self.root_recipient)
                except Exception:
                    # An ordinary refusal ends the attempt; only cancellation
                    # leaves the outcome unknown for retire to reconcile.
                    self._discharge_failed_open()
                    raise
            else:
                self.root = self.delegation.open(self.identity[2], self.identity[1])
        if callable(getattr(self.delegation, "acknowledge", None)):
            self.delegation.acknowledge(self.root_recipient, self.root)
            self._root_handoff_started = False
        if session is not None:
            self.check_sdk_cgroup()
        try:
            self.boundary = self.delegation.boundary(self.root, self.token)
        except CleanupIncomplete as error:
            self.partial = error.resource
            raise
        # cgroup.procs rechecks CURRENT credentials at the common ancestor,
        # even for an FD opened by root. Delegate only the two owned workload
        # migration files; the service/main and every common external ancestor
        # remain root-owned. This allows owner/guardian migration within this
        # family but cannot import a foreign process into its kill boundary.
        for leaf in (self.boundary.outer, self.boundary.inner):
            # Root's fixed birth prelude retains group-0 write permission.
            # The caller owns only these leaves; no common ancestor is altered.
            os.fchown(leaf.join_fd, self.uid, 0)
            os.fchmod(leaf.join_fd, 0o660)
        return self.boundary

    def exec_owner(self, hello, fds, ancestor, gate, limit):
        # No caller code or environment executes before this fixed prelude.
        # Parent migrates its unreaped, pinned child before opening this gate.
        prctl(1, signal.SIGKILL)
        if os.getppid() != ancestor:
            raise RuntimeError("independent supervisor died before owner gate")
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        if os.read(gate, 1) != b"G":
            raise RuntimeError("owner birth gate was not committed")
        os.close(gate)
        account = self.account
        prctl(8, 0)  # PR_SET_KEEPCAPS: do not retain root capabilities.
        os.initgroups(self.login, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
        if os.getuid() != account.pw_uid or os.geteuid() != account.pw_uid:
            raise RuntimeError("cleanup owner credential drop failed")
        prctl(38, 1)
        prctl(1, signal.SIGKILL)  # Cleared by setuid; set again AFTER UID drop.
        if os.getppid() != ancestor:
            raise RuntimeError("independent supervisor died before owner exec")
        keep = sorted(fds)
        low = 3
        for fd in keep:
            os.closerange(low, fd)
            os.set_inheritable(fd, True)
            low = fd + 1
        os.closerange(low, limit)
        argv = ["/usr/bin/python3", hello["source"], "--supervised-owner", str(ancestor), str(hello["sdk"]),
                *map(str, fds), self.token]
        os.execve("/usr/bin/python3", argv, hello["env"])

    def spawn(self, hello, fds):
        if getattr(self, "sdk_cgroup", None) is not None:
            self.check_sdk_cgroup()
        if self.birth_pid is not None:
            raise RuntimeError("owner birth already attempted")
        limit = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
        if limit == resource.RLIM_INFINITY or limit <= max(fds):
            raise RuntimeError("owner requires a finite descriptor close boundary")
        ancestor = os.getpid()
        gate, commit = self.handles.allocate(os.pipe2, os.O_CLOEXEC, many=True)
        self.handles.register((gate, commit))
        self.handles.capture((gate, commit))
        self.birth_attempted = True
        pid = self.fork_child("birth_pid")  # Single thread; explicit SIGCHLD default; no other reaper.
        if pid == 0:
            try:
                os.close(commit)
                self.exec_owner(hello, fds, ancestor, gate, limit)
            except BaseException:
                pass
            os._exit(127)
        # This internal birth identity is retained before any fallible pin or
        # migration. Popen's exec-error pipe must never block the parent monitor.
        self.birth_pid = pid
        self.process = type("OwnedBirth", (), {"pid": pid})()
        self.owner_fd = self.handles.allocate(os.pidfd_open, pid)
        if self.ops.pidfd_pid(self.owner_fd) != pid:
            raise RuntimeError("owner birth pidfd does not match unreaped child")
        data = f"{pid}\n".encode()
        if os.write(self.boundary.outer.join_fd, data) != len(data):
            raise RuntimeError("cannot migrate pinned unreaped owner birth")
        if os.write(commit, b"G") != 1:
            raise RuntimeError("cannot commit owner birth gate")
        self.birth_gate_committed = True
        errors = []
        for fd in (gate, commit, *fds[1:]):
            try:
                self.handles.release_one(fd)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise CleanupIncomplete("ancestor-owner-channel-release", problems=errors, resource=self)

    @staticmethod
    def _validate_primary(value):
        if (set(value) != {"version", "op", "instance", "request", "argv", "env", "cwd"}
            or value["op"] != "primary-launch" or not isinstance(value["request"], str)
            or len(value["request"]) != 32
            or any(ch not in "0123456789abcdef" for ch in value["request"])
            or not isinstance(value["argv"], list) or not value["argv"] or len(value["argv"]) > 4096
            or any(not isinstance(arg, str) or "\x00" in arg for arg in value["argv"])
            or not value["argv"][0]
            or not isinstance(value["env"], dict) or len(value["env"]) > 4096
            or value["cwd"] is not None and (not isinstance(value["cwd"], str) or "\x00" in value["cwd"])):
            raise ValueError("invalid primary launch request")
        if value["cwd"] is not None and not os.path.isabs(value["cwd"]):
            raise ValueError("primary working directory must be absolute")
        for key, item in value["env"].items():
            if (not isinstance(key, str) or not key or "=" in key or "\x00" in key
                or not isinstance(item, str) or "\x00" in item):
                raise ValueError("invalid primary environment")
        if sum(len(arg.encode()) for arg in value["argv"]) + sum(
                len(key.encode()) + len(item.encode()) for key, item in value["env"].items()) > 100000:
            raise ValueError("primary launch request is too large")

    def exec_primary(self, request, streams, gate, exec_error, ancestor, limit):
        """Fixed root-owned prelude; user command runs only after the UID drop."""
        try:
            prctl(1, signal.SIGKILL)
            if os.getppid() != ancestor:
                raise RuntimeError("independent supervisor died before primary gate")
            for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGPIPE):
                signal.signal(sig, signal.SIG_DFL)
            if os.read(gate, 1) != b"G":
                raise RuntimeError("primary birth gate was not committed")
            os.close(gate)
            for target, source in enumerate(streams):
                if source != target:
                    os.dup2(source, target, inheritable=True)
            prctl(8, 0)
            os.initgroups(self.login, self.account.pw_gid)
            os.setgid(self.account.pw_gid)
            os.setuid(self.account.pw_uid)
            if os.getuid() != self.uid or os.geteuid() != self.uid:
                raise RuntimeError("primary credential drop failed")
            prctl(38, 1)
            prctl(1, signal.SIGKILL)
            if os.getppid() != ancestor:
                raise RuntimeError("independent supervisor died before primary exec")
            if request["cwd"] is not None:
                os.chdir(request["cwd"])
            os.set_inheritable(exec_error, False)
            os.closerange(3, exec_error)
            os.closerange(exec_error + 1, limit)
            os.execvpe(request["argv"][0], request["argv"], request["env"])
        except BaseException as error:
            try:
                code = int(getattr(error, "errno", 0) or 0)
                payload = struct.pack("!i", code) + str(error).encode("utf-8", "replace")[:2048]
                os.write(exec_error, payload)
            except BaseException:
                pass
            os._exit(127)

    def spawn_primary(self, value, fds):
        self._validate_primary(value)
        if value["instance"] != self.token:
            raise ValueError("primary request belongs to another instance")
        if self.primary_attempted:
            if value["request"] != self.primary_request or value != self.primary_payload:
                raise RuntimeError("primary birth request conflicts with retained identity")
            if self.primary_fd is None:
                self.recover_primary_pin()
            # A duplicate packet can carry duplicate stdio/status descriptors.
            for fd in fds:
                self.handles.release_one(fd)
            return self.primary_fd
        if len(fds) != 4 or self.boundary is None or self.owner_fd is None:
            raise ValueError("primary launch lacks its owned boundary or streams")
        status_stat = os.fstat(fds[3])
        status_flags = fcntl.fcntl(fds[3], fcntl.F_GETFL)
        if (not stat.S_ISFIFO(status_stat.st_mode) or status_flags & os.O_ACCMODE != os.O_WRONLY
            or not status_flags & os.O_NONBLOCK):
            raise ValueError("primary status capability is not a nonblocking pipe writer")
        if self.ops.ready(self.sdk_fd) or self.ops.ready(self.owner_fd):
            raise CleanupIncomplete("primary-controller-lost-before-birth", resource=self)
        if getattr(self, "sdk_cgroup", None) is not None:
            self.check_sdk_cgroup()
        self.primary_attempted = True
        self.primary_request = value["request"]
        self.primary_payload = dict(value)
        self.primary_status_fd = fds[3]
        self._primary_status_lease = self.handles.leases.get(fds[3])
        limit = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
        if limit == resource.RLIM_INFINITY or limit <= max(fds):
            raise RuntimeError("primary requires a finite descriptor close boundary")
        gate_read, gate_write = self.handles.allocate(os.pipe2, os.O_CLOEXEC, many=True)
        error_read, error_write = self.handles.allocate(os.pipe2, os.O_CLOEXEC | os.O_NONBLOCK, many=True)
        self.handles.register((gate_read, gate_write, error_read, error_write))
        self.handles.capture((gate_read, gate_write, error_read, error_write))
        self.primary_gate_fd = gate_write
        self.primary_exec_error_fd = error_read
        ancestor = os.getpid()
        blocked = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
        try:
            pid = self.fork_child("primary_pid")
            if pid == 0:
                signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
                self.exec_primary(value, fds[:3], gate_read, error_write, ancestor, limit)
                os._exit(127)
            self.primary_pid = pid  # Set before unblocking asynchronous cancellation.
            self.primary_fd = self.handles.allocate(os.pidfd_open, pid)
            if self.ops.pidfd_pid(self.primary_fd) != pid:
                raise RuntimeError("primary birth pidfd does not match its unreaped child")
            # Only this ancestor's exact pinned, unreaped, gated fork birth is
            # moved. Root credentials permit cross-boundary migration; user
            # process signals still borrow caller UID with zero capabilities.
            try:
                if os.getresuid() != (0, 0, 0):
                    raise CleanupIncomplete("primary-migration-credentials", resource=self)
                data = f"{pid}\n".encode()
                if os.write(self.boundary.inner.join_fd, data) != len(data):
                    raise RuntimeError("cannot migrate exact primary birth into its boundary")
            finally:
                self.restore_credentials()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        failures = []
        for fd in (gate_read, error_write, *fds[:3]):
            try:
                self.handles.release_one(fd)
            except BaseException as error:
                failures.append(error)
        if failures:
            raise CleanupIncomplete("primary-birth-release", problems=failures, resource=self)
        return self.primary_fd

    def recover_primary_pin(self):
        # Numeric reacquisition is safe only while this one-threaded ancestor
        # still owns the direct, unreaped fork child. Never reopen after wait.
        if self.primary_pid is None or self.primary_waited or self.primary_fd is not None:
            if self.primary_fd is not None:
                return self.primary_fd
            raise CleanupIncomplete("primary-birth-authority-lost", resource=self)
        fd = self.handles.allocate(os.pidfd_open, self.primary_pid)
        if self.ops.pidfd_pid(fd) != self.primary_pid:
            self.handles.release_one(fd)
            raise CleanupIncomplete("primary-birth-pidfd-mismatch", resource=self)
        self.primary_fd = fd
        return fd

    def commit_primary(self, request):
        if not self.primary_attempted or request != self.primary_request:
            raise RuntimeError("primary commit has no retained gated birth")
        if self.primary_exec_complete:
            if self.primary_exec_error is not None:
                raise self.primary_exec_error
            return self.primary_pid
        if self.ops.ready(self.sdk_fd) or self.ops.ready(self.owner_fd):
            raise CleanupIncomplete("primary-controller-lost-before-exec", resource=self)
        if not self.primary_gate_committed:
            if self.primary_gate_attempted:
                ready, _, _ = select.select([self.primary_exec_error_fd], [], [], 0)
                if ready:
                    self.primary_gate_committed = True
            if not self.primary_gate_committed:
                self.primary_gate_attempted = True
                try:
                    if os.write(self.primary_gate_fd, b"G") != 1:
                        raise RuntimeError("cannot commit exact primary birth gate")
                    self.primary_gate_committed = True
                except BrokenPipeError:
                    ready, _, _ = select.select([self.primary_exec_error_fd], [], [], 6)
                    if not ready:
                        raise CleanupIncomplete("primary-gate-commit", pending=("exec-status",), resource=self)
                    self.primary_gate_committed = True
                except BaseException as error:
                    # A one-byte pipe write may have reached the child before
                    # cancellation. Keep exact gate and reconcile by retry.
                    raise CleanupIncomplete("primary-gate-commit", problems=(error,), resource=self) from error
            self.handles.release_one(self.primary_gate_fd)
            self.primary_gate_fd = None
        ready, _, _ = select.select([self.primary_exec_error_fd], [], [], 6)
        if not ready:
            raise CleanupIncomplete("primary-exec-ack", pending=("exec-status",), resource=self)
        error = os.read(self.primary_exec_error_fd, 4096)
        self.handles.release_one(self.primary_exec_error_fd)
        self.primary_exec_error_fd = None
        if error:
            code = struct.unpack("!i", error[:4])[0] if len(error) >= 4 else 0
            self.primary_exec_error = OSError(code, error[4:].decode("utf-8", "replace"))
            raise self.primary_exec_error
        self.primary_exec_complete = True
        return self.primary_pid

    def collect_primary(self):
        if self.primary_exec_status is None:
            info = getattr(self, "_primary_wait_result", None)
            if info is None:
                if getattr(self, "_primary_wait_attempted", False):
                    raise CleanupIncomplete("primary-wait-ambiguous", resource=self)
                if self.primary_fd is None:
                    return None
                self._primary_wait_attempted = True
                self._primary_wait_result = self._wait_exact(self.primary_fd)
                info = self._primary_wait_result
                if info is None:
                    self._primary_wait_attempted = False
                    return None
            self._record_primary_status(info)
        # Collection and delivery/release are separate obligations. A cache
        # hit repairs interrupted publication and resumes only unfinished work.
        self.primary_waited = True
        self._deliver_primary_status()
        return self.primary_exec_status

    def _record_primary_status(self, info):
        if info.si_code == os.CLD_EXITED:
            status = info.si_status
        elif info.si_code in (os.CLD_KILLED, os.CLD_DUMPED):
            status = -info.si_status
        else:
            raise CleanupIncomplete("primary-wait-status", resource=self)
        if type(status) is not int:
            raise CleanupIncomplete("primary-wait-status", resource=self)
        if self.primary_exec_status is not None and self.primary_exec_status != status:
            raise CleanupIncomplete("primary-wait-status-conflict", resource=self)
        self.primary_exec_status = status
        self.primary_waited = True
        self._deliver_primary_status()
        return status

    def _deliver_primary_status(self):
        if self.primary_status_fd is None:
            return
        state = getattr(self, "_primary_status_state", "pending")
        lease = getattr(self, "_primary_status_lease", None)
        if lease is not None and (lease.uncertain or lease.fd != self.primary_status_fd):
            # A released writer is usable only for finishing registry/field
            # retirement, never for writing by its former number.
            if state not in ("delivered", "reader-gone") or lease.fd is not None:
                raise CleanupIncomplete("primary-status-incarnation", resource=self)
        if state == "pending":
            payload = struct.pack("!i", self.primary_exec_status)
            # Four bytes fit PIPE_BUF on the supported Linux nonblocking pipe.
            # Known no-effect EAGAIN/EINTR can retry. Arbitrary cancellation or
            # a short/unknown write cannot be resent without an acknowledgement.
            self._primary_status_state = "uncertain"
            try:
                if os.write(self.primary_status_fd, payload) != len(payload):
                    raise CleanupIncomplete("primary-status-short-write", resource=self)
            except BrokenPipeError:
                self._primary_status_state = "reader-gone"
            except OSError as error:
                if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                    self._primary_status_state = "pending"
                raise
            else:
                self._primary_status_state = "delivered"
        if self._primary_status_state == "uncertain":
            raise CleanupIncomplete("primary-status-delivery-ambiguous", resource=self)
        # Registry removal and field publication may themselves be cancelled.
        # The retained original lease, rather than a numeric lookup, proves
        # terminal release if release_one already removed that registry entry.
        fd = self.primary_status_fd
        if lease is not None and lease.fd is None and fd not in self.handles.fds:
            self.primary_status_fd = None
            return
        self.handles.release_one(fd)
        self.primary_status_fd = None

    def primary(self, fd, sdk_pid):
        # Root may signal only a pinned nonroot caller's own DIRECT SDK child.
        # pidfd identity is read from kernel fdinfo, not supplied JSON/PIDs.
        pid = self.pidfd_pid(fd)
        status = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                      if ":" in line)
        uids = [int(n) for n in status.get("Uid", "").split()]
        if (len(uids) != 4 or any(uid != self.uid for uid in uids)
            or int(status.get("PPid", "-1")) != sdk_pid
            or self.pidfd_pid(fd) != pid or self.ops.ready(fd) or self.ops.ready(self.sdk_fd)):
            raise ValueError("primary is not the live authenticated SDK's own child")

    def packet_ready(self):
        return bool(select.select([self.control], [], [], 0)[0])

    def wait_event(self):
        watched = [self.sdk_fd, self.owner_fd, self.control]
        if self.stopping or getattr(self, "controller_fd", None) is not None and self.ops.ready(self.controller_fd):
            return "supervisor-stop"
        # A close already queued at this observation wins primary exit,
        # matching the existing intentional-close versus owner-exit rule.
        if self.packet_ready():
            return "packet"
        if self.primary_fd is not None and self.ops.ready(self.primary_fd):
            self.collect_primary()
            return "primary-exit"
        ready, _, _ = select.select(watched, [], [], .05)
        if self.sdk_fd in ready:
            return "sdk-loss"
        if self.control in ready:
            return "packet"  # A queued intentional close wins an owner-exit race.
        if self.owner_fd in ready:
            return "owner-loss"
        return None

    def _discharge_failed_open(self):
        """An open that raised an ordinary exception handed nothing over."""
        claim = getattr(self.delegation, "claim", None)
        try:
            retained = claim(self.root_recipient) if callable(claim) else None
        except Exception:
            return  # Keep the attempt open; retire reports the conflict.
        if retained is None:
            self._root_handoff_started = False

    def _root_gone(self):
        self.root = None
        self._root_released = True

    def retire(self):
        self.reconcile_births()
        self.restore_credentials()
        errors = []
        released = getattr(self, "_root_released", False)  # Never claim a released root.
        started = getattr(self, "_root_handoff_started", False) and not released
        claim = getattr(self.delegation, "claim", None)
        claimed = None
        if callable(claim) and not released:
            try:
                claimed = claim(self.root_recipient)
                if claimed is not None:
                    if self.root is not None and claimed is not self.root:
                        raise CleanupIncomplete("delegation-handoff-mismatch", resource=self)
                    self.root = claimed
                elif started and self.root is None:
                    # The delegation retains no token for this recipient: the
                    # open never completed a hand-off and its pending
                    # descriptors are closed by close_pending below.
                    self._root_handoff_started = started = False
            except BaseException as error:
                errors.append(error)
        if not errors and (started or claimed is not None):
            try:
                if self.root is None:
                    path = self._root_handoff_path
                    pid = self._root_handoff_pid
                    kwargs = {"recipient": self.root_recipient}
                    member_cgroup = getattr(self, "_root_handoff_member_cgroup", None)
                    if member_cgroup is not None:
                        kwargs["member_cgroup"] = member_cgroup
                    self.root = self.delegation.open(path, pid, **kwargs)
                acknowledge = getattr(self.delegation, "acknowledge", None)
                if callable(acknowledge):
                    acknowledge(self.root_recipient, self.root)
                self._root_handoff_started = False
            except BaseException as error:
                errors.append(error)
        status_pending = False
        if getattr(self, "primary_exec_status", None) is not None or getattr(self, "_primary_wait_attempted", False):
            try:
                self.collect_primary()
            except BaseException as error:
                status_pending = True
                errors.append(error)
        for key in ("boundary", "partial"):
            value = getattr(self, key)
            if value is not None:
                try:
                    value.retire()
                    setattr(self, key, None)
                except BaseException as error:
                    errors.append(error)
        if not errors and self.root is not None:
            try:
                release(self.root, self.delegation.close, self._root_gone)
            except BaseException as error:
                errors.append(error)
        try: self.delegation.close_pending()
        except BaseException as error: errors.append(error)
        # Registry and boundary proofs precede closure of SDK/owner/primary
        # capabilities. Connections stay open until the final receipt is sent.
        if status_pending:
            # Preserve the unresolved writer and primary incarnation. Release
            # independent descriptors, without a blind same-attempt bulkclose.
            for fd in list(self.handles.fds):
                if fd not in (self.primary_status_fd, self.primary_fd):
                    try: self.handles.release_one(fd)
                    except BaseException as error: errors.append(error)
        else:
            try: self.handles.close()
            except BaseException as error: errors.append(error)
        if errors:
            raise CleanupIncomplete("ancestor-resource-retirement", problems=errors, resource=self)

    def close_connections(self):
        errors = []
        for key in ("control", "listener"):
            value = getattr(self, key)
            if value is not None:
                try:
                    value.close()
                    setattr(self, key, None)
                except Exception as error:
                    errors.append(error)
        if errors:
            raise CleanupIncomplete("ancestor-connection-retirement", problems=errors, resource=self)


class SupervisorServer:
    """One instance; injectable native adapter, exact protocol and real drain."""
    def __init__(self, native):
        self.native = native
        self.cleanup = None
        self.sdk_pid = None
        self.receipt = None
        self.started = False
        self.trigger = "requested"

    def bootstrap(self, timeout=6):
        n = self.native
        n.setup()
        self.bootstrap_after_setup(timeout)

    def bootstrap_after_setup(self, timeout=6):
        n = self.native
        peer_pid, peer_uid = n.accept(n.ops.now() + timeout)
        value, fds = receive(n.control, 5, n.handles)
        validate_hello(value, n.login, n.token, peer_pid, peer_uid, n.uid)
        n.authenticate_sdk(fds[0], peer_pid)
        self.sdk_pid = peer_pid
        n.validate_channels(fds[1:])
        boundary = n.allocate()
        self.cleanup = AncestorCleanup(n.token, n.identity, boundary, n.ops)
        # The surviving ancestor/subreaper and BOTH boundaries exist before birth.
        n.spawn(value, fds)
        self.started = True
        send(n.control, {"op": "boundary", "instance": n.token, "supervisor": list(n.identity),
                         "boundary": list(boundary.identity)}, boundary.fds())

    def packet(self):
        n = self.native
        # Count is determined from the operation only AFTER all delivered
        # descriptors have been registered; receive accepts at most one here.
        value, fds = receive(n.control, (0, 1, 4), n.handles)
        if value == {"version": VERSION, "op": "close", "instance": n.token} and not fds:
            return "close"
        if value.get("op") == "primary-launch":
            fd = n.spawn_primary(value, fds)
            if self.cleanup.primary is None:
                self.cleanup.bind(fd)
            send(n.control, {"op": "primary", "instance": n.token,
                             "request": value["request"], "pid": n.primary_pid}, (fd,))
            return None
        if (value.get("op") == "primary-commit" and not fds
            and set(value) == {"version", "op", "instance", "request"}
            and value["version"] == VERSION and value["instance"] == n.token):
            n.commit_primary(value["request"])
            send(n.control, {"op": "primary-committed", "instance": n.token,
                             "request": value["request"], "pid": n.primary_pid})
            return None
        raise ValueError("primary must be born behind the independent ancestor gate")

    def finish(self):
        n = self.native
        if hasattr(n, "reconcile_births"):
            n.reconcile_births()
        if n.primary_pid is not None and n.primary_fd is None:
            n.recover_primary_pin()
        if self.cleanup is not None and self.cleanup.primary is None and n.primary_fd is not None:
            self.cleanup.bind(n.primary_fd)
        if self.cleanup is not None and self.receipt is None:
            self.receipt = dict(self.cleanup.attempt(), trigger=self.trigger)
        elif self.cleanup is None:
            # No child birth is possible before cleanup creation. Still prove
            # no adopted child remains on any partial bootstrap path.
            if not n.ops.empty_children() or n.ops.children():
                raise CleanupIncomplete("ancestor-bootstrap-children", resource=self)
        n.retire()
        if self.receipt is None and n.identity is not None:
            self.receipt = {"version": VERSION, "op": "unlaunched", "instance": n.token,
                            "supervisor": list(n.identity), "no_descendant_birth": True,
                            "adoption_drained": True}
        return self.receipt

    def run(self):
        n = self.native
        failure = None
        try:
            self.bootstrap()
            while True:
                event = n.wait_event()
                if event in ("close", "sdk-loss", "owner-loss", "supervisor-stop", "primary-exit"):
                    self.trigger = "requested" if event == "close" else event
                    break
                if event == "packet" and self.packet() == "close":
                    break
        except BaseException as error:
            failure = error
            self.trigger = "protocol-fault" if self.started else "bootstrap-fault"
        # Cancellation/SDK EOF/owner death cannot relinquish a live family.
        # Expired attempts retain this ancestor and its real child registry.
        notified = sent_receipt = False
        while True:
            try:
                value = self.finish()
                if value is not None and n.control is not None and not sent_receipt:
                    if getattr(n, "wait_identity", None) is not None:
                        value = {"op": "wait-certificate", "instance": n.token,
                                 "wait_owner": list(n.wait_identity), "certificate": value}
                    try: send(n.control, value)
                    except (BrokenPipeError, ConnectionResetError, OSError): pass
                    sent_receipt = True
                n.close_connections()
                return 0 if self.started else 1
            except BaseException as error:
                failure = failure or error
                if not notified and n.control is not None:
                    notified = True
                    try: send(n.control, {"op": "pending", "instance": n.token})
                    except OSError: pass
                # This independent owner is retained until cleanup/retirement
                # succeeds. It creates no more children after closing starts.
                n.ops.sleep(.05)



class OwnedSocket:
    """Detach before closing a retained incarnation, including late cancellation."""
    def __init__(self, sock, lease=None):
        self.socket = sock
        self.lease = lease if lease is not None else CheckedDescriptor(sock.fileno(), os)

    def capture(self):
        self.lease.capture()
        return self

    def __getattr__(self, name):
        return getattr(self.socket, name)

    def recvmsg(self, *args):
        try:
            return self.socket.recvmsg(*args)
        except (TimeoutError, BlockingIOError, InterruptedError) as error:
            if type(self.socket) is socket.socket:
                error.receive_not_consumed = self
            raise

    def close(self):
        if self.socket.fileno() != -1:
            self.socket.detach()
        self.lease.close()


class WaitOwnerBootstrap:
    """Executed child transaction. No workload birth before its final commit.

    The child, not its controller, owns the manager connection and every family
    fork/wait. A lost/ambiguous manager reply can only prevent births; it never
    transfers the family to PID 1 or relinquishes an existing cleanup registry.
    """
    def __init__(self, native):
        self.native = native
        self.attempted = self.committed = False
        self.identity = None

    def establish(self, timeout=6):
        n = self.native
        deadline = n.ops.now() + timeout
        n.establish_parent()  # Retained pre-fork controller pidfd, not numeric reopen.
        n.establish_subreaper()  # The subreaper flag is NOT inherited across fork.
        n.manager.connect(deadline)
        if n.manager.scope(wait_name(n.instance), deadline) is not None:
            raise RuntimeError("fresh wait-owner scope already exists")
        self.attempted = True  # Even an ambiguous create reply retains ownership.
        n.manager.place_self(n.instance, deadline)
        while True:
            record = n.manager.scope(wait_name(n.instance), deadline)
            if record and record.get("ActiveState") == "active":
                identity = wait_identity(record, n.instance, n.self_pid(), n.identity, self.identity)
                n.verify_placement(identity)
                self.identity = identity
                n.wait_identity = identity
                self.committed = True
                return identity
            if record and record.get("ActiveState") in ("inactive", "failed"):
                raise RuntimeError("wait-owner scope failed before birth")
            if n.ops.now() >= deadline:
                raise TimeoutError("wait-owner ownership transaction expired")
            n.ops.sleep(.01)

    def birth_authority(self):
        if not self.committed or self.identity is None:
            raise CleanupIncomplete("wait-owner-before-birth", resource=self)
        if self.native.ops.ready(self.native.controller_fd):
            raise CleanupIncomplete("controller-lost-before-birth", resource=self)


class NativeWaitOwner(NativeAncestor):
    """Fixed fork child, physically outside the supervisor service kill domain."""
    def establish_parent(self):
        if (os.getuid() != 0 or os.geteuid() != 0 or self.ops.ready(self.controller_fd)
            or self.pidfd_pid(self.controller_fd) != self.identity[1] or os.getppid() != self.identity[1]):
            raise RuntimeError("wait owner lacks its pinned fixed parent")

    def establish_subreaper(self):
        prctl(36, 1)
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sig, self._stop)

    def self_pid(self):
        return os.getpid()

    def verify_placement(self, identity):
        paths = [line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::")]
        if paths != [identity[2]] or os.getuid() != 0 or os.geteuid() != 0:
            raise RuntimeError("wait owner is outside its authenticated root scope")
        # A manager reexec/transport loss cannot change this retained kernel
        # membership, parent authority, instance or family capability registry.

    def setup(self):
        self.wait_bootstrap.establish()
        send(self.handoff, {"op": "wait-ready", "instance": self.token,
                            "supervisor": list(self.identity), "wait_owner": list(self.wait_identity)})

    def accept(self, deadline):
        self.handoff.settimeout(max(.001, deadline - self.ops.now()))
        value, fds = receive(self.handoff, 1, self.handles)
        if (set(value) != {"version", "op", "instance", "sdk", "uid"}
            or value["op"] != "sdk-channel" or value["instance"] != self.token):
            raise ValueError("invalid fixed-parent SDK channel transfer")
        # Register the FD before constructing a socket. Preserve the original
        # incarnation lease through cancellation of either assignment/transfer.
        self.control_fd = fds[0]
        if self.control is None:
            self.control = self.handles.attach_socket(self.control_fd, socket.socket, OwnedSocket)
            self.handles.transfer_object(self.control)
        self.handles.transfer(self.control_fd)
        pid, uid, _ = struct.unpack("3i", self.control.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if (pid != value["sdk"] or uid != value["uid"] or uid != self.uid or pid <= 1
            or self.control.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_SEQPACKET):
            raise ValueError("transferred SDK socket has foreign kernel credentials")
        self.control.settimeout(max(.001, deadline - self.ops.now()))
        self.self_fd = self.handles.allocate(os.pidfd_open, os.getpid())
        send(self.control, {"op": "wait-owner", "instance": self.token,
                           "supervisor": list(self.identity), "wait_owner": list(self.wait_identity)}, (self.self_fd,))
        return pid, uid

    def allocate(self):
        # Nonphysical workloads use the wait scope, never the service kill
        # domain. Physical workloads retain the real session ancestor.
        saved = self.identity
        try:
            self.identity = self.wait_identity
            return super().allocate()
        finally:
            self.identity = saved

    def spawn(self, hello, fds):
        self.wait_bootstrap.birth_authority()
        return super().spawn(hello, fds)

    def spawn_primary(self, value, fds):
        self.wait_bootstrap.birth_authority()
        return super().spawn_primary(value, fds)

    def commit_primary(self, request):
        self.wait_bootstrap.birth_authority()
        return super().commit_primary(request)

    def retire(self):
        # A partial recipient owns its immutable lease before registry removal.
        if self.control is not None and getattr(self, "control_fd", None) in self.handles.fds:
            self.handles.transfer(self.control_fd)
        super().retire()
        # Reconcile an ambiguous pre-birth manager reply using this process's
        # current kernel membership; never collect a same-name foreign scope.
        if self.wait_identity is None and self.wait_bootstrap.attempted:
            deadline = self.ops.now() + 1.5
            try:
                record = self.manager.scope(wait_name(self.instance), deadline)
            except Exception:
                self.manager.reconnect(deadline)
                record = self.manager.scope(wait_name(self.instance), deadline)
            paths = [line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::")]
            if record and paths == [record.get("ControlGroup")]:
                self.wait_identity = wait_identity(record, self.instance, os.getpid(), self.identity)
            elif paths != [self.identity[2]]:
                raise CleanupIncomplete("ambiguous-wait-scope-placement", resource=self)
        if not getattr(self, "retired_ledger_sent", False):
            try:
                send(self.handoff, {"op": "wait-retired", "instance": self.token,
                                   "supervisor": list(self.identity),
                                   "wait_owner": list(self.wait_identity) if self.wait_identity else None})
            except (BrokenPipeError, ConnectionResetError):
                pass  # Controller disappeared; family proof belongs to this survivor.
            self.retired_ledger_sent = True
        self.manager.close()

    def wait_event(self):
        if select.select([self.handoff], [], [], 0)[0]:
            # This private fixed-parent channel has no further messages after
            # handoff. EOF means orderly controller stop or abrupt disappearance.
            data = self.handoff.recv(1)
            if data:
                raise ValueError("unexpected fixed-controller lifetime packet")
            self.stopping = True
        return super().wait_event()

    def close_connections(self):
        # Reconcile a partially published socket against Handles before any
        # close; a cancellation never closes a replacement numerical FD.
        if self.control is not None and getattr(self, "control_fd", None) in self.handles.fds:
            self.handles.transfer(self.control_fd)
        super().close_connections()
        if self.handoff is not None:
            self.handoff.close()
            self.handoff = None


class RootController:
    """Finite delivery controller. It never forks or waits caller workloads."""
    def __init__(self, native):
        self.native = native
        self.owner_identity = None
        self.started = self.handed = False

    def bootstrap(self, timeout=6):
        n = self.native
        n.setup()
        self.started = True  # Includes cancellation/ambiguous fixed child birth.
        n.fork_wait_owner()
        value = n.wait_ready(n.ops.now() + timeout)
        if (set(value) != {"version", "op", "instance", "supervisor", "wait_owner"}
            or value["op"] != "wait-ready" or value["instance"] != n.token
            or value["supervisor"] != list(n.identity)):
            raise ValueError("unbound fixed wait-owner ready record")
        n.verify_wait_owner(tuple(value["wait_owner"]), n.ops.now() + timeout)
        self.owner_identity = tuple(value["wait_owner"])
        peer = n.accept(n.ops.now() + timeout)
        # Sending does not relinquish the child's parent/wait/manager authority.
        # A cancelled transfer retains the controller socket until exact child
        # cleanup. The child owns every SCM delivery before parsing its payload.
        n.handoff_sdk(peer)
        self.handed = True

    def finish(self):
        n = self.native
        n.end_delivery()
        n.collect_wait_owner()  # Exact pre-fork child identity; never arbitrary PID.
        n.collect_wait_scope(self.owner_identity)
        n.retire_controller()

    def run(self):
        try:
            self.bootstrap()
            self.native.collect_wait_owner()
        except BaseException:
            pass
        while True:
            try:
                self.finish()
                return 0 if self.handed else 1
            except BaseException:
                self.native.ops.sleep(.05)


class NativeController(NativeAncestor):
    """The sole system-service main; fixed per-instance internal factory."""
    def __init__(self, instance):
        super().__init__(instance)
        self.handoff = self.child_channel = None
        self.child_pid = self.child_fd = self.controller_fd = None
        self.child_waited = False
        self.socket_birth = None
        self.private_packet = None
        self.delivery_ended = False
        self.manager = None

    def setup(self):
        super().setup()
        self.listener = self.handles.promote_socket(self.listener, OwnedSocket)
        self.handles.transfer_object(self.listener)
        self.listener.capture()

    def accept(self, deadline):
        peer = super().accept(deadline)
        self.control = self.handles.promote_socket(self.control, OwnedSocket)
        self.handles.transfer_object(self.control)
        self.control.capture()
        return peer

    def fork_wait_owner(self):
        # Acquire the controller's exact lease BEFORE fork, eliminating a
        # parent-death/recycled-number window in the child authentication.
        self.controller_fd = self.handles.allocate(os.pidfd_open, os.getpid())
        blocked = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
        try:
            self.socket_birth = self.handles.allocate_objects(socket.socketpair, socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC, many=True)
            self.handoff = self.handles.promote_socket(self.socket_birth[0], OwnedSocket)
            self.handles.transfer_object(self.handoff)
            self.child_channel = self.handles.promote_socket(self.socket_birth[1], OwnedSocket)
            self.handles.transfer_object(self.child_channel)
            self.handoff.capture()
            self.child_channel.capture()
            self.socket_birth = None
            pid = self.fork_child("child_pid")
            if pid == 0:
                signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
                # No caller-supplied object is involved; the private channels
                # and controller identity came from this immutable root fork.
                self.handoff.close()
                self.listener.close()
                n = NativeWaitOwner(self.instance)
                n.identity = self.identity
                n.controller_fd = n.handles.add(self.controller_fd)
                n.handoff = self.child_channel
                n.manager = WaitScopeManager()
                n.wait_identity = None
                n.wait_bootstrap = WaitOwnerBootstrap(n)
                status = SupervisorServer(n).run()
                os._exit(status)
            self.child_pid = pid  # Before unblocking asynchronous cancellation.
            self.child_fd = self.handles.allocate(os.pidfd_open, pid)
            if self.ops.pidfd_pid(self.child_fd) != pid:
                raise RuntimeError("fixed wait-owner birth pin mismatch")
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        self.child_channel.close()
        self.child_channel = None
        self.manager = WaitScopeManager()
        self.manager.connect(self.ops.now() + 6)

    def wait_ready(self, deadline):
        self.handoff.settimeout(max(.001, deadline - self.ops.now()))
        value, _ = receive(self.handoff, 0, self.handles)
        self.private_packet = value
        return value

    def verify_wait_owner(self, identity, deadline):
        if len(identity) != 3 or self.ops.pidfd_pid(self.child_fd) != identity[1] or self.ops.ready(self.child_fd):
            raise RuntimeError("wait-owner ready identity is not the exact fixed child")
        wait_identity(self.manager.scope(wait_name(self.instance), deadline), self.instance,
                      self.child_pid, self.identity, identity)
        status = dict(line.split(":", 1) for line in Path(f"/proc/{self.child_pid}/status").read_text().splitlines() if ":" in line)
        group = [line[3:] for line in Path(f"/proc/{self.child_pid}/cgroup").read_text().splitlines() if line.startswith("0::")]
        if status.get("Uid", "").split() != ["0"] * 4 or int(status.get("PPid", "-1")) != os.getpid() or group != [identity[2]]:
            raise RuntimeError("wait owner lacks exact parent/root/domain authority")

    def handoff_sdk(self, peer):
        send(self.handoff, {"op": "sdk-channel", "instance": self.token,
                            "sdk": peer[0], "uid": peer[1]}, (self.control.fileno(),))
        self.control.close()
        self.control = None
        self.listener.close()
        self.listener = None

    def end_delivery(self):
        # EOF cancels a partially transferred SDK handoff while retaining the
        # private read side for the child's exact allocation retirement ledger.
        if self.handoff is not None and not self.delivery_ended:
            self.handoff.shutdown(socket.SHUT_WR)
            self.delivery_ended = True
        self.close_connections()

    def close_delivery(self):
        if not hasattr(self, "handles"):
            self.handles = Handles(getattr(os, "close", None), os)
        if self.socket_birth is not None:
            # Promote every socket from the original returned tuple before
            # attempting any independent release; do not recreate detached FDs.
            if self.handoff is None and self.socket_birth[0].fileno() != -1:
                self.handoff = self.handles.promote_socket(self.socket_birth[0], OwnedSocket)
                self.handles.transfer_object(self.handoff)
                self.handoff.capture()
            if self.child_channel is None and self.socket_birth[1].fileno() != -1:
                self.child_channel = self.handles.promote_socket(self.socket_birth[1], OwnedSocket)
                self.handles.transfer_object(self.child_channel)
                self.child_channel.capture()
            self.socket_birth = None
        errors = []
        for key in ("handoff", "child_channel", "control", "listener"):
            value = getattr(self, key)
            if value is not None:
                try:
                    value.close()
                    setattr(self, key, None)
                except BaseException as error:
                    if isinstance(value, OwnedSocket) and value.lease.fd is None:
                        setattr(self, key, None)
                    errors.append(error)
        if errors:
            raise CleanupIncomplete("controller-delivery-retirement", problems=errors, resource=self)

    def collect_wait_owner(self):
        self.reconcile_births()
        if self.child_waited or self.child_pid is None:
            return
        if self.child_fd is None:
            # Sole unreaped direct child; no competing or implicit waiter.
            self.child_fd = self.handles.allocate(os.pidfd_open, self.child_pid)
            if self.ops.pidfd_pid(self.child_fd) != self.child_pid:
                raise CleanupIncomplete("wait-owner-recovery-pin", resource=self)
        while self._wait_exact(self.child_fd) is None:
            if self.stopping:
                self.end_delivery()
            self.ops.sleep(.01)
        self.child_waited = True

    def collect_wait_scope(self, identity):
        if identity is None and self.child_pid is not None and self.handoff is not None:
            value = self.private_packet
            if value is None or value.get("op") != "wait-retired":
                value, _ = receive(self.handoff, 0, self.handles)
                self.private_packet = value
            if (set(value) != {"version", "op", "instance", "supervisor", "wait_owner"}
                or value["op"] != "wait-retired" or value["instance"] != self.token
                or value["supervisor"] != list(self.identity)):
                raise CleanupIncomplete("wait-owner-retirement-ledger", resource=self)
            if value["wait_owner"] is None:
                return  # Fixed child proved no self placement; no foreign-unit GC.
            identity = tuple(value["wait_owner"])
            if len(identity) != 3 or identity[1] != self.child_pid:
                raise CleanupIncomplete("wait-owner-retirement-identity", resource=self)
        if self.manager is not None:
            self.manager.collect_scope(self.instance, identity, self.ops, self.ops.now() + 1.5)

    def retire_controller(self):
        if not self.ops.empty_children() or self.ops.children():
            raise CleanupIncomplete("controller-adopted-family", resource=self)
        self.close_delivery()
        self.handles.close()
        if self.manager is not None:
            self.manager.close()

def main():
    if len(sys.argv) != 2:
        raise SystemExit("expected one installed service instance")
    return RootController(NativeController(sys.argv[1])).run()


if __name__ == "__main__":
    raise SystemExit(main())
