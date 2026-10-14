"""Private-bus completion, with injectable process, clock and transport adapters.

Linux integration is deliberately fail-closed: an independently held cgroup-v2
boundary accounts for descendants which have not yet reached the subreaper.
No process names, process groups or reusable numeric PIDs authorize a signal.
"""
import ctypes
import errno
import json
import os
from pathlib import Path
import select
import signal
import stat
import sys
import time


class CleanupIncomplete(RuntimeError):
    def __init__(self, phase, *, pending=(), problems=(), resource=None):
        self.phase = phase
        self.pending = tuple(pending)
        self.problems = tuple(str(p) for p in problems)
        self.resource = resource
        super().__init__(f"private bus cleanup incomplete ({phase}); "
                         f"pending={self.pending}; problems={self.problems}")


class DescriptorReleased(OSError):
    """Linux close reported an error after invalidating descriptor authority."""


class DescriptorCloseCancelled(KeyboardInterrupt):
    """Typed adapter guarantees cancellation before any close effect.

    Raw close never emits this proof. An adapter may use it only while it
    still owns the original incarnation and has not attempted its release.
    """


class CheckedDescriptor:
    """Close cancellation retains an object identity, never a reusable number.

    An ambiguous attempt never regains authority from fstat equality: another
    descriptor for the SAME object has the same identity too. EBADF or a
    different object can disprove continued ownership, without closing it.
    Only a typed pre-effect refusal permits another close attempt.
    """
    def __init__(self, fd, filesystem=os, *, raw=True):
        self.fd, self.filesystem = fd, filesystem
        self.identity, self.uncertain = None, False
        self.raw = raw

    def capture(self):
        st = self.filesystem.fstat(self.fd)
        self.identity = (st.st_dev, st.st_ino, getattr(st, "st_mode", 0))

    def close(self):
        if self.fd is None:
            return
        if self.uncertain:
            if self.identity is None:
                raise CleanupIncomplete("cancelled-descriptor-identity", resource=self)
            try:
                st = self.filesystem.fstat(self.fd)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
                self.fd, self.uncertain = None, False
                return
            if (st.st_dev, st.st_ino, getattr(st, "st_mode", 0)) != self.identity:
                self.fd, self.uncertain = None, False
                return
            phase = ("cancelled-anonymous-descriptor" if stat.S_IFMT(getattr(st, "st_mode", 0)) == 0
                     else "cancelled-descriptor-incarnation")
            raise CleanupIncomplete(phase, resource=self)
        # Publish uncertainty BEFORE the effect. This also covers cancellation
        # after close returns, before success/late-error invalidation publishes.
        self.uncertain = True
        try:
            self.filesystem.close(self.fd)
        except OSError as error:
            if not self.raw and not isinstance(error, DescriptorReleased):
                self.uncertain = False
                raise  # Typed adapter refusal happened before release.
            self.fd, self.uncertain = None, False  # Linux invalidates before all late errors.
            raise DescriptorReleased(str(error)) from error
        except BaseException as error:
            if not self.raw and isinstance(error, DescriptorCloseCancelled):
                self.uncertain = False  # Explicit typed no-effect proof only.
            raise
        else:
            self.fd, self.uncertain = None, False


def close_descriptor(fd):
    try:
        os.close(fd)
    except OSError as error:
        # Linux releases a valid descriptor before late close errors, including
        # EINTR; EBADF already denotes an invalid descriptor. Never retry its
        # number or retain it as signal authority. Still expose the diagnostic.
        raise DescriptorReleased(str(error)) from error


class DescriptorIncarnation(int):
    """An allocator's ownership token, while retaining the native integer ABI."""
    def __new__(cls, fd):
        result = super().__new__(cls, fd)
        result.ownership = object()
        return result

    def __eq__(self, other):
        if isinstance(other, DescriptorIncarnation):
            return self.ownership is other.ownership
        return int.__eq__(self, other)

    def __ne__(self, other):
        result = self.__eq__(other)
        return NotImplemented if result is NotImplemented else not result

    __hash__ = int.__hash__


def descriptor_incarnation(fd):
    return (DescriptorIncarnation(fd) if isinstance(fd, int) and
            not isinstance(fd, DescriptorIncarnation) else fd)


def descriptor_authority(fd):
    """A consumed/ambiguous saved lease cannot authorize a later native effect."""
    lease = getattr(fd, "acquisition_lease", None)
    if lease is not None and (lease.fd is None or lease.uncertain):
        raise CleanupIncomplete("saved-descriptor-authority", resource=lease)


