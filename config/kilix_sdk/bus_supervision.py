"""Independent, installed per-instance ancestor for private GUI bus ownership.

The system manager starts a fixed root-owned program, never caller code as root.
That program becomes a subreaper BEFORE creating the unprivileged cleanup owner.
Only that ancestor can certify adopted-child waits after SDK AND owner loss.
Neither scope emptiness nor PID 1's live-process culling substitutes for waits.
All native effects live behind adapters; importing this module has none.
"""
import array
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import select
import socket
import stat
import struct
import time

if __package__:
    from .bus_ownership import (CgroupBoundary, InstanceBoundary, CleanupIncomplete,
                               LinuxProcesses, OwnerCleanup, release, stop_exact, CheckedDescriptor, descriptor_incarnation)
    from .bus_platform import (SystemdTransport, PlatformUnsupported, UNIT, SERVICE,
                              MANAGER, PATH, invocation, components, remaining)
else:
    from bus_ownership import (CgroupBoundary, InstanceBoundary, CleanupIncomplete,
                              LinuxProcesses, OwnerCleanup, release, stop_exact, CheckedDescriptor, descriptor_incarnation)
    from bus_platform import (SystemdTransport, PlatformUnsupported, UNIT, SERVICE,
                             MANAGER, PATH, invocation, components, remaining)

VERSION = 2
PREFIX = "kilix-private-bus-supervisor@"
PROGRAM_ROOT = "/usr/libexec/kilix-private-bus-supervisor"
PROGRAM = PROGRAM_ROOT + "/bus_supervisor.py"
FRAGMENT = "/etc/systemd/system/kilix-private-bus-supervisor@.service"
RUNTIME = "/run/kilix-private-bus-supervisor"
MODULES = ("bus_supervisor.py", "bus_supervision.py", "bus_ownership.py", "bus_platform.py")
TOKEN = re.compile(r"[0-9a-f]{32}\Z")
LOGIN = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")


def delivery_id(directory):
    hashes = [hashlib.sha256((Path(directory) / name).read_bytes()).hexdigest() for name in MODULES]
    return hashlib.sha256("\n".join(hashes).encode()).hexdigest()


def installed_program(directory):
    return PROGRAM_ROOT + "/" + delivery_id(directory) + "/bus_supervisor.py"


def instance_name(login, token):
    if (not isinstance(login, str) or not LOGIN.fullmatch(login) or login in (".", "..", "root")
        or not isinstance(token, str) or not TOKEN.fullmatch(token)):
        raise ValueError("invalid private-bus service instance")
    return login + "-" + token


def decode_instance(value):
    login, token = value.rsplit("-", 1)
    if instance_name(login, token) != value:
        raise ValueError("noncanonical private-bus service instance")
    return login, token


def decode(data):
    if not isinstance(data, bytes) or not data or len(data) > 131072:
        raise ValueError("missing/oversized supervision packet")
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate supervision key")
            value[key] = item
        return value
    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != VERSION:
        raise ValueError("unknown supervision protocol")
    return value


