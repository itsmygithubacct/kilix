"""Primary identity comes from the gated child's SELF-opened pidfd.

There is no parent numeric-PID acquisition/reacquisition, even if a concurrent
waiter already reaped it. Poll/wait/signals use the retained pidfd. Popen wait
bookkeeping shares its owning lock, preventing its internal waitpid path from
reaping a recycled PID. Native SIGCHLD changes/other FD writers are not allowed.
"""
import enum
import os
import select
import signal
import struct
import subprocess
import threading
import time

if __package__:
    from .bus_ownership import CleanupIncomplete, LinuxProcesses, release, stop_exact, CheckedDescriptor
else:
    from bus_ownership import CleanupIncomplete, LinuxProcesses, release, stop_exact, CheckedDescriptor


class UnknownExitStatus(enum.Enum):
    REAPED_ELSEWHERE = "exact process reaped; status collected by another waiter"


class PidfdProcess:
    def __init__(self, process, fd, ops=None):
        self.process, self.fd = process, fd
        self.ops = ops or LinuxProcesses()
        self.lock = threading.RLock()
        self.result = None
        self._kilix_primary_pin = self
        self._kilix_unpinned_gated = False

    def __getattr__(self, name):
        return getattr(self.process, name)

    def poll(self):
        with self.lock:
            if self.result is not None:
                return self.result
            if self.fd is None:
                raise CleanupIncomplete("primary-pin-released-without-wait", resource=self)
            # Popen's internal lock also covers independent Popen poll/wait.
            with self.process._waitpid_lock:
                if self.process.returncode is not None:
                    self.result = self.process.returncode
                    return self.result
                try:
                    info = self.ops.wait_exact(self.fd)
                except ChildProcessError:
                    if not self.ops.reaped(self.fd):
                        raise CleanupIncomplete("primary-external-wait", resource=self)
                    self.result = UnknownExitStatus.REAPED_ELSEWHERE
                else:
                    if info is None:
                        return None
                    if info.si_code == os.CLD_EXITED:
                        self.result = info.si_status
                    elif info.si_code in (os.CLD_KILLED, os.CLD_DUMPED):
                        self.result = -info.si_status
                    else:
                        raise CleanupIncomplete("primary-wait-status", resource=self)
                # Do not leave a Popen with returncode=None able to poll an old
                # numeric PID after an external exact wait. Unknown is explicit.
                self.process.returncode = self.result
                return self.result

    def wait(self, timeout=None):
        deadline = self.ops.now() + (timeout if timeout is not None else 1.5)
        while self.poll() is None:
            if self.ops.now() >= deadline:
                raise CleanupIncomplete("primary-parent-wait", resource=self)
            self.ops.ready(self.fd, min(.01, deadline - self.ops.now()))
            self.ops.sleep(.001)
        return self.result

    def send_signal(self, sig):
        with self.lock:
            if self.result is None:
                if self.fd is None:
                    raise CleanupIncomplete("primary-signal-authority", resource=self)
                self.ops.signal(self.fd, sig)

    def terminate(self):
        self.send_signal(signal.SIGTERM)

    def kill(self):
        self.send_signal(signal.SIGKILL)

    def release(self):
        with self.lock:
            if self.fd is not None:
                if self.poll() is None:
                    raise CleanupIncomplete("primary-pin-live-release", resource=self)
                release(self.fd, self.ops.close, lambda: setattr(self, "fd", None))