class SharedDescriptorCloser:
    """Recipients use the producer's actual one-close leases, including overlap."""
    def __init__(self, leases):
        self.leases = {getattr(fd, "ownership", fd): lease for fd, lease in leases.items()}

    def __call__(self, fd):
        lease = self.leases.get(getattr(fd, "ownership", fd))
        if lease is None:
            raise CleanupIncomplete("shared-descriptor-lease-missing", resource=self)
        lease.close()

    def check_signal(self, fd):
        lease = self.leases.get(getattr(fd, "ownership", fd))
        if lease is None or lease.fd is None or lease.uncertain:
            raise CleanupIncomplete("shared-descriptor-signal-authority", resource=self)


class RetainedCloser:
    """Per-owner cancelled close leases; never a global numeric-FD registry."""
    def __init__(self, closer, filesystem):
        self.closer, self.filesystem, self.leases = closer, filesystem, {}

    def acquired(self, fd):
        """Record a successful allocation BEFORE publishing its ownership.

        Only an allocator/SCM ownership transfer may call this, never a retry
        or an fstat comparison. A terminal old lease can survive cancelled
        owner removal; the new allocation is a separate incarnation. Unknown
        close outcomes remain unresolved even when an allocator reuses a number.
        """
        if isinstance(fd, DescriptorIncarnation):
            return fd  # Retry publication of the SAME saved allocation.
        lease = self.leases.get(fd)
        if lease is not None:
            if lease.fd is not None:
                raise CleanupIncomplete("descriptor-acquisition-overlaps-lease", resource=self)
        # Keep the old terminal entry until its own owner removes it. A new
        # allocation must not overwrite the lease of a still-indexed old owner.
        return DescriptorIncarnation(fd) if isinstance(fd, int) else fd

    @staticmethod
    def key(fd):
        return fd.ownership if isinstance(fd, DescriptorIncarnation) else fd

    def __call__(self, fd):
        key = self.key(fd)
        lease = self.leases.get(key)
        shared = getattr(fd, "acquisition_lease", None)
        if shared is not None:
            if lease is not None and lease is not shared:
                raise CleanupIncomplete("descriptor-shared-lease-conflict", resource=self)
            lease = self.leases[key] = shared
        if lease is None:
            adapter = type("CloseAdapter", (), {})()
            adapter.fstat, adapter.close = self.filesystem.fstat, self.closer
            lease = self.leases[key] = CheckedDescriptor(fd, adapter, raw=False)
            lease.capture()
        lease.close()

    def forget_released(self, fd):
        # Called only AFTER the owning field/list has been invalidated.
        # A cancellation between close and that callback retains a tombstone.
        key = self.key(fd)
        lease = self.leases.get(key)
        if lease is not None and lease.fd is None:
            self.leases.pop(key)

    def check_signal(self, fd):
        lease = getattr(fd, "acquisition_lease", None) or self.leases.get(self.key(fd))
        if lease is not None and (lease.uncertain or lease.fd is None):
            raise CleanupIncomplete("cancelled-pidfd-signal-authority", resource=self)


def release(fd, closer, released):
    shared = getattr(fd, "acquisition_lease", None)
    if shared is not None:
        try:
            shared.close()
        finally:
            if shared.fd is None:
                released()
        return
    try:
        closer(fd)
    except DescriptorReleased:
        released()
        forget = getattr(closer, "forget_released", None) or getattr(
            getattr(closer, "__self__", None), "forget_released", None)
        if forget is not None:
            forget(fd)
        raise
    else:
        released()
        forget = getattr(closer, "forget_released", None) or getattr(
            getattr(closer, "__self__", None), "forget_released", None)
        if forget is not None:
            forget(fd)


def parse_events(data):
    """Unknown, malformed or truncated events never prove an empty boundary."""
    if not isinstance(data, bytes) or not data.endswith(b"\n") or len(data) > 4096:
        raise ValueError("invalid cgroup events")
    entries = {}
    for line in data.splitlines():
        key, value = line.split()
        if key in entries:
            raise ValueError("duplicate cgroup event")
        entries[key] = value
    if entries.get(b"populated") not in (b"0", b"1"):
        raise ValueError("missing cgroup population evidence")
    return entries[b"populated"] == b"0"