class Handles:
    """One-close ownership, with a journal published before every acquisition.

    A leaf may be cancelled after its effect but before returning its handle.
    In that case the pending journal is deliberately unknown: neither another
    acquisition nor completed cleanup is possible. It never invents a handle
    from a number, fstat equality, or an older incarnation.
    """
    def __init__(self, closer, filesystem=None):
        self.closer, self.filesystem = closer, filesystem
        self._ownership = ([], {}, {})
        self.pending = None
        self.objects = []
        # A socket object is an exact lifetime token: it cannot be confused
        # with another object that later reuses the same descriptor number.
        # Keep one promotion record for that lifetime so a lost return value
        # cannot manufacture a second close lease on retry.
        self._socket_promotions = {}

    @staticmethod
    def key(fd):
        return getattr(fd, "ownership", fd)

    @property
    def fds(self):
        return self._ownership[0]

    @fds.setter
    def fds(self, value):
        self._ownership = (value, self.leases, self._ownership[2])

    @property
    def leases(self):
        return self._ownership[1]

    @leases.setter
    def leases(self, value):
        self._ownership = (self.fds, value, self._ownership[2])

    def begin(self, kind, **initial):
        if self.pending is not None:
            raise CleanupIncomplete("supervision-acquisition-pending", resource=self)
        journal = {"kind": kind, "raw": None, "tokens": None}
        journal.update(initial)
        self.pending = journal
        return self.pending

    def allocate(self, allocator, *args, many=False, **kwargs):
        journal = self.begin("fds" if many else "fd")
        # Retain the actual return directly in the pre-published journal.
        # Cancellation inside a leaf/STORE_SUBSCR seam leaves explicit unknown.
        journal["raw"] = (allocator(*args, **kwargs),)
        fds = self.publish_pending()
        self.capture(fds)
        return fds if many else fds[0]

    def allocate_objects(self, allocator, *args, many=False, **kwargs):
        journal = self.begin("objects" if many else "object")
        journal["raw"] = (allocator(*args, **kwargs),)
        return self.publish_pending()

    def promote_socket(self, sock, wrapper):
        journal = self.pending
        if journal is not None:
            raw = journal.get("raw")
            if (journal.get("kind") != "socket-wrapper" or raw is None or len(raw) != 1
                    or raw[0] is not sock or journal.get("wrapper") is not wrapper):
                raise CleanupIncomplete("supervision-acquisition-pending", resource=self)
            if journal.get("promotion") is not None:
                self._socket_promotions.setdefault(sock, journal["promotion"])
            return self.publish_pending()
        promotion = self._socket_promotions.get(sock)
        if promotion is not None:
            if promotion["wrapper"] is not wrapper:
                raise CleanupIncomplete("supervision-socket-promotion-conflict", resource=self)
            recipient = promotion["recipient"]
            if recipient is not None:
                return recipient
            self.begin("socket-wrapper", raw=(sock,), wrapper=wrapper,
                       lease=promotion["lease"], promotion=promotion)
            return self.publish_pending()
        if self.filesystem is None:
            raise CleanupIncomplete("supervision-socket-filesystem-unavailable", resource=self)
        lease = CheckedDescriptor(sock.fileno(), self.filesystem)
        promotion = {"wrapper": wrapper, "lease": lease, "recipient": None}
        self.begin("socket-wrapper", raw=(sock,), wrapper=wrapper, lease=lease,
                   promotion=promotion)
        self._socket_promotions[sock] = promotion
        return self.publish_pending()

    def attach_socket(self, fd, factory, wrapper):
        lease = self.leases[fd]
        if lease.fd is None or lease.uncertain:
            raise CleanupIncomplete("supervision-socket-lease-unavailable", resource=self)
        journal = self.begin("socket-wrapper")
        journal["wrapper"], journal["lease"] = wrapper, lease
        # An interrupted socket(fileno=...) may create and destroy an owning
        # Python object. The old numeric lease then cannot close safely.
        journal["lease"].uncertain = True
        journal["raw"] = (factory(fileno=fd),)
        return self.publish_pending()

    def transfer_object(self, obj):
        self.objects = [held for held in self.objects if held is not obj]

    def packet(self, control, size, contract, *, cloexec=socket.MSG_CMSG_CLOEXEC):
        journal = self.pending
        if journal is None:
            journal = self.begin("receive")
            journal["control"], journal["count"] = control, contract
            try:
                journal["raw"] = (control.recvmsg(size, socket.CMSG_SPACE(9 * array.array("i").itemsize),
                                                 cloexec),)
            except (TimeoutError, BlockingIOError, InterruptedError) as error:
                # Only the native socket's no-delivery contract or an explicit
                # adapter proof permits retry. An arbitrary adapter can consume
                # SCM rights and then raise an ordinary timeout exception.
                if type(control) is socket.socket or getattr(error, "receive_not_consumed", None) is control:
                    self.pending = None
                raise
        elif (journal["kind"] != "receive" or journal.get("control") is not control
              or journal.get("count") != contract):
            raise CleanupIncomplete("supervision-receive-pending", resource=self)
        return self.publish_pending(register=False)

    def publish_pending(self, register=True):
        journal = self.pending
        if journal is None:
            return
        if journal["raw"] is None:
            raise CleanupIncomplete("supervision-acquisition-outcome", resource=self)
        kind = journal["kind"]
        raw = journal["raw"][0]
        if kind == "socket-wrapper":
            promotion = journal.get("promotion")
            if promotion is None:
                promotion = self._socket_promotions.get(raw)
            if promotion is None:
                promotion = {"wrapper": journal["wrapper"], "lease": journal["lease"],
                             "recipient": None}
                journal["promotion"] = promotion
            self._socket_promotions.setdefault(raw, promotion)
            if promotion["wrapper"] is not journal["wrapper"]:
                raise CleanupIncomplete("supervision-socket-promotion-conflict", resource=self)
            if promotion["recipient"] is None:
                # The promotion record already owns the single close lease.
                # If cancellation lands before this assignment, retry may
                # produce another wrapper, but every wrapper shares that same
                # one-close authority and the same raw socket object.
                promotion["recipient"] = journal["wrapper"](raw, promotion["lease"])
            recipient = promotion["recipient"]
            # The concrete returned owning socket is retained. There is no
            # reacquisition through fstat or its descriptor number.
            if promotion["lease"] is not None:
                promotion["lease"].uncertain = False
            self.objects = [held for held in self.objects if held is not raw and held is not recipient] + [recipient]
            self.pending = None
            return recipient
        if kind in ("objects", "object"):
            values = raw if kind == "objects" else (raw,)
            for obj in values:
                if callable(getattr(obj, "close", None)) and not any(obj is held for held in self.objects):
                    self.objects.append(obj)
            self.pending = None
            return raw
        if kind == "receive":
            data, ancillary, flags, _ = raw
            numbers, malformed = [], False
            for level, tag, body in ancillary:
                if level != socket.SOL_SOCKET or tag != socket.SCM_RIGHTS:
                    malformed = True
                    continue
                values = array.array("i")
                values.frombytes(body[:len(body) - len(body) % values.itemsize])
                numbers.extend(values)
                malformed |= bool(len(body) % values.itemsize)
            values = numbers
        else:
            values = raw if kind == "fds" else (raw,)
        if journal["tokens"] is None:
            journal["tokens"] = tuple(descriptor_incarnation(fd) for fd in values)
        fds = journal["tokens"]
        if register:
            self.register(fds)
        if kind == "receive":
            journal["packet"] = (data, fds, malformed, flags)
            # Receive owns the journal until its callback/transaction is saved.
            return journal["packet"]
        self.pending = None
        return fds

    def add(self, fd, *, allocated=False):
        # Compatibility for an ALREADY returned handle. Production allocators
        # use allocate(), which publishes the journal before calling the leaf.
        # Cancellation before entering this API is a caller capability limit.
        if allocated:
            journal = self.begin("fd")
            journal["raw"] = (fd,)
            fd = self.publish_pending()[0]
        else:
            self.register((fd,))
        self.capture((fd,))
        return fd

    def register(self, fds):
        # Build membership and leases together, then publish once. Repeating
        # an exact saved token is idempotent, including after its retirement.
        fds = list(fds)
        retained, leases, retired = list(self.fds), dict(self.leases), self._ownership[2]
        keys = {self.key(fd) for fd in retained}
        for fd in fds:
            key = self.key(fd)
            if key in retired or key in keys:
                continue
            if type(fd) is int and any(isinstance(saved, int) and int(saved) == fd for saved in retired.values()):
                raise CleanupIncomplete("supervision-unproved-numeric-alias", resource=self)
            retained.append(fd)
            keys.add(key)
            if fd not in leases:
                shared = getattr(fd, "acquisition_lease", None)
                if shared is not None:
                    leases[fd] = shared
                elif self.filesystem is not None:
                    leases[fd] = CheckedDescriptor(fd, self.filesystem)
                else:
                    adapter = type("CloseAdapter", (), {})()
                    adapter.close = self.closer
                    leases[fd] = CheckedDescriptor(fd, adapter, raw=False)
                if hasattr(fd, "ownership"):
                    fd.acquisition_lease = leases[fd]
        self._ownership = (retained, leases, retired)
        return fds

    def capture(self, fds):
        errors = []
        for fd in fds:
            try:
                if self.filesystem is not None and fd in self.leases and self.leases[fd].identity is None:
                    self.leases[fd].capture()
            except BaseException as error:
                errors.append(error)
        if errors:
            cancellation = next((error for error in errors if not isinstance(error, Exception)), None)
            if cancellation is not None:
                cancellation.resource = self
                raise cancellation
            raise CleanupIncomplete("supervision-descriptor-capture", pending=self.fds,
                                    problems=errors, resource=self)

    def transfer(self, fd):
        key = self.key(fd)
        retired = self._ownership[2]
        pending = self.pending
        journal_keys = {self.key(held) for held in (pending.get("tokens") or ())} if pending else set()
        if key not in {self.key(held) for held in self.fds} | {self.key(held) for held in self.leases} | set(retired) | journal_keys:
            raise ValueError("descriptor transfer does not belong to this registry")
        retained = [held for held in self.fds if self.key(held) != key]
        leases = {held: lease for held, lease in self.leases.items() if self.key(held) != key}
        self._ownership = (retained, leases, retired | {key: fd})
        return fd

    def transfer_many(self, fds):
        keys = {self.key(fd) for fd in fds}
        owned = {self.key(fd) for fd in self.fds}
        retired = self._ownership[2]
        if not keys.issubset(owned | set(retired)):
            raise ValueError("descriptor transfer does not belong to this registry")
        retained = [fd for fd in self.fds if self.key(fd) not in keys]
        leases = {fd: lease for fd, lease in self.leases.items() if self.key(fd) not in keys}
        # Every alias and the one-close tombstone retire in the same commit.
        self._ownership = (retained, leases, retired | {self.key(fd): fd for fd in fds})

    def release_one(self, fd):
        if self.key(fd) in self._ownership[2]:
            return
        if not any(self.key(fd) == self.key(held) for held in self.fds):
            raise ValueError("descriptor release does not belong to this registry")
        if fd in self.leases:
            lease = self.leases[fd]
            try:
                lease.close()
            finally:
                if lease.fd is None:
                    self.transfer(fd)
        else:
            raise CleanupIncomplete("supervision-descriptor-lease-missing", resource=self)

    def close(self):
        errors = []
        try:
            self.publish_pending()
        except BaseException as error:
            errors.append(error)
        for obj in list(self.objects):
            try:
                obj.close()
                self.objects.remove(obj)
            except BaseException as error:
                errors.append(error)
        for fd in list(self.fds):
            try:
                self.release_one(fd)
            except BaseException as error:
                errors.append(error)
        # A retained delivery cannot be forgotten merely by closing its FDs;
        # it must not allow the next receive to consume another packet.
        if self.pending is not None and self.pending["raw"] is None:
            errors.append("unknown acquisition cannot certify completion")
        if errors:
            raise CleanupIncomplete("supervision-descriptors", pending=self.fds,
                                    problems=errors, resource=self)
        if self.pending is not None:
            self.pending = None  # Known delivery was consumed by exact retirement.


