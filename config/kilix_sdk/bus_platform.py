"""Authenticated, per-instance systemd user-scope delegation.

No ambient bus address or cgroup directory is authority. The system bus at its
root-owned installed path identifies PID 1 and its user@UID.service invocation;
that record authenticates the private user-manager peer. Only a gated direct
SDK child is attached. The SDK and the primary retain their session identity.
This is lifetime ownership, not protection against hostile same-UID writers.
"""
import os
import re
import socket
import stat
import struct
import sys
import time
from pathlib import Path

if __package__:
    from .bus_ownership import BoundaryHandoff, CgroupBoundary, InstanceBoundary, CleanupIncomplete, release, close_descriptor, RetainedCloser, DescriptorIncarnation, DescriptorReleased
else:
    from bus_ownership import BoundaryHandoff, CgroupBoundary, InstanceBoundary, CleanupIncomplete, release, close_descriptor, RetainedCloser, DescriptorIncarnation, DescriptorReleased

MANAGER = "org.freedesktop.systemd1.Manager"
UNIT = "org.freedesktop.systemd1.Unit"
SCOPE = "org.freedesktop.systemd1.Scope"
SERVICE = "org.freedesktop.systemd1.Service"
PATH = "/org/freedesktop/systemd1"
SUPPORT = ("Private GUI buses require 64-bit Linux cgroup v2 with cgroup.kill (Linux 5.14+), "
           "systemd 252+ as PID 1, the system bus, python3-dbus and the installed fixed "
           "per-instance supervisor/template/own-account polkit rule. Run the normal "
           "Pleb or shared-SDK platform installer. No leaking fallback is used.")
PRIVATE_BUS_MODULES = ("private_bus.py", "bus_ownership.py", "bus_platform.py", "bus_supervision.py", "bus_supervisor.py", "process_pin.py")