class LinuxProcesses:
    now = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)

    def __init__(self):
        self.close = RetainedCloser(close_descriptor, os)
        def checked_signal(fd, sig):
            self.close.check_signal(fd)
            LinuxProcesses.signal(fd, sig)
        self.signal = checked_signal

    @staticmethod
    def ready(fd, timeout=0):
        descriptor_authority(fd)
        return fd is None or bool(select.select([fd], [], [], max(0, timeout))[0])

    @staticmethod
    def signal(fd, sig):
        descriptor_authority(fd)
        if fd is not None:
            try:
                signal.pidfd_send_signal(fd, sig)
            except ProcessLookupError:
                pass

    @staticmethod
    def children():
        return {int(pid) for task in Path("/proc/self/task").iterdir()
                for pid in (task / "children").read_text().split()}

    @staticmethod
    def acquire(pid):
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return None
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[1]) == os.getpid():
                return fd
        except FileNotFoundError:
            pass
        except BaseException:
            os.close(fd)
            raise
        os.close(fd)
        return None

    @staticmethod
    def reap(pid):
        try:
            return os.waitpid(pid, os.WNOHANG)[0] == pid
        except ChildProcessError:
            return True  # Already waited; the retained pidfd proved exit first.

    @staticmethod
    def empty_children():
        # WNOWAIT cannot consume a child behind the pidfd registry. None means
        # living children remain; an exited result needs acquisition/reaping.
        # Only ECHILD proves there are no remaining children to wait for.
        try:
            os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return True
        return False

    close = staticmethod(close_descriptor)

    @staticmethod
    def status(process):
        return process.poll()

    @staticmethod
    def pidfd_pid(fd):
        descriptor_authority(fd)
        values = [line.split(":", 1)[1].strip() for line in Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
                  if line.startswith("Pid:")]
        if len(values) != 1:
            raise ValueError("missing/duplicate exact pidfd task identity")
        return int(values[0])

    @staticmethod
    def reaped(fd):
        descriptor_authority(fd)
        if fd is None:
            return True
        # Linux 5.14 lacks the newer pidfd POLLHUP flag. Kernel pidfd fdinfo
        # reports Pid=-1 only after that exact task has been detached. Never
        # confuse a zombie's readable pidfd with this separate reaping proof.
        values = [line.split(":", 1)[1].strip() for line in Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
                  if line.startswith("Pid:")]
        if len(values) != 1:
            raise ValueError("missing/duplicate exact pidfd task identity")
        return LinuxProcesses.ready(fd) and values == ["-1"]

    @staticmethod
    def wait_exact(fd):
        descriptor_authority(fd)
        return os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG)


def stop_exact(fd, ops, deadline, grace=.2):
    if ops.ready(fd):
        return
    ops.signal(fd, signal.SIGTERM)
    if ops.ready(fd, min(grace, max(0, deadline - ops.now()))):
        return
    ops.signal(fd, signal.SIGKILL)
    if not ops.ready(fd, max(0, deadline - ops.now())):
        raise CleanupIncomplete("primary-wait", pending=("primary",))


class DescendantDrain:
    """Retain adoption accounting across bounded attempts, including failures.

    All levels share one TERM grace. cgroup.kill covers still-parented levels
    and concurrent forks; pidfds are retained until exit AND wait are proved.
    The owner stays alive on incomplete attempts, retaining this registry.
    """
    def __init__(self, boundary, ops):
        self.boundary, self.ops = boundary, ops
        self.owned = {}
        self.waited = set()
        self._acquisition = None

    def _publish_acquisition(self):
        pending = self._acquisition
        if pending is None:
            return
        if len(pending) != 2:
            # An allocator call was entered but its returned handle was lost.
            # No new numeric acquisition or empty-tree proof can resolve it.
            raise CleanupIncomplete("descendant-acquisition-outcome", resource=self)
        pid, fd = pending
        if fd is not None:
            acquired = getattr(self.ops.close, "acquired", None)
            if acquired is not None:
                self._acquisition = (pid, acquired(fd))
                fd = self._acquisition[1]
            self.owned[pid] = fd
            self.waited.discard(pid)
        self._acquisition = None
        return fd

    def run(self, *, deadline, grace=.2):
        ops = self.ops
        kill_at = min(deadline, ops.now() + grace)
        killed = False
        phase = "adoption"
        def check():
            if ops.now() >= deadline:
                raise CleanupIncomplete(phase, pending=self.owned,
                                        problems=("budget expired",), resource=self)
        try:
            self._publish_acquisition()
            while True:
                phase = "adoption"
                check()
                children = ops.children()
                check()
                for pid in children - self.owned.keys():
                    check()
                    self._acquisition = (pid,)
                    self._acquisition = (pid, ops.acquire(pid))
                    fd = self._publish_acquisition()
                    if fd is not None:
                        check()
                        ops.signal(fd, signal.SIGKILL if killed else signal.SIGTERM)
                        check()
                phase = "reap"
                for pid, fd in list(self.owned.items()):
                    check()
                    if pid in self.waited or ops.ready(fd) and ops.reap(pid):
                        self.waited.add(pid)  # Never repeat a numeric wait after cancellation/reaping.
                        release(fd, ops.close, lambda: self.owned.pop(pid))
                        self.waited.discard(pid)
                    check()
                phase = "boundary-empty"
                if not self.owned and not children and self.boundary.empty() and ops.empty_children():
                    # A final adoption read is required after the kernel proof.
                    phase = "final-adoption"
                    if not ops.children() and ops.empty_children() and ops.now() < deadline:
                        return
                check()
                if not killed and ops.now() >= kill_at:
                    phase = "boundary-kill"
                    self.boundary.kill()
                    for fd in self.owned.values():
                        ops.signal(fd, signal.SIGKILL)
                    killed = True
                    check()
                ops.sleep(min(.01, max(0, deadline - ops.now())))
        except Exception as error:
            # Deadline/adapter failures never relinquish either boundary or fds.
            # Issue the scoped kill even if acquisition consumed the budget.
            problems = [error]
            try:
                self.boundary.kill()
                for fd in self.owned.values():
                    ops.signal(fd, signal.SIGKILL)
            except Exception as kill_error:
                problems.append(kill_error)
            raise CleanupIncomplete(phase, pending=self.owned, problems=problems,
                                    resource=self) from error