class WaitReceive:
    """Pre-published cleanup journal, independent of the decoded handoff.

    An interrupted kernel delivery/initial incarnation capture can be unknown.
    That state never licenses a second receive or capture by a retained number.
    Once captured, these original leases survive every registry/publication gap.

    Deliberate fail-closed dead end: after an interrupted first capture
    (`capture_started`) an identity that was never recorded is not rebuilt
    from a descriptor number, so retirement of that receive stays blocked even
    after the peer is collected. A number cannot prove which object it names,
    so the block is reported (wait-receive-capture-ambiguous), never cleared.
    """
    def __init__(self):
        self.raw = self.packet = None
        self.leases = {}
        self.captured = False
        self.capture_started = False
        self.started = False

    def capture(self, data, fds, malformed, flags, handles):
        packet = (data, tuple(fds), malformed, flags)
        if self.packet is not None and self.packet != packet:
            raise CleanupIncomplete("wait-receive-packet-replaced", resource=self)
        self.packet = packet
        if self.captured:
            return
        if handles.filesystem is None:
            raise CleanupIncomplete("wait-receive-incarnation-unavailable", resource=self)
        if self.capture_started:
            # A prior fstat was interrupted. Reuse only already-published
            # identities; an absent identity cannot be rebuilt from a number.
            for fd in fds:
                lease = self.leases.get(fd)
                if (lease is None or lease.fd is None or lease.uncertain
                        or lease.identity is None or handles.leases.get(fd) is not lease):
                    raise CleanupIncomplete("wait-receive-capture-ambiguous", resource=self)
            self.captured = True
            return
        self.capture_started = True
        for fd in fds:
            lease = self.leases.get(fd)
            if lease is None:
                lease = handles.leases.get(fd)
                if lease is None:
                    lease = CheckedDescriptor(fd, handles.filesystem)
                self.leases[fd] = lease
            registered = handles.leases.get(fd)
            if registered is not None and registered is not lease:
                raise CleanupIncomplete("wait-receive-lease-replaced", resource=self)
            handles.leases[fd] = lease
            if lease.identity is None:
                lease.capture()
            if lease.fd is None or lease.uncertain or lease.identity is None:
                raise CleanupIncomplete("wait-receive-lease-ambiguous", resource=self)
        self.captured = True

    def original(self, fd, handles):
        if (self.packet is None or tuple(self.leases) != self.packet[1]
            or any(lease.identity is None for lease in self.leases.values())):
            raise CleanupIncomplete("wait-receive-capture-ambiguous", resource=self)
        lease = self.leases[fd]
        registered = handles.leases.get(fd)
        if (registered is not None and registered is not lease
            or lease.fd != fd or lease.uncertain or lease.identity is None):
            raise CleanupIncomplete("wait-receive-lease-ambiguous", resource=self)
        st = lease.filesystem.fstat(fd)
        if (st.st_dev, st.st_ino, getattr(st, "st_mode", 0)) != lease.identity:
            raise CleanupIncomplete("wait-receive-descriptor-replaced", resource=self)
        return lease

    def release(self, handles):
        errors = []
        for fd, lease in self.leases.items():
            try:
                lease.close()
            except BaseException as error:
                errors.append(error)
            finally:
                if lease.fd is None and fd in handles.fds:
                    if handles.leases.get(fd) is not lease:
                        errors.append("wait registry lease was replaced")
                    else:
                        handles.transfer(fd)
                elif lease.fd is None and handles.leases.get(fd) is lease:
                    handles.transfer(fd)
        if errors:
            raise CleanupIncomplete("wait-receive-release", problems=errors, resource=self)


def receive(control, count, handles, retain=None, transaction=None):
    # This write precedes recvmsg. Retirement can therefore distinguish a
    # never-entered receive from an interrupted delivery whose result is
    # ambiguous or whose publication has not completed.
    if transaction is not None:
        transaction.started = True
    data, fds, malformed, flags = handles.packet(control, 131072, (count, retain, transaction))
    journal = handles.pending
    if transaction is not None:
        transaction.raw = journal["raw"][0]
        transaction.capture(data, fds, malformed, flags, handles)
    # The transaction already holds its original leases before registration.
    # Other callers retain raw delivery and saved tokens in the Handles journal.
    handles.register(fds)
    if retain is not None:
        retain(data, list(fds), malformed, flags)
    handles.capture(fds)
    handles.pending = None
    counts = (count,) if isinstance(count, int) else count
    if malformed or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(fds) not in counts:
        raise ValueError("invalid supervision descriptor transfer")
    return decode(data), list(fds)


def send(control, value, fds=()):
    data = json.dumps(dict(value, version=VERSION), separators=(",", ":")).encode()
    if len(data) > 131072:
        raise ValueError("oversized supervision packet")
    ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))] if fds else []
    if control.sendmsg([data], ancillary) != len(data):
        raise RuntimeError("short supervision packet")


def validate_hello(value, login, token, peer_pid, peer_uid, expected_uid):
    if (set(value) != {"version", "op", "instance", "login", "sdk", "source", "env"}
        or value["op"] != "launch" or value["instance"] != token or value["login"] != login
        or type(value["sdk"]) is not int or value["sdk"] != peer_pid or peer_pid <= 1
        or peer_uid != expected_uid or expected_uid <= 0
        or not isinstance(value["source"], str) or not value["source"].startswith("/")
        or "\x00" in value["source"] or not value["source"].endswith("/private_bus.py")
        or not isinstance(value["env"], dict)):
        raise ValueError("unbound supervision launch")
    for key, item in value["env"].items():
        if (not isinstance(key, str) or not key or "=" in key or "\x00" in key
            or not isinstance(item, str) or "\x00" in item):
            raise ValueError("invalid unprivileged owner environment")


class AncestorCleanup:
    """A surviving ancestor owns all waits, including the owner itself.

    It is a process ancestor, not an SDK sibling monitor. Bounded failed
    attempts retain the same registry and boundary. The receipt is generated
    here, never relayed from the user-controlled owner's completion socket.
    """
    def __init__(self, instance, identity, boundary, ops):
        self.instance, self.identity, self.boundary = instance, identity, boundary
        self.cleanup = OwnerCleanup(instance, boundary, ops, drain_boundary=boundary.outer)
        self.receipt = None

    @property
    def primary(self):
        return self.cleanup.primary

    def bind(self, fd):
        if self.primary is not None or self.receipt is not None:
            raise RuntimeError("supervisor primary already bound or cleanup complete")
        self.cleanup.primary = fd

    def attempt(self, timeout=1.5):
        # OwnerCleanup independently tries the primary and the entire tree.
        deadline = self.cleanup.ops.now() + timeout
        value = self.cleanup.attempt(timeout)
        if not self.boundary.empty():
            raise CleanupIncomplete("ancestor-final-boundary", resource=self)
        while not self.cleanup.ops.reaped(self.primary):
            if self.cleanup.ops.now() >= deadline:
                raise CleanupIncomplete("primary-reaping", pending=("primary-wait",), resource=self)
            self.cleanup.ops.sleep(.01)
        self.receipt = dict(value, version=VERSION, supervisor=list(self.identity), source="ancestor-waits",
                            primary_reaped=True, primary_wait_authority="kernel-task-detached")
        return self.receipt