def validate_source_delivery(directory, filesystem=os):
    """Installer preflight only: no manager connection or kernel feature probe."""
    for name in PRIVATE_BUS_MODULES:
        st = filesystem.stat(os.path.join(directory, name), follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError("Incomplete/unsafe private GUI bus platform source: " + name)
    if not callable(getattr(InstanceBoundary, "create_at", None)):
        raise RuntimeError("Private GUI bus ownership/platform modules are incompatible")


class PlatformUnsupported(RuntimeError):
    def __init__(self, reason, resource=None):
        self.resource = resource
        super().__init__(str(reason) + ". " + SUPPORT)


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("private-bus platform bootstrap/retirement deadline expired")
    return value


def invocation(value):
    if not isinstance(value, bytes) or len(value) != 16 or not any(value):
        raise ValueError("missing manager/unit invocation identity")
    return value


def components(path):
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 4096:
        raise ValueError("invalid manager cgroup path")
    parts = path[1:].split("/")
    if not parts or any(not p or p in (".", "..") or "\x00" in p for p in parts):
        raise ValueError("invalid manager cgroup path components")
    return parts


class SystemdTransport:
    """Bounded synchronous D-Bus calls on pinned peers; no global environment."""
    def __init__(self):
        self.system = self.user = None
        self.uid = os.getuid()
        if self.uid == 0 or self.uid != os.geteuid():
            raise PlatformUnsupported("root/setuid private GUI sessions are unsupported")
        try:
            import dbus
            from dbus.bus import BusConnection
            from dbus.connection import Connection
        except ImportError as error:
            raise PlatformUnsupported("python3-dbus is unavailable") from error
        self.dbus, self.BusConnection, self.Connection = dbus, BusConnection, Connection

    def _safe_directory(self, path, uid):
        st = os.stat(path, follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or st.st_mode & 0o022:
            raise PlatformUnsupported("unsafe installed manager directory: " + path)

    def _call(self, connection, destination, path, interface, method, args, deadline):
        proxy = connection.get_object(destination, path, introspect=False)
        value = getattr(self.dbus.Interface(proxy, interface), method)(*args, timeout=remaining(deadline))
        remaining(deadline)
        return value

    def _root_record(self, deadline):
        owner = str(self._call(self.system, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                               "org.freedesktop.DBus", "GetNameOwner", ("org.freedesktop.systemd1",), deadline))
        uid = int(self._call(self.system, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                            "org.freedesktop.DBus", "GetConnectionUnixUser", (owner,), deadline))
        pid = int(self._call(self.system, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                            "org.freedesktop.DBus", "GetConnectionUnixProcessID", (owner,), deadline))
        if uid != 0 or pid != 1:
            raise PlatformUnsupported("system manager is not the authenticated root PID 1")
        path = str(self._call(self.system, owner, PATH, MANAGER, "GetUnit", (f"user@{self.uid}.service",), deadline))
        unit = self._properties(self.system, owner, path, UNIT, deadline)
        service = self._properties(self.system, owner, path, SERVICE, deadline)
        if unit.get("Id") != f"user@{self.uid}.service" or unit.get("ActiveState") != "active" or not service.get("Delegate"):
            raise PlatformUnsupported("no active delegated PAM user manager")
        record = (owner, int(service["MainPID"]), invocation(unit["InvocationID"]), service["ControlGroup"])
        components(record[3])
        if record[1] <= 1:
            raise PlatformUnsupported("user manager has no exact live MainPID")
        if str(self._call(self.system, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                          "org.freedesktop.DBus", "GetNameOwner", ("org.freedesktop.systemd1",), deadline)) != owner:
            raise PlatformUnsupported("system manager changed during authentication")
        return record

    def _properties(self, connection, destination, path, interface, deadline):
        raw = self._call(connection, destination, path, "org.freedesktop.DBus.Properties", "GetAll", (interface,), deadline)
        result = {}
        for key, value in raw.items():
            key = str(key)
            if key == "InvocationID":
                result[key] = bytes(value)
            elif key in ("Delegate", "Transient", "SendSIGKILL"):
                if not isinstance(value, self.dbus.Boolean):
                    raise ValueError("invalid manager boolean: " + key)
                result[key] = bool(value)
            elif key in ("MainPID", "TimeoutStopUSec"):
                result[key] = int(value)
            elif key in ("Id", "Description", "ControlGroup", "ActiveState", "KillMode", "CollectMode"):
                result[key] = str(value)
        return result

    def connect(self, deadline):
        # Neither XDG_RUNTIME_DIR nor any DBUS_* value selects these peers.
        self._safe_directory("/run", 0)
        self._safe_directory("/run/dbus", 0)
        if not stat.S_ISSOCK(os.stat("/run/dbus/system_bus_socket", follow_symlinks=False).st_mode):
            raise PlatformUnsupported("installed system bus socket is missing/unsafe")
        self.system = self.BusConnection("unix:path=/run/dbus/system_bus_socket")
        self.record = self._root_record(deadline)
        self._safe_directory("/run/user", 0)
        self._safe_directory(f"/run/user/{self.uid}", self.uid)
        self._safe_directory(f"/run/user/{self.uid}/systemd", self.uid)
        self.user = self.Connection(f"unix:path=/run/user/{self.uid}/systemd/private")
        self.attest(deadline)
        version = str(self._call(self.user, None, PATH, "org.freedesktop.DBus.Properties", "Get",
                                 (MANAGER, "Version"), deadline))
        match = re.match(r"([0-9]+)", version)
        if not match or int(match[1]) < 252:
            raise PlatformUnsupported("systemd user manager version is below 252")
        return self.record[3]

    def attest(self, deadline):
        fd = self.user.get_unix_fd()
        if fd is None:
            raise PlatformUnsupported("user manager did not supply a Unix peer")
        with socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            pid, uid, _gid = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if (uid != self.uid or pid != self.record[1] or
            self._root_record(deadline) != self.record):
            raise PlatformUnsupported("private user-manager peer or invocation changed")

    def unit(self, name, deadline):
        try:
            path = str(self._call(self.user, None, PATH, MANAGER, "GetUnit", (name,), deadline))
        except self.dbus.DBusException as error:
            if error.get_dbus_name() == "org.freedesktop.systemd1.NoSuchUnit":
                return None
            raise
        unit = self._properties(self.user, None, path, UNIT, deadline)
        scope = self._properties(self.user, None, path, SCOPE, deadline)
        return dict(unit, **scope, object_path=path)

    def create(self, name, description, pid, deadline):
        d = self.dbus
        properties = d.Array([
            ("Description", d.String(description)), ("PIDs", d.Array([d.UInt32(pid)], signature="u")),
            ("Delegate", d.Boolean(True)), ("KillMode", d.String("control-group")),
            ("SendSIGKILL", d.Boolean(True)), ("TimeoutStopUSec", d.UInt64(1500000)),
            ("CollectMode", d.String("inactive-or-failed")),
        ], signature="(sv)")
        return self._call(self.user, None, PATH, MANAGER, "StartTransientUnit",
                          (name, "fail", properties, d.Array([], signature="(sa(sv))")), deadline)

    def member(self, pid, deadline):
        return str(self._call(self.user, None, PATH, MANAGER, "GetUnitByPID", (self.dbus.UInt32(pid),), deadline))

    def stop(self, name, deadline):
        self._call(self.user, None, PATH, MANAGER, "StopUnit", (name, "fail"), deadline)

    def close(self):
        errors = []
        for key in ("user", "system"):
            connection = getattr(self, key)
            if connection is not None:
                try:
                    connection.close()
                    setattr(self, key, None)
                except Exception as error:
                    errors.append(error)
        if errors:
            raise CleanupIncomplete("platform-connections", problems=errors, resource=self)


class LinuxDelegation:
    def __init__(self):
        self.pending = []
        self._closer = None
        self._allocation = None
        self._handoff = None
        self._last_handoff = None

    @property
    def closer(self):
        # Capture the effect adapters when this owner first uses them. Keep
        # the same leases on every subsequent attempt, including cancellation.
        if self._closer is None:
            self._closer = RetainedCloser(close_descriptor, os)
        return self._closer

    def close_pending(self):
        errors = []
        handoff = self._handoff
        if handoff is not None and handoff["accepted"]:
            # The recipient saved this exact returned incarnation before the
            # acknowledgement. Finish list retirement idempotently.
            fd = handoff["fd"]
            self.pending = [held for held in self.pending if held is not fd]
            self._last_handoff = (handoff["recipient"], fd)
            self._handoff = None
            handoff = None
        if handoff is not None:
            errors.append("delegation root awaits exact recipient acknowledgement")
        boundary_handoff = getattr(self, "_boundary_handoff", None)
        if boundary_handoff is not None:
            try:
                boundary_handoff.retire()
            except BaseException as error:
                errors.append(error)
        try:
            self._publish_opened()
        except Exception as error:
            errors.append(error)
        for fd in list(self.pending):
            if (self._allocation == (fd,) or
                    handoff is not None and handoff["fd"] is fd):
                continue
            try:
                release(fd, self.closer, lambda fd=fd: self.pending.remove(fd))
            except Exception as error:
                errors.append(error)
        if errors:
            raise CleanupIncomplete("delegation-bootstrap-handles", pending=self.pending,
                                    problems=errors, resource=self)

    def acquired(self, fd):
        acquired = getattr(self.closer, "acquired", None)
        if acquired is not None:
            return acquired(fd)
        return fd

    def _publish_opened(self):
        pending = self._allocation
        if pending is None:
            return
        if len(pending) != 1:
            raise CleanupIncomplete("delegation-allocation-outcome", resource=self)
        fd = pending[0]
        if not isinstance(fd, DescriptorIncarnation) and isinstance(fd, int):
            self._allocation = (self.acquired(fd),)
            fd = self._allocation[0]
        if fd not in self.pending:
            self.pending.append(fd)
        self._allocation = None
        return fd

    def claim(self, recipient):
        """Return this recipient's exact saved root token without reopening.

        The token stops being claimable once close() has released it: a
        released descriptor number must never be handed to a second closer.
        """
        handoff = self._handoff
        if handoff is None:
            if (self._last_handoff is not None and self._last_handoff[0] is recipient):
                return self._last_handoff[1]
            return None
        if handoff["recipient"] is not recipient:
            raise CleanupIncomplete("delegation-handoff-mismatch", resource=self)
        return handoff["fd"]

    def acknowledge(self, recipient, fd):
        handoff = self._handoff
        if handoff is None:
            if (self._last_handoff is not None and self._last_handoff[0] is recipient
                    and self._last_handoff[1] is fd):
                return fd
            raise CleanupIncomplete("delegation-handoff-missing", resource=self)
        if handoff["recipient"] is not recipient or handoff["fd"] is not fd:
            raise CleanupIncomplete("delegation-handoff-mismatch", resource=self)
        handoff["accepted"] = True
        self.close_pending()
        return fd

    def _open(self, *args, **kwargs):
        if self._allocation is not None:
            raise CleanupIncomplete("delegation-allocation-pending", resource=self)
        self._allocation = ()
        try:
            self._allocation = (os.open(*args, **kwargs),)
        except OSError:
            self._allocation = None
            raise
        return self._publish_opened()

    def open(self, path, pid, *, member_cgroup=None, recipient=None):
        handoff = self._handoff
        if handoff is not None:
            if handoff["recipient"] is recipient:
                return handoff["fd"]
            raise CleanupIncomplete("delegation-handoff-pending", resource=self)
        self.close_pending()
        parts = components(path)
        fd = self._open("/sys/fs/cgroup", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            CgroupBoundary._filesystem(fd)
            for part in parts:
                child = self._open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                release(fd, self.closer, lambda fd=fd: self.pending.remove(fd))
                fd = child
            if member_cgroup is not None:
                # Fixed root ancestor only: kernel-read live SDK membership,
                # never a launch packet or ambient environment-selected path.
                components(member_cgroup)
                if member_cgroup != path and not member_cgroup.startswith(path + "/"):
                    raise PlatformUnsupported("SDK cgroup is outside the pinned physical session")
                st = os.fstat(fd)
                if not stat.S_ISDIR(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
                    raise PlatformUnsupported("physical session ancestor is not root-owned and protected")
                lines = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
                if [line[3:] for line in lines if line.startswith("0::")] != [member_cgroup]:
                    raise PlatformUnsupported("SDK changed cgroup during physical session pinning")
                if recipient is None:
                    self.pending = [held for held in self.pending if held is not fd]
                else:
                    self._handoff = {"recipient": recipient, "fd": fd, "accepted": False}
                return fd
            procs = self._open("cgroup.procs", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            try:
                if os.read(procs, 4097) != f"{pid}\n".encode():
                    raise PlatformUnsupported("delegation does not contain only the gated owner")
            finally:
                release(procs, self.closer, lambda: self.pending.remove(procs))
            if recipient is None:
                self.pending = [held for held in self.pending if held is not fd]
            else:
                self._handoff = {"recipient": recipient, "fd": fd, "accepted": False}
            return fd
        except BaseException as error:
            try:
                self.close_pending()
            except Exception as cleanup_error:
                raise CleanupIncomplete("delegation-bootstrap", pending=self.pending,
                                        problems=(error, cleanup_error), resource=self) from error
            raise

    def boundary(self, root, instance):
        handoff = getattr(self, "_boundary_handoff", None)
        if handoff is None:
            self._boundary_handoff = handoff = BoundaryHandoff(root, instance)
        elif handoff.root is not root or handoff.instance != instance:
            raise CleanupIncomplete("delegation-boundary-handoff-conflict", resource=self)
        return handoff.birth()

    def close(self, root):
        try:
            self.closer(root)
        except DescriptorReleased:
            # Linux invalidated the descriptor before reporting a late error.
            self._forget_handoff(root)
            raise
        self._forget_handoff(root)

    def _forget_handoff(self, root):
        last = self._last_handoff
        if last is not None and last[1] is root:
            self._last_handoff = None

    def forget_released(self, root):
        self.closer.forget_released(root)


class PlatformScope:
    """Retain the authenticated scope and pinned boundary across failed cleanup."""
    def __init__(self, instance, transport=None, filesystem=None):
        if not re.fullmatch(r"[0-9a-f]{32}", instance):
            raise ValueError("invalid private-bus instance token")
        self.instance = instance
        self.name = "kilix-private-bus-" + instance + ".scope"
        self.description = "Kilix private bus " + instance
        self.transport = transport if transport is not None else SystemdTransport()
        self.filesystem = filesystem if filesystem is not None else LinuxDelegation()
        self.root = self.boundary = self.partial_boundary = self.identity = None
        self._root_recipient = object()
        self._root_handoff_started = False
        self._root_released = False
        self._root_handoff_path = self._root_handoff_pid = None
        self.attempted = self.closed = self._scope_retired = False

    def prepare(self, timeout=4):
        deadline = time.monotonic() + timeout
        try:
            self.manager_root = self.transport.connect(deadline)
            components(self.manager_root)
            if self.transport.unit(self.name, deadline) is not None:
                raise PlatformUnsupported("fresh instance scope name already exists")
            remaining(deadline)
        except Exception as error:
            raise PlatformUnsupported(error, self) from error

    def _verify(self, unit):
        if (unit is None or unit.get("Id") != self.name or unit.get("Description") != self.description or
            unit.get("Transient") is not True or unit.get("Delegate") is not True or
            unit.get("KillMode") != "control-group" or unit.get("SendSIGKILL") is not True or
            unit.get("TimeoutStopUSec") != 1500000 or unit.get("CollectMode") != "inactive-or-failed"):
            raise PlatformUnsupported("manager did not provision the requested private scope", self)
        ident = invocation(unit.get("InvocationID"))
        if self.identity is not None and ident != self.identity:
            raise PlatformUnsupported("private scope invocation was replaced", self)
        path = unit.get("ControlGroup", "")
        if path:
            components(path)
            if not path.startswith(self.manager_root + "/"):
                raise PlatformUnsupported("scope escaped the authenticated user-manager delegation", self)
        return ident

    def attach(self, process, owner_fd, ops, timeout=4):
        deadline = time.monotonic() + timeout
        try:
            self.transport.attest(deadline)
            if ops.ready(owner_fd):
                raise RuntimeError("gated owner exited before delegation")
            self.attempted = True  # Includes ambiguous StartTransientUnit timeout.
            self.transport.create(self.name, self.description, process.pid, deadline)
            while True:
                unit = self.transport.unit(self.name, deadline)
                if unit is not None and unit.get("ActiveState") == "active":
                    break
                remaining(deadline)
                if ops.ready(owner_fd) or (unit is not None and unit.get("ActiveState") in ("failed", "inactive")):
                    raise RuntimeError("private scope failed during gated bootstrap")
                time.sleep(min(.01, remaining(deadline)))
            self.identity = self._verify(unit)
            self.cgroup = unit["ControlGroup"]
            if not self.cgroup or self.transport.member(process.pid, deadline) != unit["object_path"] or ops.ready(owner_fd):
                raise RuntimeError("exact gated owner is not a member of its private scope")
            if self.root is None:
                if callable(getattr(self.filesystem, "acknowledge", None)):
                    # Publish the exact recipient and retry arguments before
                    # open: cancellation can lose its return before root is
                    # assigned, while LinuxDelegation still retains the token.
                    self._root_handoff_started = True
                    self._root_handoff_path, self._root_handoff_pid = self.cgroup, process.pid
                    try:
                        self.root = self.filesystem.open(self.cgroup, process.pid,
                                                         recipient=self._root_recipient)
                    except Exception:
                        # An ordinary refusal (ENOENT, owner not sole member)
                        # ends the attempt; only cancellation leaves the
                        # outcome unknown for retire to reconcile.
                        self._discharge_failed_open()
                        raise
                else:
                    self.root = self.filesystem.open(self.cgroup, process.pid)
            if callable(getattr(self.filesystem, "acknowledge", None)):
                self.filesystem.acknowledge(self._root_recipient, self.root)
                self._root_handoff_started = False
            self.boundary = self.filesystem.boundary(self.root, self.instance)
            self.transport.attest(deadline)
            current = self.transport.unit(self.name, deadline)
            self._verify(current)
            if (current["ControlGroup"] != self.cgroup or current["ActiveState"] != "active" or
                self.transport.member(process.pid, deadline) != current["object_path"] or ops.ready(owner_fd)):
                raise RuntimeError("delegation changed before boundary handoff")
            remaining(deadline)
            return self.boundary
        except Exception as error:
            if isinstance(error, CleanupIncomplete) and callable(getattr(error.resource, "retire", None)):
                self.partial_boundary = error.resource
            raise PlatformUnsupported(error, self) from error

    def _retire_scope(self, deadline):
        if self._scope_retired:
            return
        if self.attempted:
            self.transport.attest(deadline)
            unit = self.transport.unit(self.name, deadline)
            if unit is not None:
                self.identity = self._verify(unit)
                if self.identity is not None and hasattr(self, "cgroup") and unit.get("ControlGroup") not in ("", self.cgroup):
                    raise CleanupIncomplete("scope-path-changed", resource=self)
                if unit["ActiveState"] not in ("inactive", "failed"):
                    self.transport.stop(self.name, deadline)
                while True:
                    unit = self.transport.unit(self.name, deadline)
                    if unit is None:
                        break
                    self._verify(unit)
                    # failed/inactive alone does not prove no live D-state tasks;
                    # systemd retains ControlGroup until recursive emptiness.
                    if unit["ActiveState"] in ("inactive", "failed") and not unit.get("ControlGroup"):
                        break
                    time.sleep(min(.01, remaining(deadline)))
        remaining(deadline)
        self._scope_retired = True

    def _discharge_failed_open(self):
        """An open that raised an ordinary exception handed nothing over."""
        claim = getattr(self.filesystem, "claim", None)
        try:
            retained = claim(self._root_recipient) if callable(claim) else None
        except Exception:
            return  # Keep the attempt open; retire reports the conflict.
        if retained is None:
            self._root_handoff_started = False

    def _root_gone(self):
        self.root = None
        self._root_released = True

    def _claim_root_handoff(self):
        """Recover the exact root token before stopping or releasing it."""
        if getattr(self, "_root_released", False):
            return  # Released once; its number is never claimable again.
        started = getattr(self, "_root_handoff_started", False)
        claim = getattr(self.filesystem, "claim", None)
        claimed = claim(self._root_recipient) if callable(claim) else None
        if claimed is not None:
            if self.root is not None and claimed is not self.root:
                raise CleanupIncomplete("delegation-handoff-mismatch", resource=self)
            self.root = claimed
        elif started and self.root is None:
            if callable(claim):
                # The delegation retains no token for this recipient, so the
                # open never completed a hand-off; its pending descriptors are
                # closed by close_pending. Nothing to recover, nothing to reopen.
                self._root_handoff_started = False
                return
            self.root = self.filesystem.open(
                self._root_handoff_path, self._root_handoff_pid,
                recipient=self._root_recipient)
        if self.root is None or not (started or claimed is not None):
            return
        acknowledge = getattr(self.filesystem, "acknowledge", None)
        if callable(acknowledge):
            acknowledge(self._root_recipient, self.root)
        self._root_handoff_started = False

    def retire(self, timeout=4):
        if self.closed:
            return
        deadline = time.monotonic() + timeout
        # The caller must verify exact owner exit before entering this method.
        # Manager retirement can prune a partially constructed empty leaf;
        # each independent descriptor registry is still attempted on failure.
        errors = []
        try:
            self._claim_root_handoff()
        except Exception as error:
            errors.append(error)
        try:
            self._retire_scope(deadline)
        except Exception as error:
            errors.append(error)
        for key in ("boundary", "partial_boundary"):
            resource = getattr(self, key)
            if resource is not None:
                try:
                    resource.retire()
                    if key == "partial_boundary":
                        self.partial_boundary = None
                except Exception as error:
                    errors.append(error)
        if errors:
            raise CleanupIncomplete("platform-scope-retire", problems=errors, resource=self)
        if self.root is not None:
            try:
                release(self.root, self.filesystem.close, self._root_gone)
            except Exception as error:
                errors.append(error)
        if hasattr(self.filesystem, "close_pending"):
            try:
                self.filesystem.close_pending()
            except Exception as error:
                errors.append(error)
        try:
            self.transport.close()
        except Exception as error:
            errors.append(error)
        if errors:
            raise CleanupIncomplete("platform-scope-handles", problems=errors, resource=self)
        self.closed = True


if __name__ == "__main__":
    if sys.argv[1:] != ["--check-source-delivery"]:
        raise SystemExit("usage: bus_platform.py --check-source-delivery")
    validate_source_delivery(os.path.dirname(os.path.abspath(__file__)))