def completion(instance, boundary, bound, *, complete, problems=(), pending=()):
    return {"version": 1, "instance": instance, "boundary": list(boundary.identity),
            "state": "complete" if complete else "incomplete", "bound": bound,
            "primary_exited": complete, "descendants_empty": complete,
            "adoption_drained": complete, "pending": list(pending),
            "problems": [str(p)[:256] for p in problems]}


def validate_completion(data, instance, identity, bound):
    if not isinstance(data, bytes) or not data or len(data) > 8192:
        raise ValueError("missing/oversized cleanup acknowledgement")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate acknowledgement key")
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=unique)
    required = {"version", "instance", "boundary", "state", "bound", "primary_exited",
                "descendants_empty", "adoption_drained", "pending", "problems"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("partial/unknown cleanup acknowledgement")
    if (type(value["version"]) is not int or value["version"] != 1 or
            value["instance"] != instance or value["boundary"] != list(identity) or
            any(type(n) is not int for n in value["boundary"]) or
            type(value["bound"]) is not bool or value["bound"] != bound or
            value["state"] != "complete" or value["pending"] != [] or
            value["problems"] != [] or any(value[k] is not True for k in
            ("primary_exited", "descendants_empty", "adoption_drained"))):
        raise ValueError("unbound or incomplete cleanup acknowledgement")
    return value


class OwnerCleanup:
    def __init__(self, instance, boundary, ops, *, drain_boundary=None):
        self.instance, self.boundary, self.ops = instance, boundary, ops
        self.primary = None
        self.drain = DescendantDrain(drain_boundary or boundary, ops)

    def attempt(self, timeout=1.5):
        deadline = self.ops.now() + timeout
        errors = []
        try:
            stop_exact(self.primary, self.ops, deadline)
        except Exception as error:
            errors.append(error)
        try:
            self.drain.run(deadline=deadline)
        except Exception as error:
            errors.append(error)
        if errors:
            raise CleanupIncomplete("owner", pending=self.drain.owned,
                                    problems=errors, resource=self)
        return completion(self.instance, self.boundary, self.primary is not None,
                          complete=True)


class SDKCleanup:
    """Owner exit is evidence of exit only; this object retains SDK authority.

    A fixed SDK watcher calls observe_exit on hard owner loss. Recovery uses the
    same pinned kernel boundary and primary pidfd. The first close reports the
    fault even when recovery succeeded; a retry can return a recovery receipt.
    """
    def __init__(self, instance, boundary, ops, process, owner_fd, transport):
        self.instance, self.boundary, self.ops = instance, boundary, ops
        self.process, self.owner_fd, self.transport = process, owner_fd, transport
        self.primary = None
        self.receipt = self.fault = None
        self.fault_reported = False
        self.recovered = False

    def _proof(self):
        return (self._owner_ready() and self.ops.ready(self.primary)
                and self.boundary.empty())

    def _owner_ready(self):
        # A missing pidfd is a startup fault, never evidence of owner exit.
        return (self.ops.ready(self.owner_fd) if self.owner_fd is not None
                else self.ops.status(self.process) is not None)

    def _recover(self, timeout=1.5):
        deadline = self.ops.now() + timeout
        errors = []
        def check():
            if self.ops.now() >= deadline:
                raise CleanupIncomplete("sdk-recovery-budget", pending=("instance",))
        try:
            self.boundary.kill()
            check()
        except Exception as error:
            errors.append(error)
        try:
            if self.owner_fd is not None:
                # Also cover a stopped owner before its first group migration.
                stop_exact(self.owner_fd, self.ops, deadline)
            check()
        except Exception as error:
            errors.append(error)
        try:
            stop_exact(self.primary, self.ops, deadline)
            check()
        except Exception as error:
            errors.append(error)
        try:
            while True:
                check()
                proved = self._proof()
                check()
                if proved:
                    break
                if self.owner_fd is None:
                    # Cover migration after a startup pidfd-acquisition fault.
                    self.boundary.kill()
                self.ops.sleep(min(.01, deadline - self.ops.now()))
        except Exception as error:
            errors.append(error)
        self.recovered = not errors
        if errors:
            raise CleanupIncomplete("sdk-recovery", problems=errors, resource=self)

    def observe_exit(self, deadline=None):
        if self.receipt is not None or self.fault is not None:
            return
        if deadline is None:
            deadline = self.ops.now() + .5
        def check():
            if self.ops.now() >= deadline:
                raise CleanupIncomplete("completion-budget", pending=("evidence",))
        try:
            check()
            if self.owner_fd is None or not self._owner_ready():
                raise ValueError("owner still running/unknown")
            status = self.ops.status(self.process)
            check()
            if type(status) is not int or status != 0:
                raise ValueError(f"abnormal/unknown cleanup owner status {status!r}")
            receipt = validate_completion(self.transport.receive(), self.instance,
                                          self.boundary.identity, self.primary is not None)
            if not self._proof():
                raise ValueError("acknowledgement lacks kernel/primary exit proof")
            check()
            self.receipt = receipt
        except Exception as error:
            self.fault = CleanupIncomplete("owner-completion", problems=(error,), resource=self)
            try:
                self._recover()
            except Exception as recovery_error:
                self.fault = CleanupIncomplete("owner-completion", problems=(error, recovery_error),
                                               resource=self)

    def finish(self, timeout=3):
        deadline = self.ops.now() + timeout
        if self.receipt is not None:
            try:
                if not self._proof():
                    raise ValueError("completion proof changed")
                if self.ops.now() >= deadline:
                    raise CleanupIncomplete("completion-budget", pending=("evidence",))
                return self.receipt
            except Exception as error:
                self.receipt = None
                self.fault = CleanupIncomplete("completion-changed", problems=(error,), resource=self)
                try:
                    self._recover()
                except Exception as recovery_error:
                    self.fault = CleanupIncomplete("completion-changed", problems=(error, recovery_error),
                                                   resource=self)
        if self.fault is None:
            try:
                if self.owner_fd is None:
                    raise ValueError("cleanup owner pidfd unavailable")
                self.ops.signal(self.owner_fd, signal.SIGTERM)
                self.ops.ready(self.owner_fd, max(0, deadline - self.ops.now()))
                if self.ops.now() >= deadline:
                    raise CleanupIncomplete("owner-wait-budget", pending=("owner",))
                self.observe_exit(deadline)
            except Exception as error:
                self.fault = CleanupIncomplete("owner-stop", problems=(error,), resource=self)
                try:
                    self._recover()
                except Exception as recovery_error:
                    self.fault = CleanupIncomplete("owner-stop", problems=(error, recovery_error),
                                                   resource=self)
            if self.receipt is not None:
                return self.receipt
        if not self.fault_reported:
            self.fault_reported = True
            raise self.fault
        self._recover()
        self.receipt = {"state": "complete", "instance": self.instance,
                        "boundary": list(self.boundary.identity), "source": "sdk-recovery",
                        "owner_fault": str(self.fault)}
        return self.receipt


class CompletionSocket:
    def __init__(self, control):
        self.control = control

    def receive(self):
        # Exactly one final datagram is expected after the owner exits. An
        # earlier bounded-attempt NACK can precede it; cap the protocol at two.
        import socket
        last = b""
        for index in range(3):
            if not select.select([self.control], [], [], 0)[0]:
                break
            data, ancillary, flags, _address = self.control.recvmsg(8192, 0)
            if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or ancillary:
                raise ValueError("truncated cleanup acknowledgement")
            if not data:
                break
            if index == 2:
                raise ValueError("too many cleanup acknowledgements")
            last = data
        return last


class BootstrapRetirement:
    """Retain every partially constructed boundary until retirement succeeds."""
    def __init__(self, resources):
        self.resources = list(resources)

    def retire(self):
        errors = []
        for resource in list(self.resources):
            try:
                resource.retire()
                self.resources.remove(resource)
            except Exception as error:
                errors.append(error)
        if errors:
            raise CleanupIncomplete("boundary-bootstrap-retire", pending=self.resources,
                                    problems=errors, resource=self)


class CgroupConstruction:
    """Unhanded allocation: retryable exact leaf and descriptor accounting."""
    def __init__(self, parent, name):
        self.parent, self.name = parent, name
        self.opened = []
        self.creation_state = "not-started"
        self.identity = None
        self.closer = RetainedCloser(close_descriptor, os)
        self._allocation = None

    @property
    def created(self):
        return self.creation_state == "created"

    @created.setter
    def created(self, value):
        self.creation_state = "created" if value else "absent"

    def _publish_opened(self):
        pending = self._allocation
        if pending is None:
            return
        if len(pending) != 1:
            raise CleanupIncomplete("cgroup-allocation-outcome", resource=self)
        fd = pending[0]
        if not isinstance(fd, DescriptorIncarnation) and isinstance(fd, int):
            self._allocation = (self.closer.acquired(fd),)
            fd = self._allocation[0]
        if fd not in self.opened:
            self.opened.append(fd)
        self._allocation = None
        return fd

    def open(self, *args, **kwargs):
        if self._allocation is not None:
            raise CleanupIncomplete("cgroup-allocation-pending", resource=self)
        self._allocation = ()
        try:
            self._allocation = (os.open(*args, **kwargs),)
        except OSError:
            self._allocation = None  # A failed open returns no allocated FD.
            raise
        return self._publish_opened()

    def duplicate(self, fd):
        if self._allocation is not None:
            raise CleanupIncomplete("cgroup-allocation-pending", resource=self)
        self._allocation = ()
        try:
            self._allocation = (os.dup(fd),)
        except OSError:
            self._allocation = None
            raise
        return self._publish_opened()

    def close(self, fd):
        release(fd, self.closer, lambda: self.opened.remove(fd))

    def retire(self):
        errors = []
        try:
            self._publish_opened()
        except Exception as error:
            errors.append(error)
        if self.creation_state == "pending":
            # mkdir may have succeeded before cancellation hid its return.
            # An exact parent-relative rmdir is the only discharge: success
            # or ENOENT proves the unique leaf is gone; nonempty/other errors
            # retain the parent and pending journal for a later retry. ENODEV
            # is what kernfs can report for an operation on an already-removed
            # cgroup node: no live child exists below a removed parent, so it
            # discharges exactly like ENOENT. No other errno does.
            try:
                os.rmdir(self.name, dir_fd=self.parent)
            except OSError as error:
                if error.errno in (errno.ENOENT, errno.ENODEV):
                    self.creation_state = "absent"
                else:
                    errors.append(error)
            except Exception as error:
                errors.append(error)
            else:
                self.creation_state = "absent"
        if self.created:
            try:
                try:
                    st = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
                except OSError as error:
                    if error.errno not in (errno.ENOENT, errno.ENODEV):
                        raise
                    st = None  # Pinned cgroup parent was pruned by its manager.
                if st is not None:
                    if self.identity is None or (st.st_dev, st.st_ino) != self.identity:
                        raise CleanupIncomplete("bootstrap-boundary-identity", resource=self)
                    os.rmdir(self.name, dir_fd=self.parent)  # Kernel rejects a live/nonempty group.
                self.creation_state = "absent"
            except Exception as error:
                errors.append(error)
        for fd in list(self.opened):
            if self._allocation == (fd,):
                continue  # Publication must resolve before retirement of this incarnation.
            try:
                self.close(fd)
            except Exception as error:
                errors.append(error)
        # An unresolved leaf still needs its exact parent for stat/rmdir retry.
        # Other independent file descriptors are released even in that case.
        if (self.parent is not None and not self.created
                and self.creation_state != "pending"):
            try:
                release(self.parent, self.closer, lambda: setattr(self, "parent", None))
            except Exception as error:
                errors.append(error)
        if errors:
            raise CleanupIncomplete("bootstrap-boundary-handles", pending=self.opened,
                                    problems=errors, resource=self)


class CgroupBoundary:
    """Pinned domain cgroup, created before the owner or guardian is spawned.

    The platform adapter authenticates and provisions a fresh delegated scope
    containing only the gated owner. No ambient directory grants authority.
    cgroup.kill, migration permission and no external writers/migrators are
    required before the owner may start any descendants.
    This is lifetime accounting, not a sandbox for hostile same-UID programs.
    """
    def __init__(self, directory, join, kill, events, *, parent=None, name=None):
        self.directory, self.join_fd, self.kill_fd, self.events_fd = directory, join, kill, events
        st = os.fstat(directory)
        self.identity = (st.st_dev, st.st_ino)
        self.parent, self.name = parent, name
        self.retired = False
        self.removed = False
        self.closer = RetainedCloser(close_descriptor, os)
        leases = {fd: getattr(fd, "acquisition_lease", None) for fd in self.fds()}
        if parent is not None:
            leases[parent] = getattr(parent, "acquisition_lease", None)
        if all(lease is not None for lease in leases.values()):
            self.closer = SharedDescriptorCloser(leases)

    @staticmethod
    def _filesystem(fd):
        # glibc Linux statfs ABI on the supported 64-bit Linux hosts. Other
        # ABIs need a separately reviewed adapter, rather than a guessed layout.
        if sys.platform != "linux" or ctypes.sizeof(ctypes.c_long) != 8:
            raise RuntimeError("private bus cgroup adapter requires 64-bit Linux")
        class Statfs(ctypes.Structure):
            _fields_ = [(n, ctypes.c_long) for n in
                        ("type", "bsize", "blocks", "bfree", "bavail", "files", "ffree")]
            _fields_ += [("fsid", ctypes.c_int * 2), ("namelen", ctypes.c_long),
                         ("frsize", ctypes.c_long), ("flags", ctypes.c_long),
                         ("spare", ctypes.c_long * 4)]
        libc = ctypes.CDLL(None, use_errno=True)
        libc.fstatfs.argtypes = (ctypes.c_int, ctypes.POINTER(Statfs))
        libc.fstatfs.restype = ctypes.c_int
        result = Statfs()
        if libc.fstatfs(fd, ctypes.byref(result)):
            raise OSError(ctypes.get_errno(), "cannot verify cgroup filesystem")
        if result.type != 0x63677270:
            raise RuntimeError("private bus root must be a cgroup-v2 filesystem")

    @classmethod
    def create(cls, env, instance):
        raise RuntimeError("ambient cgroup paths are not delegation; use authenticated PlatformScope")

    @classmethod
    def create_at(cls, parent, name):
        """Consume a parent directory fd; never accept a pre-existing leaf."""
        allocation = CgroupConstruction(parent, name)
        try:
            cls._filesystem(parent)
            allocation.creation_state = "pending"
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent)
            except OSError:
                # A failed mkdir has no created entry. Other BaseException
                # outcomes remain unknown under the prepublished journal.
                allocation.creation_state = "absent"
                raise
            allocation.creation_state = "created"
            st = os.stat(name, dir_fd=parent, follow_symlinks=False)
            allocation.identity = (st.st_dev, st.st_ino)
            directory = allocation.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                        dir_fd=parent)
            st = os.fstat(directory)
            if (st.st_dev, st.st_ino) != allocation.identity:
                raise CleanupIncomplete("bootstrap-boundary-open-identity", resource=allocation)
            def open_file(name, mode):
                return allocation.open(name, mode | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            kind = open_file("cgroup.type", os.O_RDONLY)
            if os.read(kind, 64) != b"domain\n":
                raise RuntimeError("private bus requires a domain cgroup")
            allocation.close(kind)
            result = cls(directory, open_file("cgroup.procs", os.O_WRONLY),
                         open_file("cgroup.kill", os.O_WRONLY),
                         open_file("cgroup.events", os.O_RDONLY), parent=parent, name=name)
            if not result.empty():
                raise RuntimeError("new private bus boundary is already populated")
            return result
        except BaseException as error:
            try:
                allocation.retire()
            except Exception as cleanup_error:
                raise CleanupIncomplete("cgroup-construction", problems=(error, cleanup_error),
                                        resource=allocation) from error
            error.resource = allocation  # Actual retired leaf, never a guessed no-effect outcome.
            raise

    def fds(self):
        return (self.directory, self.join_fd, self.kill_fd, self.events_fd)

    def empty(self):
        if self.removed:
            return True
        self.closer.check_signal(self.events_fd)
        try:
            os.lseek(self.events_fd, 0, os.SEEK_SET)
            return parse_events(os.read(self.events_fd, 4097))
        except OSError as error:
            if error.errno != errno.ENODEV:
                raise
            # A pinned cgroup.events kernfs node returns ENODEV after cgroup
            # removal. Its interface cannot be unlinked separately, and the
            # kernel refuses rmdir while the group/subtree has live members.
            # systemd prunes empty scopes before the SDK necessarily polls.
            # This is dead-object proof, not a missing pathname/permission
            # fallback. Other errors and malformed events remain failures.
            self.removed = True
            return True

    def kill(self):
        if self.removed:
            return
        self.closer.check_signal(self.kill_fd)
        try:
            os.lseek(self.kill_fd, 0, os.SEEK_SET)
            if os.write(self.kill_fd, b"1") != 1:
                raise OSError("short cgroup kill write")
        except OSError as error:
            if error.errno != errno.ENODEV or not self.empty() or not self.removed:
                raise

    def retire(self):
        if self.retired:
            return
        if not self.removed and not self.empty():
            raise CleanupIncomplete("boundary-retire", pending=("cgroup",), resource=self)
        if self.removed:
            self.name = None  # Never touch a replacement of the dead object.
        if self.parent is not None and self.name is not None:
            self.closer.check_signal(self.parent)
            try:
                st = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
            except FileNotFoundError:
                st = None
            if st is not None:
                if (st.st_dev, st.st_ino) != self.identity:
                    raise CleanupIncomplete("boundary-replaced", resource=self)
                os.rmdir(self.name, dir_fd=self.parent)
            self.name = None
            self.removed = True
        # Close only handles still retained, so a partial close is retryable.
        for key in ("join_fd", "kill_fd", "events_fd", "directory", "parent"):
            fd = getattr(self, key)
            if fd is not None:
                release(fd, self.closer, lambda: setattr(self, key, None))
        self.retired = True


class BoundaryHandoff:
    """Retain the duplicate and the actual constructed boundary across handoff.

    A builder that loses its own return leaves an explicit unknown outcome.
    Neither a duplicate close nor another build can resolve that uncertainty.
    """
    def __init__(self, root, instance, builder=None):
        self.root, self.instance = root, instance
        self.builder = builder or InstanceBoundary.create_at
        self.duplicate = CgroupConstruction(None, "")
        self.attempted = False
        self.outcome = None
        self.failed = False

    def birth(self):
        if self.attempted:
            if self.outcome is None:
                raise CleanupIncomplete("boundary-handoff-outcome", resource=self)
            if self.failed:
                raise CleanupIncomplete("boundary-handoff-failed", resource=self)
            return self.outcome[0]
        self.duplicate._publish_opened()
        if not self.duplicate.opened:
            self.duplicate.duplicate(self.root)
        fd = self.duplicate.opened[0]
        self.attempted = True
        try:
            self.outcome = (self.builder(fd, self.instance),)
        except BaseException as error:
            self.failed = True
            # create_at's contract retires the consumed parent before raising,
            # or attaches its surviving exact resource. Retain that resource.
            resource = getattr(error, "resource", None)
            if resource is not None:
                self.outcome = (resource,)
            if resource is None:
                error.resource = self
            raise
        return self.outcome[0]

    def retire(self):
        if self.attempted:
            if self.outcome is None:
                raise CleanupIncomplete("boundary-handoff-outcome", resource=self)
            resource = self.outcome[0]
            if resource is not None:
                resource.retire()
            # The builder consumed this exact saved duplicate. It cannot leave
            # raw authority in the producer after the recipient retired it.
            self.duplicate.opened.clear()
        self.duplicate.retire()


class InstanceBoundary:
    """Outer owner+children lifetime, inner activation cleanup without self-kill.

    The owner joins the outer group BEFORE any fork. The guardian inherits it
    at birth, then enters the inner group before daemon birth. SDK hard-loss
    recovery kills the outer tree, including either side of that migration.
    """
    def __init__(self, outer, inner):
        self.outer, self.inner = outer, inner
        self.identity = (*outer.identity, *inner.identity)

    @classmethod
    def create(cls, env, instance):
        raise RuntimeError("private bus boundaries require authenticated platform delegation")

    @classmethod
    def create_at(cls, parent, instance):
        outer = CgroupBoundary.create_at(parent, "kilix-bus-" + instance)
        outer._inner_handoff = BoundaryHandoff(outer.directory, "activated", CgroupBoundary.create_at)
        try:
            inner = outer._inner_handoff.birth()
        except BaseException as error:
            resource = getattr(error, "resource", None)
            resources = ([resource] if callable(getattr(resource, "retire", None)) else [])
            retirement = BootstrapRetirement([*resources, outer])
            try:
                retirement.retire()
            except Exception as cleanup_error:
                raise CleanupIncomplete("boundary-bootstrap", problems=(error, cleanup_error),
                                        resource=retirement) from error
            raise
        return cls(outer, inner)

    def fds(self):
        return (*self.outer.fds(), *self.inner.fds())

    def kill(self):
        self.outer.kill()

    def empty(self):
        return self.outer.empty()

    def retire(self):
        self.inner.retire()
        handoff = getattr(self.outer, "_inner_handoff", None)
        if handoff is not None:
            handoff.retire()
        self.outer.retire()