def validate_receipt(value, token, boundary, supervisor, bound):
    required = {"version", "instance", "boundary", "state", "bound", "primary_exited",
                "descendants_empty", "adoption_drained", "pending", "problems", "supervisor", "source", "primary_reaped", "primary_wait_authority", "trigger"}
    if (not isinstance(value, dict) or set(value) != required
        or type(value["version"]) is not int or value["version"] != VERSION
        or value["instance"] != token or value["boundary"] != list(boundary)
        or any(type(n) is not int for n in value["boundary"])
        or value["supervisor"] != list(supervisor) or value["source"] != "ancestor-waits"
        or value["primary_reaped"] is not True or value["primary_wait_authority"] != "kernel-task-detached"
        or value["trigger"] not in ("requested", "primary-exit", "sdk-loss", "owner-loss", "supervisor-stop", "protocol-fault", "bootstrap-fault")
        or value["state"] != "complete" or type(value["bound"]) is not bool or value["bound"] != bound
        or value["pending"] != [] or value["problems"] != []
        or any(value[key] is not True for key in ("primary_exited", "descendants_empty", "adoption_drained"))):
        raise ValueError("missing/unbound ancestor wait certificate")
    return value


class RootManager(SystemdTransport):
    """Authenticate PID 1 directly; a user-manager lifetime is not authority."""
    def _root_identity(self, deadline):
        call = lambda method, args: self._call(self.system, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                                               "org.freedesktop.DBus", method, args, deadline)
        owner = str(call("GetNameOwner", ("org.freedesktop.systemd1",)))
        if (int(call("GetConnectionUnixUser", (owner,))) != 0
            or int(call("GetConnectionUnixProcessID", (owner,))) != 1
            or str(call("GetNameOwner", ("org.freedesktop.systemd1",))) != owner):
            raise PlatformUnsupported("supervision manager is not authenticated root PID 1")
        return owner

    def connect(self, deadline):
        for path in ("/run", "/run/dbus"):
            self._safe_directory(path, 0)
        st = os.stat("/run/dbus/system_bus_socket", follow_symlinks=False)
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != 0:
            raise PlatformUnsupported("unsafe installed system bus endpoint")
        self.system = self.BusConnection("unix:path=/run/dbus/system_bus_socket")
        self.record = self._root_identity(deadline)
        version = str(self._call(self.system, self.record, PATH, "org.freedesktop.DBus.Properties", "Get",
                                 (MANAGER, "Version"), deadline))
        if not re.match(r"(?:25[2-9]|2[6-9][0-9]|[3-9][0-9]{2})(?:\D|$)", version):
            raise PlatformUnsupported("system manager version is below 252")

    def attest(self, deadline):
        # A bus reconnect/reexec can change the unique name. It cannot change
        # the root PID 1 peer, or the caller's pinned service invocation.
        self.record = self._root_identity(deadline)

    def unit(self, name, deadline):
        self.attest(deadline)
        try:
            path = str(self._call(self.system, self.record, PATH, MANAGER, "GetUnit", (name,), deadline))
        except self.dbus.DBusException as error:
            if error.get_dbus_name() == "org.freedesktop.systemd1.NoSuchUnit":
                return None
            raise
        unit = self._properties(self.system, self.record, path, UNIT, deadline)
        service = self._properties(self.system, self.record, path, SERVICE, deadline)
        raw_unit = self._call(self.system, self.record, path, "org.freedesktop.DBus.Properties", "GetAll", (UNIT,), deadline)
        raw = self._call(self.system, self.record, path, "org.freedesktop.DBus.Properties", "GetAll", (SERVICE,), deadline)
        for key in ("User", "Type", "ExitType", "Restart"):
            service[key] = str(raw.get(key, ""))
        unit["FragmentPath"] = str(raw_unit.get("FragmentPath", ""))
        service["ExecStart"] = [(str(row[0]), [str(arg) for arg in row[1]]) for row in raw.get("ExecStart", ())]
        return dict(unit, **service, object_path=path)

    def scope(self, name, deadline):
        return WaitScopeManager.scope(self, name, deadline)

    def start(self, name, deadline):
        # No transient root command, properties, UID, Environment or PIDs API.
        self._call(self.system, self.record, PATH, MANAGER, "StartUnit", (name, "fail"), deadline)

    def reconnect(self, deadline):
        self.close()
        self.connect(deadline)


# Scope units cannot be instantiated from templates. Keep the authenticated
# account/nonce in a plain unit name; '@' makes StartTransientUnit refuse it.
WAIT_PREFIX = "kilix-private-bus-wait-"
SCOPE = "org.freedesktop.systemd1.Scope"


def wait_name(instance):
    decode_instance(instance)
    return WAIT_PREFIX + instance + ".scope"


def wait_identity(record, instance, pid, supervisor, expected=None):
    """Only the manager-derived sibling destruction domain is authority."""
    name = wait_name(instance)
    if (not record or record.get("Id") != name or record.get("Transient") is not True
        or record.get("Delegate") is not True or record.get("KillMode") != "control-group"
        or record.get("SendSIGKILL") is not True or record.get("CollectMode") != "inactive-or-failed"
        or record.get("ActiveState") != "active"
        or record.get("Description") != "Kilix exact wait owner " + instance):
        raise PlatformUnsupported("independent wait scope is incompatible")
    group = record.get("ControlGroup")
    components(group)
    if (not group.endswith("/" + name) or group == supervisor[2]
        or group.startswith(supervisor[2] + "/") or supervisor[2].startswith(group + "/")):
        raise PlatformUnsupported("wait owner shares supervisor destruction domain")
    identity = (invocation(record["InvocationID"]).hex(), pid, group)
    if pid <= 1 or identity[0] == supervisor[0] or expected is not None and identity != expected:
        raise PlatformUnsupported("independent wait invocation changed")
    return identity


class WaitScopeManager(RootManager):
    """Fixed root prelude's SELF placement; never a public PID/command API.

    Public callers retain only the original own-account StartUnit permission.
    A scope has no ExecStart and no Requires/PartOf/BindsTo link to its caller.
    It contains the root wait process, not the physical-session workload.
    """
    def __init__(self, bindings=None):
        self.system = self.user = None
        self.uid = os.getuid()
        if self.uid != 0 or os.geteuid() != 0:
            raise PlatformUnsupported("wait manager is restricted to the fixed root prelude")
        if bindings is None:
            import dbus
            from dbus.bus import BusConnection
            from dbus.connection import Connection
            bindings = (dbus, BusConnection, Connection)
        self.dbus, self.BusConnection, self.Connection = bindings

    def scope(self, name, deadline):
        self.attest(deadline)
        try:
            path = str(self._call(self.system, self.record, PATH, MANAGER, "GetUnit", (name,), deadline))
        except self.dbus.DBusException as error:
            if error.get_dbus_name() == "org.freedesktop.systemd1.NoSuchUnit":
                return None
            raise
        return dict(self._properties(self.system, self.record, path, UNIT, deadline),
                    **self._properties(self.system, self.record, path, SCOPE, deadline))

    def place_self(self, instance, deadline):
        if os.getuid() != 0 or os.geteuid() != 0:
            raise PlatformUnsupported("wait scope creation requires the fixed root prelude")
        self.attest(deadline)
        d = self.dbus
        properties = d.Array([
            ("Description", d.String("Kilix exact wait owner " + instance)),
            ("PIDs", d.Array([d.UInt32(os.getpid())], signature="u")),
            ("Delegate", d.Boolean(True)), ("KillMode", d.String("control-group")),
            ("SendSIGKILL", d.Boolean(True)), ("TimeoutStopUSec", d.UInt64(1500000)),
            ("CollectMode", d.String("inactive-or-failed")),
        ], signature="(sv)")
        return self._call(self.system, self.record, PATH, MANAGER, "StartTransientUnit",
                          (wait_name(instance), "fail", properties, d.Array([], signature="(sa(sv))")), deadline)

    def collect_scope(self, instance, identity, ops, deadline):
        """No stop/reset by name, including replacement and partial retirement."""
        while True:
            try:
                record = self.scope(wait_name(instance), deadline)
            except Exception:
                self.reconnect(deadline)
                record = self.scope(wait_name(instance), deadline)
            if record is None:
                return
            if identity is not None and invocation(record["InvocationID"]).hex() != identity[0]:
                raise CleanupIncomplete("wait-scope-replaced", resource=self)
            if record.get("ActiveState") not in ("inactive", "failed") or record.get("ControlGroup"):
                raise CleanupIncomplete("wait-scope-still-owned", resource=self)
            if ops.now() >= deadline:
                raise CleanupIncomplete("wait-scope-collection-expired", resource=self)
            ops.sleep(min(.01, deadline - ops.now()))