class RemoteProcess:
    """Popen-shaped handle for a primary forked and waited by the root ancestor.

    The client owns duplicates of the exact pidfd and a one-result status pipe.
    It can signal through pidfd, while only the ancestor consumes wait status.
    """
    def __init__(self, pid, fd, status_fd, args, *, ops=None, filesystem=None,
                 stdin=None, stdout=None, stderr=None):
        self.pid, self.fd, self.status_fd = pid, fd, status_fd
        self.args = args
        self.ops = ops or LinuxProcesses()
        filesystem = filesystem or os
        self._pin = getattr(fd, "acquisition_lease", None) or CheckedDescriptor(fd, filesystem)
        if self._pin.identity is None and not hasattr(fd, "acquisition_lease"):
            self._pin.capture()
        self._status_pin = getattr(status_fd, "acquisition_lease", None) or CheckedDescriptor(status_fd, filesystem)
        if self._status_pin.identity is None and not hasattr(status_fd, "acquisition_lease"):
            self._status_pin.capture()
        self.stdin, self.stdout, self.stderr = stdin, stdout, stderr
        self.returncode = None
        self._status = bytearray()
        self._lock = threading.RLock()
        self._user_signals_enabled = False
        self._kilix_primary_pin = self
        self._kilix_unpinned_gated = False

    def poll(self):
        with self._lock:
            if self.returncode is not None:
                return self.returncode
            pin, status = getattr(self, "_pin", None), getattr(self, "_status_pin", None)
            if (pin is not None and (pin.fd is None or pin.uncertain)
                or status is not None and (status.uncertain or self.status_fd is not None and status.fd is None)):
                raise CleanupIncomplete("primary-proxy-descriptor-authority", resource=self)
            if self.status_fd is not None:
                while select_ready(self.status_fd):
                    part = os.read(self.status_fd, 4 - len(self._status))
                    if not part:
                        if self._status:
                            raise CleanupIncomplete("primary-status-truncated", resource=self)
                        if self.ops.reaped(self.fd):
                            self.returncode = UnknownExitStatus.REAPED_ELSEWHERE
                            return self.returncode
                        raise CleanupIncomplete("primary-status-lost", resource=self)
                    self._status.extend(part)
                    if len(self._status) == 4:
                        self.returncode = struct.unpack("!i", self._status)[0]
                        return self.returncode
                    if len(self._status) > 4:
                        raise CleanupIncomplete("primary-status-invalid", resource=self)
            # The ancestor reaps before publishing the four-byte status. A
            # still-open writer means that publication can legitimately be
            # pending even after the pidfd reports a collected process.
            if self.status_fd is None and self.ops.reaped(self.fd):
                raise CleanupIncomplete("primary-detached-without-status", resource=self)
            return None

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            result = self.poll()
            if result is not None:
                return result
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(self.args, timeout)
            # An exited pidfd stays readable and would spin while the ancestor
            # publishes status. The pipe reports either that result or EOF.
            watched = [self.status_fd] if self.status_fd is not None else [self.fd]
            select.select([fd for fd in watched if fd is not None], [], [], remaining)

    def send_signal(self, sig):
        with self._lock:
            if not self._user_signals_enabled:
                raise CleanupIncomplete("primary-signal-before-ancestor-release", resource=self)
            if self._pin.fd is None or self._pin.uncertain:
                raise CleanupIncomplete("primary-proxy-signal-authority", resource=self)
            if self.returncode is None and self.fd is not None and not self.ops.ready(self.fd):
                self.ops.signal(self.fd, sig)

    def enable_user_signals(self):
        with self._lock:
            self._user_signals_enabled = True

    def terminate(self):
        self.send_signal(signal.SIGTERM)

    def kill(self):
        self.send_signal(signal.SIGKILL)

    def release(self):
        with self._lock:
            if self.returncode is None:
                result = self.poll()
                if result is None:
                    raise CleanupIncomplete("primary-proxy-live-release", resource=self)
            errors = []
            if self._pin.fd is None:
                self.fd = None
            if self._status_pin.fd is None:
                self.status_fd = None
            if self._pin.fd is not None:
                try:
                    self._pin.close()
                except BaseException as error:
                    errors.append(error)
                if self._pin.fd is None:
                    self.fd = None
            if self._status_pin.fd is not None:
                try:
                    self._status_pin.close()
                except BaseException as error:
                    errors.append(error)
                if self._status_pin.fd is None:
                    self.status_fd = None
            if errors:
                raise CleanupIncomplete("primary-proxy-release", problems=errors, resource=self)


def select_ready(fd):
    import select
    return bool(select.select([fd], [], [], 0)[0])