class ServiceProcess:
    """An authenticated manager child; never pretend it is an SDK Popen child."""
    def __init__(self, platform):
        self.platform, self.pid = platform, platform.identity[1]

    def poll(self):
        if not self.platform.ops.ready(self.platform.pidfd):
            return None
        # Exit alone says nothing about adopted children. The separate bound
        # receipt is required by SupervisedCleanup even for a zero return here.
        return 0 if self.platform.receipt is not None else 1


class SupervisedCleanup:
    """SDK close consumes ancestor proof; there is no cgroup-only success path."""
    def __init__(self, instance, boundary, ops, process, owner_fd, transport):
        self.instance, self.boundary, self.ops = instance, boundary, ops
        self.platform = process.platform
        self.primary = self.receipt = self.fault = None
        self.fault_reported = False

    def observe_exit(self):
        if self.receipt is not None:
            return
        try:
            value = self.platform.finish(self.primary, request=False)
            if value["trigger"] in ("owner-loss", "supervisor-stop", "protocol-fault", "bootstrap-fault"):
                self.fault = CleanupIncomplete("ancestor-recovered-fault", problems=(value["trigger"],), resource=self)
            else:
                self.receipt = value
        except Exception as error:
            self.fault = error

    def finish(self, timeout=1.5):
        if (self.platform.primary_process is not None and not self.platform.primary_bound
            and self.platform.primary_committed):
            self.platform.primary_process.terminate()
            self.platform.primary_process.wait(timeout)
        value = self.platform.finish(self.primary, timeout=timeout)
        if not self.fault_reported and value["trigger"] in ("owner-loss", "supervisor-stop", "protocol-fault", "bootstrap-fault"):
            self.fault_reported = True
            self.fault = CleanupIncomplete("ancestor-recovered-fault", problems=(value["trigger"],), resource=self)
            raise self.fault
        self.receipt = value
        self.fault = None
        return self.receipt


class SupervisedPlatform:
    """Retain one root service, exact peer/FDs, and the complete receipt."""
    def __init__(self, token, manager=None, ops=None):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ValueError("invalid private-bus nonce")
        self.token, self.ops = token, ops or LinuxProcesses()
        uid = os.getuid()
        if uid <= 0 or uid != os.geteuid():
            raise PlatformUnsupported("root/setuid GUI bus caller is unsupported")
        self.login = pwd.getpwuid(uid).pw_name
        self.instance = instance_name(self.login, token)
        self.name = PREFIX + self.instance + ".service"
        self.manager = manager or RootManager()
        self.identity = self.boundary = self.pidfd = self.control = self.receipt = None
        self.handles = Handles(self.ops.close, os)
        self.started = self.closed = self.launch_attempted = False
        self._boundary_retired = False
        self.primary_bound = False
        self.primary_requested = False
        self.primary_process = None
        self.primary_pid = None
        self.primary_fd = None
        self.primary_lease = None
        self.primary_handoff_complete = False
        self.primary_handoff = None
        self.primary_request = None
        self.primary_commit_requested = False
        self.primary_committed = False
        self.custodian = None
        self.close_requested = False
        self.bootstrap_packet = None
        self.remote_boundary_identity = None
        self.wait_owner = self.wait_pidfd = self.wait_handoff = None
        self.pending_wait_packet = None
        self.pending_wait_collected = False
        self.wait_receive_required = False
        self.wait_receive = None

    def _retain_bootstrap(self, data, fds, malformed, flags):
        self.bootstrap_packet = (data, tuple(fds), malformed, flags)

    def _retain_wait_packet(self, data, fds, malformed, flags):
        # Pending input retains cleanup responsibility, not scope authority.
        self.pending_wait_packet = (data, tuple(fds), malformed, flags)

    def _pending_wait_parts(self):
        transaction = getattr(self, "wait_receive", None)
        packet = self.pending_wait_packet
        if packet is None and transaction is not None:
            packet = transaction.packet
            if packet is None:
                # recvmsg may have returned SCM_RIGHTS before cancellation hid
                # receive()'s transaction capture. Reconcile only the exact
                # retained Handles journal for this transaction; never recover
                # through an FD number or a later receive.
                journal = self.handles.pending
                contract = None if journal is None else journal.get("count")
                if (journal is None or journal.get("kind") != "receive"
                        or not isinstance(contract, tuple) or len(contract) != 3
                        or contract[2] is not transaction):
                    raise CleanupIncomplete("wait-receive-unresolved", resource=self)
                packet = self.handles.publish_pending(register=False)
                if packet is None:
                    raise CleanupIncomplete("wait-receive-packet-unpublished", resource=self)
                transaction.raw = journal["raw"][0]
                transaction.capture(*packet, self.handles)
            elif not transaction.captured:
                # Capture can itself be interrupted after the exact packet and
                # lease were saved. Retry that same lease, never a new receive.
                transaction.capture(*packet, self.handles)
        if packet is None:
            raise CleanupIncomplete("wait-receive-unresolved", resource=self)
        data, fds, malformed, flags = packet
        if malformed or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(fds) != 1:
            raise ValueError("invalid retained wait-owner descriptor packet")
        if transaction is not None:
            if transaction.packet is None or fds != transaction.packet[1]:
                raise CleanupIncomplete("wait-receive-packet-mismatch", resource=self)
            lease = transaction.original(fds[0], self.handles)
        else:
            lease = self.handles.leases[fds[0]]
        if lease.fd != fds[0] or lease.uncertain:
            raise CleanupIncomplete("wait-owner-descriptor-incarnation", resource=self)
        if lease.identity is None:
            lease.capture()
        value = decode(data)
        if (set(value) != {"version", "op", "instance", "supervisor", "wait_owner"}
            or value["op"] != "wait-owner" or value["instance"] != self.token
            or value["supervisor"] != list(self.identity)
            or not isinstance(value["wait_owner"], list) or len(value["wait_owner"]) != 3):
            raise ValueError("missing authenticated independent wait owner")
        owner = tuple(value["wait_owner"])
        if type(owner[1]) is not int or owner[1] <= 1:
            raise ValueError("invalid pending wait process identity")
        return owner, fds[0], lease

    def _authenticate_wait_packet(self, deadline):
        if self.wait_handoff is not None:
            return self.wait_handoff
        owner, fd, lease = self._pending_wait_parts()
        if self.ops.pidfd_pid(fd) != owner[1] or self.ops.ready(fd):
            raise ValueError("independent owner pidfd is not its exact live process")
        record = self.manager.scope(wait_name(self.instance), deadline)
        wait_identity(record, self.instance, owner[1], self.identity, owner)
        self.wait_handoff = (owner, fd, lease)
        return self.wait_handoff

    def _collect_pending_wait(self, deadline):
        if self.pending_wait_collected:
            return
        if self.wait_handoff is None:
            owner, fd, lease = self._pending_wait_parts()
            pid = self.ops.pidfd_pid(fd)
            if pid not in (owner[1], -1):
                raise CleanupIncomplete("pending-wait-process-mismatch", resource=self)
            if self.ops.ready(fd):
                # The authenticated controller delivered this original pidfd,
                # but scope ownership may never have been authenticated. Exact
                # collection plus an absent manager scope discharges cleanup;
                # it does not publish wait_owner or authorize any named action.
                if not self.ops.reaped(fd):
                    raise CleanupIncomplete("pending-wait-owner-uncollected", resource=self)
                if self.manager.scope(wait_name(self.instance), deadline) is not None:
                    raise CleanupIncomplete("pending-wait-scope-not-absent", resource=self)
                self.pending_wait_collected = True
                return
        owner, fd, lease = self._authenticate_wait_packet(deadline)
        transaction = getattr(self, "wait_receive", None)
        if transaction is not None:
            transaction.original(fd, self.handles)
        if self.ops.pidfd_pid(fd) not in (owner[1], -1):
            raise CleanupIncomplete("authenticated-wait-process-mismatch", resource=self)
        if lease.fd != fd or lease.uncertain or not self.ops.ready(fd, max(0, deadline - self.ops.now())):
            raise CleanupIncomplete("pending-wait-owner-exit", resource=self)
        if not self.ops.reaped(fd):
            raise CleanupIncomplete("authenticated-wait-owner-uncollected", resource=self)
        WaitScopeManager.collect_scope(self.manager, self.instance, owner, self.ops, deadline)
        self.pending_wait_collected = True

    def _retire_wait_handoff(self):
        transaction = getattr(self, "wait_receive", None)
        if transaction is not None:
            try:
                transaction.release(self.handles)
            finally:
                if all(lease.fd is None for lease in transaction.leases.values()):
                    self.wait_pidfd = self.wait_handoff = None
            return
        record = getattr(self, "wait_handoff", None)
        if record is None:
            return
        owner, fd, lease = record
        try:
            lease.close()
        finally:
            if lease.fd is None:
                if fd in self.handles.fds:
                    self.handles.transfer(fd)
                self.wait_pidfd = self.wait_handoff = None

    def _certificate(self, value):
        owner = getattr(self, "wait_owner", None)
        if owner is not None:
            if (not isinstance(value, dict)
                or set(value) != {"version", "op", "instance", "wait_owner", "certificate"}
                or value.get("version") != VERSION or value.get("op") != "wait-certificate"
                or value.get("instance") != self.token or value.get("wait_owner") != list(owner)):
                raise ValueError("certificate lacks the authenticated independent owner")
            return value["certificate"]
        # Adapter-level legacy packet tests have no installed bootstrap. The
        # production prepare path always establishes wait_owner first.
        return value

    def _complete_bootstrap(self):
        if self.bootstrap_packet is None:
            receive(self.control, (0, 8), self.handles, self._retain_bootstrap)
        data, received, malformed, flags = self.bootstrap_packet
        if malformed or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            raise ValueError("invalid retained supervisor bootstrap packet")
        value = self._certificate(decode(data)) if not received else decode(data)
        if not received and value == {"version": VERSION, "op": "unlaunched", "instance": self.token,
                                      "supervisor": list(self.identity), "no_descendant_birth": True,
                                      "adoption_drained": True} and value["no_descendant_birth"] is True and value["adoption_drained"] is True and not self.primary_bound:
            self.receipt = value
            return False
        if not received and value.get("source") == "ancestor-waits" and not self.primary_bound:
            identity = value.get("boundary")
            if not isinstance(identity, list) or len(identity) != 4 or any(type(n) is not int or n < 0 for n in identity):
                raise ValueError("invalid root-certified bootstrap boundary")
            # Owner birth can fail before the eight-FD response. The SAME
            # authenticated root ancestor still owns those pinned identities,
            # all waits and retirement; its certificate cannot come from UID code.
            self.receipt = validate_receipt(value, self.token, identity, self.identity, False)
            self.remote_boundary_identity = tuple(identity)
            return False
        if (len(received) != 8 or set(value) != {"version", "op", "instance", "supervisor", "boundary"}
            or value["op"] != "boundary" or value["instance"] != self.token
            or value["supervisor"] != list(self.identity)):
            raise ValueError("unbound supervisor boundary response")
        if self.boundary is None:
            boundary = InstanceBoundary(CgroupBoundary(*received[:4]), CgroupBoundary(*received[4:]))
            if value["boundary"] != list(boundary.identity) or any(type(n) is not int for n in value["boundary"]):
                raise ValueError("supervisor boundary identities do not match retained FDs")
            self.boundary = boundary
        self.handles.transfer_many([fd for fd in received if fd in self.handles.fds])
        return True

    def set_custodian(self, owner):
        if self.custodian is not None or self.launch_attempted:
            raise RuntimeError("supervisor descriptor custodian already registered")
        self.custodian = owner

    def claim_main(self, owner):
        if owner is not self.custodian or owner.owner_pidfd != self.pidfd:
            raise RuntimeError("supervisor main descriptor has no registered recipient")
        if self.pidfd in self.handles.fds:
            self.main_lease = self.handles.leases[self.pidfd]
            owner._owner_pidfd_lease = self.main_lease
            self.handles.transfer(self.pidfd)

    def _service(self, record):
        argv = ["/usr/bin/python3", "-I", installed_program(Path(__file__).parent), self.instance]
        if (not record or record.get("Id") != self.name or record.get("FragmentPath") != FRAGMENT
            or record.get("User") != "root" or record.get("Type") != "exec"
            or record.get("ExitType") != "main" or record.get("Restart") != "no"
            or record.get("Delegate") is not True or record.get("KillMode") != "control-group"
            or record.get("CollectMode") != "inactive-or-failed"
            or record.get("SendSIGKILL") is not True or record.get("ExecStart") != [("/usr/bin/python3", argv)]):
            raise PlatformUnsupported("installed private-bus supervisor unit is incompatible or unsafe", self)
        identity = (invocation(record["InvocationID"]).hex(), int(record["MainPID"]), record["ControlGroup"])
        components(identity[2])
        if (identity[1] <= 1 or record.get("ActiveState") != "active"
            or not identity[2].endswith("/" + self.name)):
            raise PlatformUnsupported("private-bus supervisor has no live exact main process", self)
        if self.identity is not None and identity != self.identity:
            raise PlatformUnsupported("private-bus supervisor invocation was replaced", self)
        return identity

    def _installed(self):
        # Root-owned immutable delivery; caller checkout code is never a root
        # ExecStart. Versions must agree before we grant any launch capability.
        directory = str(Path(installed_program(Path(__file__).parent)).parent)
        for path in ("/usr", "/usr/libexec", PROGRAM_ROOT, directory, "/etc", "/etc/systemd", "/etc/systemd/system"):
            self.manager._safe_directory(path, 0)
        for name in MODULES:
            path = Path(directory) / name
            st = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
                raise PlatformUnsupported("unsafe installed supervisor module: " + name, self)
            if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(Path(__file__).with_name(name).read_bytes()).digest():
                raise PlatformUnsupported("installed supervisor source version differs; run the normal installer", self)
        st = os.stat(FRAGMENT, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise PlatformUnsupported("unsafe installed supervisor template", self)

    def prepare(self, timeout=6):
        deadline = self.ops.now() + timeout
        try:
            self._installed()
            self.manager.connect(deadline)
            if self.manager.unit(self.name, deadline) is not None:
                raise PlatformUnsupported("fresh supervisor service already exists", self)
            # Starting the fixed controller may already create its wait child.
            # Publish responsibility before that effect, not at a later receive.
            self.wait_receive_required = True
            self.started = True  # An ambiguous StartUnit reply still owns cleanup.
            self.manager.start(self.name, deadline)
            while True:
                remaining(deadline)
                record = self.manager.unit(self.name, deadline)
                if record and record.get("ActiveState") == "active":
                    self.identity = self._service(record)
                    break
                if record and record.get("ActiveState") in ("inactive", "failed"):
                    raise PlatformUnsupported("supervisor startup failed", self)
                self.ops.sleep(.01)
            self.pidfd = self.handles.allocate(os.pidfd_open, self.identity[1])
            root = RUNTIME + "/" + self.instance
            for path in ("/run", RUNTIME, root):
                self.manager._safe_directory(path, 0)
            self.control = self.handles.allocate_objects(socket.socket, socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
            self.handles.transfer_object(self.control)
            self.control.settimeout(max(.001, deadline - self.ops.now()))
            while True:
                try:
                    self.control.connect(root + "/control")
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    remaining(deadline)
                    if self.ops.ready(self.pidfd):
                        raise RuntimeError("supervisor exited before authenticated connection")
                    self.ops.sleep(.01)
            pid, uid, _ = struct.unpack("3i", self.control.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != 0 or pid != self.identity[1] or self.ops.ready(self.pidfd):
                raise PlatformUnsupported("private-bus supervisor peer is not its live root main process", self)
            self._service(self.manager.unit(self.name, deadline))
            # This handshake is mandatory before any SDK launch request. The
            # authenticated root endpoint belongs to the fixed controller and
            # is transferred only to its already placed fixed wait-owner child.
            self.wait_receive = WaitReceive()
            value, fds = receive(self.control, 1, self.handles, self._retain_wait_packet,
                                 transaction=self.wait_receive)
            owner, fd, lease = self._authenticate_wait_packet(deadline)
            self.wait_owner, self.wait_pidfd = owner, fds[0]
            self.handles.transfer(fds[0])
        except BaseException as error:
            if isinstance(error, Exception):
                raise PlatformUnsupported(error, self) from error
            error.resource = self
            raise

    def launch_owner(self, supervisor, env, parent_fd, child, info, finish, bootstrap, source):
        if self.launch_attempted or self.identity is None:
            raise RuntimeError("supervision bootstrap unavailable or already used")
        self.launch_attempted = True
        fds = (parent_fd, child.fileno(), info.fileno(), finish.fileno(), bootstrap.fileno())
        send(self.control, {"op": "launch", "instance": self.token, "login": self.login,
                            "sdk": os.getpid(), "source": source, "env": env}, fds)
        if not self._complete_bootstrap():
            raise RuntimeError("independent supervisor retired a failed bootstrap")
        # Main pidfd remains here until the registered PrivateBus has actually
        # assigned it. Cancellation during return/unpack cannot lose its owner.
        # The SDK watches this independent ancestor before opening the owner's
        # bootstrap gate. The root server owns the actual fork child.
        return ServiceProcess(self), self.pidfd, self.boundary

    def cleanup(self, *args):
        return SupervisedCleanup(*args)

    def bind_primary(self, fd):
        raise RuntimeError("caller-born primary handoff is disabled; use launch_primary")

    def launch_primary(self, argv, env, cwd, stream_fds, request):
        if (not self.launch_attempted or self.boundary is None or self.receipt is not None
            or self.control is None or not isinstance(request, str)):
            raise RuntimeError("independent primary launch is unavailable")
        payload = {"op": "primary-launch", "instance": self.token, "request": request,
                   "argv": list(argv), "env": dict(env), "cwd": cwd}
        if self.primary_request is not None and self.primary_request != payload:
            raise RuntimeError("primary retry changed its retained launch request")
        self.primary_request = payload
        self.primary_requested = True  # Before send: cancellation may follow kernel delivery.
        handoff = getattr(self, "primary_handoff", None)
        if handoff is not None:
            self.primary_pid, self.primary_fd, self.primary_lease = handoff
        if self.primary_fd is None:
            send(self.control, payload, stream_fds)
            value, received = receive(self.control, 1, self.handles)
            if (set(value) != {"version", "op", "instance", "request", "pid"}
                or value["op"] != "primary" or value["instance"] != self.token
                or value["request"] != request or type(value["pid"]) is not int or value["pid"] <= 1):
                raise ValueError("unbound primary birth response")
            fd = received[0]
            target = self.ops.pidfd_pid(fd)
            if (target not in (value["pid"], -1)
                or target == -1 and not self.ops.ready(fd)):
                raise ValueError("primary pidfd does not match ancestor birth")
            # Retain the recipient and captured incarnation BEFORE registry
            # removal. Cancellation may leave overlap, never an ownership gap.
            self.primary_handoff = (value["pid"], fd, self.handles.leases.get(fd))
            self.primary_lease = self.handles.leases.get(fd)
            self.primary_fd, self.primary_pid = fd, value["pid"]
            self.handles.transfer(fd)
        if self.primary_fd in self.handles.fds:
            self.handles.transfer(self.primary_fd)
        self.primary_handoff_complete = True
        return self.primary_pid, self.primary_fd

    def _retire_primary_handoff(self):
        if getattr(self, "primary_handoff_complete", False):
            return  # PrivateBus/RemoteProcess owns the published duplicate.
        lease = getattr(self, "primary_lease", None)
        fd = getattr(self, "primary_fd", None)
        handoff = getattr(self, "primary_handoff", None)
        if handoff is not None:
            _, fd, lease = handoff
        if fd is None and lease is not None:
            fd = lease.fd
        if fd is None:
            return
        if lease is not None:
            try:
                lease.close()
            finally:
                if lease.fd is None:
                    if fd in self.handles.fds:
                        self.handles.transfer(fd)
                    self.primary_fd = self.primary_lease = None
                    self.primary_handoff = None
        elif fd in self.handles.fds:
            # Fully typed inert registries have no native filesystem lease.
            try:
                self.handles.release_one(fd)
            finally:
                if fd not in self.handles.fds:
                    self.primary_fd = None
                    self.primary_handoff = None
        else:
            def released():
                self.primary_fd = self.primary_handoff = None
            raise CleanupIncomplete("primary-handoff-lease-missing", resource=self)

    def commit_primary(self, request):
        if self.primary_committed:
            if request != self.primary_request["request"]:
                raise RuntimeError("primary commit request changed")
            return
        if self.primary_fd is None or self.primary_request is None or request != self.primary_request["request"]:
            raise RuntimeError("primary commit has no retained birth")
        self.primary_commit_requested = True
        send(self.control, {"op": "primary-commit", "instance": self.token, "request": request})
        value, _ = receive(self.control, 0, self.handles)
        if (value != {"version": VERSION, "op": "primary-committed", "instance": self.token,
                      "request": request, "pid": self.primary_pid}):
            raise ValueError("independent ancestor did not commit the exact primary")
        self.primary_committed = self.primary_bound = True

    def request_close(self):
        if not self.close_requested and self.receipt is None and self.control is not None:
            try:
                send(self.control, {"op": "close", "instance": self.token})
            except (BrokenPipeError, ConnectionResetError):
                pass  # The final receipt may already be queued.
            self.close_requested = True

    def finish(self, primary=None, timeout=1.5, request=True):
        deadline = self.ops.now() + timeout
        if primary is None:
            primary = self.primary_fd
        if primary is None and self.primary_process is not None:
            primary = self.primary_process.fd
        if self.control is not None:
            self.control.settimeout(max(.001, deadline - self.ops.now()))
        if self.boundary is None and self.launch_attempted and self.receipt is None:
            if request:
                self.request_close()
            self._complete_bootstrap()
        if self.receipt is None and self.control is not None:
            self.control.settimeout(max(.001, deadline - self.ops.now()))
            try:
                if request:
                    self.request_close()
                while self.receipt is None:
                    if self.primary_process is not None:
                        self.primary_process.poll()  # Exact P_PIDFD wait; keeps the primary's physical parent.
                    if not select.select([self.control], [], [], min(.05, max(0, deadline - self.ops.now())))[0]:
                        remaining(deadline)
                        continue
                    value, _ = receive(self.control, 0, self.handles)
                    if value == {"version": VERSION, "op": "primary", "instance": self.token} and self.primary_requested:
                        self.primary_bound = True  # Delayed ACK; no exec gate opens in cleanup.
                        remaining(deadline)
                        continue
                    if value.get("op") == "pending":
                        if value.get("instance") != self.token:
                            raise ValueError("foreign supervisor pending response")
                        remaining(deadline)
                        continue
                    value = self._certificate(value)
                    expected_bound = self.primary_bound
                    if self.primary_requested and value.get("bound") is True:
                        expected_bound = True  # Ancestor proof can resolve a lost launch/FD ACK.
                    self.receipt = validate_receipt(value, self.token, self.boundary.identity,
                                                    self.identity, expected_bound)
                    self.primary_bound = expected_bound
            except Exception as error:
                # Culling prevents live leaks after catastrophic supervisor
                # loss; it can NEVER manufacture its missing wait certificate.
                failures = [error]
                if self.ops.ready(self.pidfd):
                    if self.boundary is not None:
                        try: self.boundary.kill()
                        except Exception as kill_error: failures.append(kill_error)
                    try: stop_exact(primary, self.ops, deadline)
                    except Exception as primary_error: failures.append(primary_error)
                raise CleanupIncomplete("ancestor-certificate", problems=failures, resource=self) from error
        if self.receipt is None:
            failures = ["independent ancestor wait certificate is unavailable"]
            if self.ops.ready(self.pidfd):
                if self.boundary is not None:
                    try: self.boundary.kill()
                    except Exception as error: failures.append(error)
                try: stop_exact(primary, self.ops, deadline)
                except Exception as error: failures.append(error)
            raise CleanupIncomplete("ancestor-certificate", problems=failures, resource=self)
        if not self.ops.ready(self.pidfd, max(0, deadline - self.ops.now())):
            raise CleanupIncomplete("supervisor-exit", pending=("supervisor",), resource=self)
        wait_fd = getattr(self, "wait_pidfd", None)
        if wait_fd is not None and not self.ops.ready(wait_fd, max(0, deadline - self.ops.now())):
            raise CleanupIncomplete("independent-wait-owner-exit", resource=self)
        if self.primary_process is not None:
            self.primary_process.poll()
        if not self.ops.ready(primary) or not self.ops.reaped(primary) or self.boundary is not None and not self.boundary.empty():
            raise CleanupIncomplete("supervisor-live-exit-proof", resource=self)
        return self.receipt

    def retire(self, timeout=1.5):
        if self.closed:
            return
        deadline = self.ops.now() + timeout
        if self.launch_attempted and self.receipt is None and self.boundary is None:
            # A launch was sent: root may already have created descendants.
            # Retry the retained response/proof, never infer completion from
            # root exit or a lost/malformed eight-FD response.
            self.finish(timeout=timeout)
        if self.boundary is not None:
            # Cancellation can interrupt the initial handoff. Reconcile ALL
            # aliases before either owning object may close a descriptor.
            overlap = [fd for fd in self.boundary.fds() if fd in self.handles.fds]
            if overlap:
                self.handles.transfer_many(overlap)
            if self.receipt is None:
                self.finish(self.primary_fd, timeout=timeout)
        if self.custodian is not None and self.custodian.owner_pidfd == self.pidfd:
            self.claim_main(self.custodian)
        # A service launched without a launch request has no children and
        # exits on connection EOF/startup expiry. It is never stopped by name.
        if self.boundary is None and not self._boundary_retired and not self.launch_attempted:
            if self.control is not None:
                self.control.close()
                self.control = None
            if self.pidfd is not None and not self.ops.ready(self.pidfd, max(0, deadline - self.ops.now())):
                raise CleanupIncomplete("supervisor-bootstrap-exit", resource=self)
            if self.started and self.pidfd is None:
                record = self.manager.unit(self.name, deadline)
                if record and record.get("ActiveState") not in ("inactive", "failed"):
                    raise CleanupIncomplete("ambiguous-supervisor-bootstrap", resource=self)
        elif self.receipt is None or not self.ops.ready(self.pidfd) or self.boundary is not None and not self.boundary.empty():
            raise CleanupIncomplete("supervisor-retirement-proof", resource=self)
        errors = []
        try:
            record = None
            if self.started:
                try:
                    record = self.manager.unit(self.name, deadline)
                except Exception:
                    self.manager.reconnect(deadline)
                    record = self.manager.unit(self.name, deadline)
            if record:
                if self.identity is not None and invocation(record["InvocationID"]).hex() != self.identity[0]:
                    raise RuntimeError("supervisor service invocation was replaced")
                if record.get("ActiveState") not in ("inactive", "failed") or record.get("ControlGroup"):
                    raise RuntimeError("supervisor service still owns a cgroup")
                while record and record.get("ActiveState") == "failed":
                    # CollectMode permits GC; an empty failed record is not
                    # yet retirement. Keep exact invocation/handles until it
                    # disappears, without granting reset-failed authority.
                    if self.ops.now() >= deadline:
                        raise CleanupIncomplete("failed-supervisor-collection",
                                                pending=(self.name,), resource=self)
                    self.ops.sleep(min(.01, deadline - self.ops.now()))
                    record = self.manager.unit(self.name, deadline)
                    if record:
                        if self.identity is not None and invocation(record["InvocationID"]).hex() != self.identity[0]:
                            raise RuntimeError("failed supervisor invocation was replaced")
                        if record.get("ActiveState") != "failed" or record.get("ControlGroup"):
                            raise RuntimeError("failed supervisor collection changed its owned state")
        except Exception as error:
            errors.append(error)
        transaction = getattr(self, "wait_receive", None)
        if ((transaction is not None and transaction.started)
            or getattr(self, "wait_receive_required", False)
            or getattr(self, "pending_wait_packet", None) is not None):
            try:
                self._collect_pending_wait(deadline)
            except BaseException as error:
                errors.append(error)
        if getattr(self, "wait_owner", None) is not None:
            try:
                WaitScopeManager.collect_scope(self.manager, self.instance, self.wait_owner, self.ops, deadline)
            except Exception as error:
                errors.append(error)
        if self.boundary is not None:
            try:
                self.boundary.retire()
                self.boundary = None
                self._boundary_retired = True
            except Exception as error:
                errors.append(error)
        if errors:
            raise CleanupIncomplete("supervisor-retirement", problems=errors, resource=self)
        if self.control is not None:
            try:
                self.control.close()
                self.control = None
            except Exception as error:
                errors.append(error)
        # PrivateBus owns the main/watch pidfds. Keep its exact original FD
        # there until its independent release succeeds (R1/R2).
        try: self._retire_wait_handoff()
        except BaseException as error: errors.append(error)
        if errors:
            # Never let bulk registry close bypass a cancelled wait lease.
            raise CleanupIncomplete("wait-handoff-release", problems=errors, resource=self)
        try: self._retire_primary_handoff()
        except BaseException as error: errors.append(error)
        try: self.handles.close()
        except Exception as error: errors.append(error)
        if self.custodian is None or self.custodian.owner_pidfd != self.pidfd:
            lease = getattr(self, "main_lease", None)
            if lease is not None:
                try: lease.close()
                except BaseException as error: errors.append(error)
            if self.pidfd not in self.handles.fds:
                if lease is None or lease.fd is None:
                    self.pidfd = None
        try: self.manager.close()
        except Exception as error: errors.append(error)
        if errors:
            raise CleanupIncomplete("supervisor-transport-release", problems=errors, resource=self)
        self.closed = True
