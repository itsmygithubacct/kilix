#!/usr/bin/env python3
"""kilix-fingerlock-agent: root custodian for one bounded locker operation.

fprintd answers "match" or "no match"; it never yields key material. The
agent keeps the master private key in memory after the owner deposits it
with the passphrase. It does not export the master private key. A caller
receives one bounded operation after a touch bound to that operation: the
custodian decrypts the named container and returns that plaintext. Forget
and expiry refuse a later operation. Plaintext already returned stays
exposed; the custodian cannot take it back.

The touch is bound to the operation binding (kind, name, and the sha256 of
the container bytes) shown in a fresh polkit approval before verification and checked before the
plaintext is released. A passphrase rewrap does not rotate the key and does
not revoke plaintext already returned. Rewrap is not a substitute for forget.

Callers are identified by SO_PEERCRED. Deposit, confirm and operate require
an active, seated, unlocked logind session owned by that uid. The session
comes from logind GetSessionByPID and its live D-Bus properties, including
LockedHint. Missing properties or an unavailable logind fail closed. ACTIVE
alone is not enough. Status and forget do not require a seat; forget from any
local peer of that uid drops custody.

The client checks the connected peer uid before it sends a key. The trusted
uid is 0. Passing another uid is the explicit test interface for a non-root
server. A fake unprivileged socket receives no key material.

Secret memory. The custody buffer is a mutable bytearray. Forget, expiry
and exit overwrite it, and mlock is attempted on that buffer. This cannot
guarantee erasure of every copy: the deposit hex, the JSON parser, libsodium
and the interpreter can retain copies, and a Python assignment is not
evidence of erasure. mlock, prctl and RLIMIT_CORE failures are reported in
status and are not treated as success. Swap and hibernation are not a
secrecy boundary when mlock fails, and are not claimed as one when it
succeeds, because other copies are not locked. Core dumps are disabled when
the kernel accepts the setting. The custody buffer is wiped before suspend,
hibernate or shutdown proceeds (SleepGuard): logind waits on a delay inhibitor
until the wipe is done.

Each user has a mode-0600 socket owned by that user and activated by systemd.
The service still runs as root, authenticates peers, and checks the configured uid. Admission is bounded
by MAX_CONCURRENT connections and MAX_PER_UID requests per uid. Each request
has one monotonic deadline; a slow sender cannot extend it by dribbling
bytes. A full agent refuses new work with EBUSY and keeps serving after a
slot frees. Disconnect before the plaintext is released cancels the operation.

Protocol: one JSON object per line, then, for operate, exactly body_bytes of
container and, on success, exactly plain_bytes of plaintext. The JSON
response does not carry the master key.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
import pwd
import select
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fingerlock_core import (  # noqa: E402
    ANY_FINGER, AccessDenied, DependencyUnavailable, DeviceNotFound, FingerlockError,
    MAX_VERIFY_ATTEMPTS, FprintdClient, NoMatch, OperationFailed, OperationTimeout, ServiceFailure,
)
from fingerlock_locker import Locker, LockerError, SEED_BYTES, unlock_bytes, wipe_buffer  # noqa: E402
from fingerlock_policy import APPROVAL_TIMEOUT, authorize_operation  # noqa: E402

def socket_path(uid: int) -> str:
    return f"/run/kilix-fingerlock/{uid}.sock"


SOCKET_PATH = socket_path(os.getuid())
PROTOCOL = 1
DEFAULT_TTL_SECONDS = 12 * 3600
MAX_TTL_SECONDS = 7 * 24 * 3600
MIN_TTL_SECONDS = 60.0
VERIFY_TIMEOUT_SECONDS = 45.0
# Scans one approved operation may arm: a no-match re-arms the reader within
# the same claim and deadline, as pam_fprintd does, instead of ending the
# operation on the first rejected touch.
VERIFY_ATTEMPTS = MAX_VERIFY_ATTEMPTS
# Session transitions. The monitor polls the depositing session's logind state
# while a key is held, and logind's Lock signal forgets at once.
SESSION_CHECK_SECONDS = 1.0
# One logind lookup (GetSession and GetAll together) may take this long before
# the session counts as unknown, so a wedged logind still forgets within a tick.
LOGIND_TIMEOUT_SECONDS = 2.0
# Sleep. A logind delay inhibitor makes suspend, hibernate and shutdown wait
# while PrepareForSleep/PrepareForShutdown forgets the key; then it is released.
# The backstop: CLOCK_BOOTTIME advances past CLOCK_MONOTONIC only while the
# system sleeps, so any growth beyond measurement noise also forgets.
SUSPEND_SLACK_SECONDS = 0.01
# Awake time after a sleep announcement with no resume signal before the
# guard re-arms on its own (the signal was lost or the transition aborted).
SLEEP_SIGNAL_GRACE_SECONDS = 30.0
# The monitor must tick within this long or custody is refused as unwatched.
MONITOR_STALL_SECONDS = 15.0
# How often the monitor retries a logind watch that could not start.
WATCH_RETRY_SECONDS = 30.0
LOGIND = "org.freedesktop.login1"
LOGIND_PATH = "/org/freedesktop/login1"
LOGIND_MANAGER = "org.freedesktop.login1.Manager"
LOGIND_SESSION = "org.freedesktop.login1.Session"
MAX_LINE = 64 * 1024
MAX_BODY = 64 * 1024 * 1024 + 2 * 1024 * 1024
MAX_CONCURRENT = 8
MAX_PER_UID = 2
REQUEST_DEADLINE_SECONDS = 5.0
REPLY_DEADLINE_SECONDS = 5.0
BODY_DEADLINE_SECONDS = 30.0



class AgentUnavailable(DependencyUnavailable):
    """The agent is not installed or not answering; the passphrase path still works."""


class Busy(ServiceFailure):
    code, exit_status = "EBUSY", 6


class SessionGone(AccessDenied):
    """logind no longer knows the session."""


@dataclass
class HardenReport:
    dumpable_cleared: bool = False
    core_limited: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self, *, memory_locked: bool, extra: list[str] | None = None) -> dict[str, Any]:
        errors = list(self.errors)
        if extra:
            errors.extend(extra)
        return {"dumpable_cleared": self.dumpable_cleared, "core_limited": self.core_limited,
                "memory_locked": memory_locked, "errors": errors}


class SecretBuffer:
    """One mutable custody buffer. Wiping it is not a claim about other copies."""

    def __init__(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray)) or len(data) != SEED_BYTES:
            raise ValueError("custody secret must be 32 bytes")
        self._buf = bytearray(data)
        self.locked = False
        self.error = ""
        self._lock_memory()

    def _address(self) -> int:
        return ctypes.addressof((ctypes.c_char * len(self._buf)).from_buffer(self._buf))

    def _lock_memory(self) -> None:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            rc = libc.mlock(ctypes.c_void_p(self._address()), ctypes.c_size_t(len(self._buf)))
            if rc != 0:
                self.error = f"mlock errno {ctypes.get_errno()}"
            else:
                self.locked = True
        except (OSError, AttributeError) as exc:
            self.error = f"mlock unavailable ({exc.__class__.__name__})"

    def copy(self) -> bytearray:
        return bytearray(self._buf)

    def wipe(self) -> None:
        wipe_buffer(self._buf)
        if self.locked:
            try:
                libc = ctypes.CDLL(None, use_errno=True)
                libc.munlock(ctypes.c_void_p(self._address()), ctypes.c_size_t(len(self._buf)))
            except (OSError, AttributeError):
                pass
            self.locked = False


@dataclass
class Entry:
    secret: SecretBuffer
    public_key: bytes
    locker_id: str
    deposited: float
    expires: float
    username: str
    generation: int
    session_id: str = ""
    sleep_mark: float = 0.0


def _custody_time() -> float:
    """Count elapsed time across suspend without following wall-clock changes."""
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def _sleep_offset() -> float:
    """Total time this boot has spent suspended or hibernated so far.

    The two clocks tick together while the system is awake, so the difference
    changes only across a sleep. BOOTTIME is read between two MONOTONIC reads
    and the narrowest of three brackets is used, which keeps scheduling noise
    to microseconds rather than allowing it to hide a short sleep."""
    best: tuple[float, float] | None = None
    for _ in range(3):
        before = time.clock_gettime(time.CLOCK_MONOTONIC)
        boot = time.clock_gettime(time.CLOCK_BOOTTIME)
        after = time.clock_gettime(time.CLOCK_MONOTONIC)
        if best is None or after - before < best[0]:
            best = (after - before, boot - (before + after) / 2)
    return best[1]


class Custody:
    """In-memory per-uid keys. Expiry and the suspend check run on a timer and
    again on every use, so a request racing the timer after resume is refused.
    Nothing is persisted: a restart or reboot starts empty."""

    def __init__(self, now: Callable[[], float] = _custody_time, *, min_ttl: float = MIN_TTL_SECONDS,
                 start_timer: bool = False, sweep_interval: float = 1.0,
                 sleep_offset: Callable[[], float] = _sleep_offset,
                 suspend_slack: float = SUSPEND_SLACK_SECONDS) -> None:
        self._entries: dict[int, Entry] = {}
        self._generation: dict[int, int] = {}
        self._reasons: dict[int, str] = {}
        self._sleep_offset = sleep_offset
        self._suspend_slack = suspend_slack
        self._lock = threading.Lock()
        self._now = now
        self._min_ttl = min_ttl
        self._stop = threading.Event()
        self._timer: threading.Thread | None = None
        self.sweep_interval = sweep_interval
        if start_timer:
            self.start()

    def start(self) -> None:
        if self._timer is not None:
            return
        self._timer = threading.Thread(target=self._timer_loop, name="fingerlock-expiry", daemon=True)
        self._timer.start()

    def close(self) -> None:
        self._stop.set()
        self.forget_all("shutdown")

    def _timer_loop(self) -> None:
        while not self._stop.wait(self.sweep_interval):
            self.sweep()

    def sweep(self) -> None:
        with self._lock:
            self._expire_locked()

    def _expire_locked(self) -> None:
        now = self._now()
        slept = self._sleep_offset()
        for uid, entry in list(self._entries.items()):
            if slept - entry.sleep_mark > self._suspend_slack:
                self._wipe_locked(uid, "suspend")
            elif entry.expires <= now:
                self._wipe_locked(uid, "expiry")

    def _wipe_locked(self, uid: int, reason: str = "forget") -> None:
        entry = self._entries.pop(uid, None)
        if entry is not None:
            entry.secret.wipe()
            self._reasons[uid] = reason

    def last_reason(self, uid: int) -> str | None:
        """Why the last key held for uid was dropped, for status only."""
        with self._lock:
            return self._reasons.get(uid)

    def held(self) -> list[tuple[int, str, int]]:
        """(uid, depositing session, generation) for each live entry; no key material."""
        with self._lock:
            self._expire_locked()
            return [(uid, entry.session_id, entry.generation) for uid, entry in self._entries.items()]

    def forget_generation(self, uid: int, generation: int, reason: str) -> bool:
        """Drop uid's key only if it is still the generation that was checked."""
        with self._lock:
            entry = self._entries.get(uid)
            if entry is None or entry.generation != generation:
                return False
            self._wipe_locked(uid, reason)
            return True

    def deposit(self, uid: int, username: str, key: bytes, locker_id: str, public_key: bytes,
                ttl: float, *, session_id: str = "") -> Entry:
        ttl = max(self._min_ttl, min(float(ttl), MAX_TTL_SECONDS))
        with self._lock:
            self._wipe_locked(uid, "replaced")
            self._reasons.pop(uid, None)
            generation = self._generation.get(uid, 0) + 1
            self._generation[uid] = generation
            now = self._now()
            entry = Entry(secret=SecretBuffer(key), public_key=bytes(public_key), locker_id=locker_id,
                          deposited=now, expires=now + ttl, username=username, generation=generation,
                          session_id=session_id, sleep_mark=self._sleep_offset())
            self._entries[uid] = entry
            return entry

    def peek(self, uid: int) -> Entry | None:
        with self._lock:
            self._expire_locked()
            return self._entries.get(uid)

    def claim(self, uid: int, generation: int, locker_id: str) -> bytearray | None:
        """Copy the key only when this generation is still the live one."""
        with self._lock:
            self._expire_locked()
            entry = self._entries.get(uid)
            if entry is None or entry.generation != generation or entry.locker_id != locker_id:
                return None
            if entry.expires <= self._now():
                self._wipe_locked(uid, "expiry")
                return None
            return entry.secret.copy()

    def live(self, uid: int, generation: int, locker_id: str) -> bool:
        with self._lock:
            self._expire_locked()
            entry = self._entries.get(uid)
            return (entry is not None and entry.generation == generation and entry.locker_id == locker_id
                    and entry.expires > self._now())

    def forget(self, uid: int) -> bool:
        with self._lock:
            had = uid in self._entries
            self._wipe_locked(uid)
            return had

    def forget_all(self, reason: str = "forget") -> int:
        with self._lock:
            held = list(self._entries)
            for uid in held:
                self._wipe_locked(uid, reason)
            return len(held)


@dataclass(frozen=True)
class CallerSession:
    session_id: str
    uid: int
    active: bool
    state: str
    seat: str
    locked: bool
    remote: bool


def _unusable(session: CallerSession) -> bool:
    return (not session.active or session.state != "active" or not session.seat
            or session.locked or session.remote)


def caller_session(pid: int, *, dbus_module: Any = None) -> CallerSession:
    """Read one live logind snapshot. Its private session files omit LockedHint."""
    if type(pid) is not int or pid <= 0:
        raise AccessDenied("invalid caller pid")
    return _logind_session(
        lambda manager, dbus, timeout: manager.GetSessionByPID(dbus.UInt32(pid), timeout=timeout),
        dbus_module, LOGIND_TIMEOUT_SECONDS)


def session_by_id(session_id: str, *, dbus_module: Any = None,
                  timeout: float = LOGIND_TIMEOUT_SECONDS) -> CallerSession:
    """The same live snapshot for a session named at deposit. SessionGone when logind
    no longer has it; any other failure is AccessDenied, as for caller_session."""
    if not isinstance(session_id, str) or not session_id:
        raise AccessDenied("invalid session id")
    return _logind_session(lambda manager, _dbus, left: manager.GetSession(session_id, timeout=left),
                           dbus_module, timeout)


def session_object_path(session_id: str) -> str:
    """logind's object path for a session id, escaped as sd_bus_path_encode does:
    every byte outside [A-Za-z0-9], and a leading digit, becomes _xx."""
    out = []
    for index, byte in enumerate(session_id.encode("utf-8")):
        ch = chr(byte)
        if ch.isascii() and (ch.isalpha() or (ch.isdigit() and index > 0)):
            out.append(ch)
        else:
            out.append(f"_{byte:02x}")
    return LOGIND_PATH + "/session/" + ("".join(out) or "_")


def _logind_session(locate: Callable[[Any, Any, float], Any], dbus_module: Any,
                    timeout: float) -> CallerSession:
    # One deadline covers both calls: a wedged logind costs at most `timeout`.
    deadline = time.monotonic() + timeout

    def left() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OperationTimeout("logind did not answer in time")
        return remaining

    if dbus_module is None:
        try:
            import dbus as dbus_module
            import dbus.mainloop  # noqa: F401  provides NULL_MAIN_LOOP below
        except ImportError as exc:
            raise DependencyUnavailable("logind requires python3-dbus") from exc
    bus = None
    try:
        # A blocking private connection with no main loop: it never attaches to the
        # GLib context a concurrent reader scan may be running.
        null_loop = getattr(getattr(dbus_module, "mainloop", None), "NULL_MAIN_LOOP", None)
        bus = dbus_module.SystemBus(private=True, mainloop=null_loop)
        manager = dbus_module.Interface(
            bus.get_object(LOGIND, LOGIND_PATH, introspect=False), LOGIND_MANAGER)
        try:
            path = locate(manager, dbus_module, left())
        except Exception as exc:
            name_getter = getattr(exc, "get_dbus_name", None)
            if callable(name_getter) and name_getter() == "org.freedesktop.login1.NoSuchSession":
                raise SessionGone("logind no longer has the session") from exc
            raise
        properties = dbus_module.Interface(
            bus.get_object(LOGIND, path, introspect=False),
            "org.freedesktop.DBus.Properties")
        facts = properties.GetAll(LOGIND_SESSION, timeout=left())
        # No defaults: an absent lock hint is an unknown lock state.
        for key in ("Active", "LockedHint", "Remote"):
            if not isinstance(facts[key], (bool, int)) or facts[key] not in (0, 1):
                raise ValueError("invalid logind boolean")
        uid = facts["User"][0]
        if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
            raise ValueError("invalid logind owner")
        session_id, state, seat = facts["Id"], facts["State"], facts["Seat"][0]
        if not all(isinstance(x, str) for x in (session_id, state, seat)) or not session_id:
            raise ValueError("invalid logind session")
        return CallerSession(str(session_id), int(uid), bool(facts["Active"]), str(state),
                             str(seat), bool(facts["LockedHint"]), bool(facts["Remote"]))
    except SessionGone:
        raise
    except Exception as exc:
        raise AccessDenied("logind could not establish the caller's session and lock state") from exc
    finally:
        if bus is not None:
            bus.close()


class SleepGuard:
    """Forget custody before the system sleeps or shuts down.

    logind delays suspend, hibernate and shutdown while this guard holds a
    delay inhibitor. PrepareForSleep(true) or PrepareForShutdown(true) forgets
    every key first and only then releases the inhibitor, so the key is gone
    before memory is frozen or written to a hibernation image. The inhibitor
    is taken again on resume or when the transition is cancelled. Custody is
    refused while the guard holds no inhibitor: a sleep would not wait for it.
    """

    def __init__(self, forget: Callable[[str], Any], log: Callable[[str], None], *,
                 grace: float = SLEEP_SIGNAL_GRACE_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._forget = forget
        self._log = log
        self._grace = grace
        self._clock = clock
        self._lock = threading.RLock()
        self._inhibit: Callable[[], int] | None = None
        self._fd: int | None = None
        self._sleeping_since: float | None = None
        self.error = "not started"

    def attach(self, inhibit: Callable[[], int]) -> None:
        with self._lock:
            self._inhibit = inhibit
            self.error = ""
        self.ensure()

    def detach(self, reason: str) -> None:
        """The signal source is gone: a sleep could now pass unannounced."""
        with self._lock:
            self._inhibit = None
            self.error = reason
            try:
                self._forget("sleep-watch-lost")
            finally:
                self._release()

    def ready(self) -> bool:
        with self._lock:
            return self._fd is not None and self._sleeping_since is None

    def state(self) -> str:
        with self._lock:
            if self._sleeping_since is not None:
                return "sleeping"
            if self._fd is not None:
                return "ready"
            return "unavailable" + (f" ({self.error})" if self.error else "")

    def ensure(self) -> None:
        """Hold the inhibitor whenever the system is awake and logind is reachable."""
        with self._lock:
            if self._sleeping_since is not None:
                if self._clock() - self._sleeping_since < self._grace:
                    return
                # Awake long after an announcement with no resume signal: the
                # signal was lost. Forget again and re-arm.
                self._log("no resume signal after a sleep announcement; re-arming the sleep guard")
                self._sleeping_since = None
                self._forget("suspend")
            if self._fd is not None or self._inhibit is None:
                return
            try:
                self._fd = self._inhibit()
                self.error = ""
            except Exception as exc:
                self.error = f"logind inhibitor unavailable ({exc.__class__.__name__})"

    def prepare(self, kind: str, starting: bool) -> None:
        reason = "suspend" if kind == "sleep" else "shutdown"
        with self._lock:
            if starting:
                self._sleeping_since = self._clock()
                try:
                    self._forget(reason)
                finally:
                    # Only now may logind proceed.
                    self._release()
                return
            # Resumed, or the transition was cancelled or failed. Nothing can have
            # been deposited meanwhile; forgetting again is the backstop.
            self._sleeping_since = None
            self._forget(reason)
        self.ensure()

    def _release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            self._inhibit = None
            self._release()


def _take_inhibitor(connection: Any) -> int:
    """logind Inhibit("sleep:shutdown", ..., "delay"); the returned fd is the lock."""
    from gi.repository import Gio, GLib
    reply, fds = connection.call_with_unix_fd_list_sync(
        LOGIND, LOGIND_PATH, LOGIND_MANAGER, "Inhibit",
        GLib.Variant("(ssss)", ("sleep:shutdown", "kilix-fingerlock",
                                "Forget the locker key before sleep or shutdown", "delay")),
        GLib.VariantType.new("(h)"), Gio.DBusCallFlags.NONE, int(LOGIND_TIMEOUT_SECONDS * 1000), None, None)
    owned = list(fds.steal_fds()) if fds is not None else []
    index = reply.unpack()[0]
    try:
        if not 0 <= index < len(owned):
            raise ServiceFailure("logind returned no inhibitor descriptor")
        fd = owned.pop(index)
        os.set_inheritable(fd, False)
        return fd
    finally:
        for extra in owned:
            os.close(extra)


class LogindWatch:
    """logind's PrepareForSleep, PrepareForShutdown and Session.Lock signals.

    A private Gio connection on its own thread and its own GLib main context:
    it never dispatches on the default context that a reader scan iterates on
    another thread. Losing the connection detaches the sleep guard, which
    forgets custody and refuses new custody until the watch is back.
    """

    def __init__(self, guard: SleepGuard, on_lock: Callable[[str], None], log: Callable[[str], None], *,
                 address: str | None = None, start_timeout: float = 5.0) -> None:
        self.guard = guard
        self.on_lock = on_lock
        self._log = log
        self.address = address
        self.start_timeout = start_timeout
        self.error = ""
        self._ready = threading.Event()
        self._loop: Any = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        self._thread = threading.Thread(target=self._run, name="fingerlock-logind", daemon=True)
        self._thread.start()
        self._ready.wait(self.start_timeout)
        return self.running()

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self._loop is not None

    def stop(self) -> None:
        loop = self._loop
        if loop is not None:
            loop.quit()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(5)

    def _signal(self, _conn: Any, _sender: Any, path: str, _iface: Any, member: str, params: Any,
                *_user: Any) -> None:
        try:
            if member == "PrepareForSleep":
                self.guard.prepare("sleep", bool(params.unpack()[0]))
            elif member == "PrepareForShutdown":
                self.guard.prepare("shutdown", bool(params.unpack()[0]))
            elif member == "Lock":
                self.on_lock(str(path))
        except Exception as exc:  # a bad signal must not end the watch
            try:
                self._log(f"logind signal {member} failed ({exc.__class__.__name__})")
            except Exception:
                pass

    def _run(self) -> None:
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
        except Exception as exc:
            self.error = f"python3-gi unavailable ({exc.__class__.__name__})"
            self._ready.set()
            return
        context = GLib.MainContext.new()
        context.push_thread_default()
        connection = None
        try:
            address = self.address or Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None)
            connection = Gio.DBusConnection.new_for_address_sync(
                address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
            connection.set_exit_on_close(False)
            loop = GLib.MainLoop.new(context, False)
            for iface, member, path in ((LOGIND_MANAGER, "PrepareForSleep", LOGIND_PATH),
                                        (LOGIND_MANAGER, "PrepareForShutdown", LOGIND_PATH),
                                        (LOGIND_SESSION, "Lock", None)):
                connection.signal_subscribe(LOGIND, iface, member, path, None,
                                            Gio.DBusSignalFlags.NONE, self._signal)
            connection.connect("closed", lambda *_args: loop.quit())
            self.guard.attach(lambda: _take_inhibitor(connection))
            self._loop = loop
            self._ready.set()
            loop.run()
            self.error = "logind connection closed"
        except Exception as exc:
            self.error = f"logind watch failed ({exc.__class__.__name__})"
        finally:
            self._loop = None
            self.guard.detach(self.error or "logind watch stopped")
            self._ready.set()
            if connection is not None:
                try:
                    connection.close_sync(None)
                except Exception:
                    pass
            context.pop_thread_default()


def _peer_pidfd(conn: socket.socket) -> int:
    # Linux SO_PEERPIDFD refers to the connecting process, even after PID reuse.
    # Require it rather than opening a pidfd for a possibly recycled numeric PID.
    try:
        fd = conn.getsockopt(socket.SOL_SOCKET, getattr(socket, "SO_PEERPIDFD", 77))
        os.set_inheritable(fd, False)
        return fd
    except OSError as exc:
        raise AccessDenied("the kernel cannot bind custody to the connecting process") from exc


def _process_alive(pidfd: int) -> bool:
    return not select.select([pidfd], [], [], 0)[0]


def _harden_process() -> HardenReport:
    """Report dumpable/core failures. Silence is not success."""
    report = HardenReport()
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE = 4
            report.errors.append(f"prctl errno {ctypes.get_errno()}")
        else:
            report.dumpable_cleared = True
    except (OSError, AttributeError) as exc:
        report.errors.append(f"prctl unavailable ({exc.__class__.__name__})")
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        report.core_limited = True
    except (ImportError, ValueError, OSError) as exc:
        report.errors.append(f"RLIMIT_CORE ({exc.__class__.__name__})")
    return report


def _peer_cred(conn: socket.socket) -> tuple[int, int, int]:
    raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", raw)
    return pid, uid, gid


def _client_gone(conn: socket.socket) -> bool:
    try:
        ready, _, _ = select.select([conn], [], [], 0)
        if not ready:
            return False
        peeked = conn.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        return peeked == b""
    except BlockingIOError:
        return False
    except OSError:
        return True


class _Stream:
    def __init__(self, conn: socket.socket, deadline_of: Callable[[], float]) -> None:
        self.conn = conn
        self.buf = bytearray()
        self.deadline_of = deadline_of

    def _fill(self, nbytes: int) -> None:
        remaining = self.deadline_of() - time.monotonic()
        if remaining <= 0:
            raise OperationTimeout("request deadline exceeded")
        ready, _, _ = select.select([self.conn], [], [], remaining)
        if not ready:
            raise OperationTimeout("request deadline exceeded")
        chunk = self.conn.recv(nbytes)
        if not chunk:
            raise ServiceFailure("client disconnected")
        self.buf += chunk

    def read_line(self, limit: int) -> bytes:
        while b"\n" not in self.buf:
            if len(self.buf) >= limit:
                raise ServiceFailure("request is too large")
            self._fill(min(4096, limit - len(self.buf)))
        line, _, rest = bytes(self.buf).partition(b"\n")
        self.buf = bytearray(rest)
        return line

    def read_exact(self, nbytes: int) -> bytes:
        while len(self.buf) < nbytes:
            self._fill(min(65536, nbytes - len(self.buf)))
        out = bytes(self.buf[:nbytes])
        del self.buf[:nbytes]
        return out


def _error_line(exc: FingerlockError) -> bytes:
    payload = {"ok": False, "code": exc.code, "error": exc.message, "exit": exc.exit_status}
    return json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n"


class Agent:
    def __init__(self, *, custody: Custody | None = None,
                 client_factory: Callable[[], FprintdClient] = FprintdClient,
                 verify_timeout: float = VERIFY_TIMEOUT_SECONDS,
                 session_lookup: Callable[[int], CallerSession] = caller_session,
                 session_state: Callable[[str], CallerSession] = session_by_id,
                 approve: Callable[..., None] = authorize_operation,
                 verify_attempts: int = VERIFY_ATTEMPTS,
                 session_interval: float = SESSION_CHECK_SECONDS,
                 custody_watch: bool = True,
                 watch_factory: Callable[["Agent"], Any] | None = None,
                 allowed_uid: int | None = None,
                 max_concurrent: int = MAX_CONCURRENT,
                 max_per_uid: int = MAX_PER_UID,
                 request_deadline: float = REQUEST_DEADLINE_SECONDS,
                 body_deadline: float = BODY_DEADLINE_SECONDS,
                 reply_deadline: float = REPLY_DEADLINE_SECONDS,
                 max_body: int = MAX_BODY) -> None:
        self.custody = custody or Custody(start_timer=True)
        self.client_factory = client_factory
        self.verify_timeout = verify_timeout
        self.session_lookup = session_lookup
        self.session_state = session_state
        self.approve = approve
        self.verify_attempts = verify_attempts
        self.session_interval = session_interval
        self._stopping = threading.Event()
        self.log: Callable[[str], None] = lambda line: sys.stderr.write(f"kilix-fingerlock-agent: {line}\n")
        # Custody is accepted and used only while the session monitor runs and the
        # sleep guard holds its logind inhibitor. False is the test interface for
        # agents that never start a monitor.
        self.custody_watch = custody_watch
        self.sleep_guard = SleepGuard(self._forget_all, self._note)
        self.watch_factory = watch_factory or (
            lambda agent: LogindWatch(agent.sleep_guard, agent.on_session_lock, agent._note))
        self._watch: Any = None
        self._watch_tried = -math.inf
        self._monitor: threading.Thread | None = None
        self._beat = -math.inf
        self.allowed_uid = allowed_uid
        self.max_concurrent = max_concurrent
        self.max_per_uid = max_per_uid
        self.request_deadline = request_deadline
        self.body_deadline = body_deadline
        self.reply_deadline = reply_deadline
        self.max_body = max_body
        self._verify_lock = threading.Lock()
        self._admit_lock = threading.Lock()
        self._uid_inflight: dict[int, int] = {}
        self._inflight = 0
        self._tls = threading.local()
        self.before_commit: Callable[[], None] | None = None
        self.hardening = _harden_process()

    def _peer_alive(self) -> bool:
        fn = getattr(self._tls, "alive", None)
        return True if fn is None else bool(fn())

    def _seat(self, pid: int, uid: int) -> str:
        session = self.session_lookup(pid)
        if _unusable(session):
            raise AccessDenied("caller is not in an active seat session that is unlocked and local")
        if session.uid != uid:
            raise AccessDenied("session is not owned by the caller")
        return session.session_id

    # Session transitions: custody belongs to the session that deposited it.
    # Lock (LockedHint or logind's Lock signal), switching away, logout and
    # logind losing the session forget it; suspend, hibernate and shutdown
    # forget it before they proceed (SleepGuard, with Custody's clock check as
    # the backstop); a restart or reboot holds nothing. An approval or scan in
    # progress for that key then stops.
    def transition_reason(self, uid: int, session_id: str) -> str | None:
        try:
            session = self.session_state(session_id)
        except SessionGone:
            return "session-ended"
        except Exception:
            return "session-unknown"
        if session.uid != uid or session.session_id != session_id:
            return "session-unknown"
        if session.locked:
            return "session-locked"
        if _unusable(session):
            return "session-inactive"
        return None

    def check_transitions(self) -> list[tuple[int, str]]:
        dropped = []
        for uid, session_id, generation in self.custody.held():
            reason = self.transition_reason(uid, session_id)
            if reason is not None and self.custody.forget_generation(uid, generation, reason):
                dropped.append((uid, reason))
                self._note(f"custody for uid {uid} forgotten ({reason})")
        return dropped

    def on_session_lock(self, path: str) -> list[tuple[int, str]]:
        """logind's Lock signal for a session object (loginctl lock-session, or a
        desktop asking logind to lock). Forget custody deposited from it at once."""
        dropped = []
        for uid, session_id, generation in self.custody.held():
            if session_object_path(session_id) == path and self.custody.forget_generation(
                    uid, generation, "session-locked"):
                dropped.append((uid, "session-locked"))
                self._note(f"custody for uid {uid} forgotten (session-locked)")
        return dropped

    def _note(self, line: str) -> None:
        # Logging is best effort: a broken journal stream must not stop the monitor.
        try:
            self.log(line)
        except Exception:
            pass

    def _forget_all(self, reason: str) -> None:
        if self.custody.forget_all(reason):
            self._note(f"custody forgotten ({reason})")

    def _fail_closed(self, reason: str) -> None:
        try:
            self._forget_all(reason)
        except Exception:
            # Nothing else can be done here; custody refuses use while unwatched.
            self._note(f"forgetting custody after {reason} failed")

    def _ensure_watch(self) -> None:
        if self._watch is None or not self._watch.running():
            now = time.monotonic()
            if now - self._watch_tried < WATCH_RETRY_SECONDS:
                return
            self._watch_tried = now
            self._watch = self.watch_factory(self)
            if not self._watch.start():
                self._note(f"logind watch unavailable ({getattr(self._watch, 'error', '') or 'not running'}); "
                           "custody is refused until it starts")
        self.sleep_guard.ensure()

    def _tick(self) -> None:
        self._beat = time.monotonic()
        try:
            self.check_transitions()
        except Exception as exc:
            # A check that cannot complete is an unknown state: forget, as for session-unknown.
            self._note(f"session check failed ({exc.__class__.__name__})")
            self._fail_closed("session-check-failed")
        try:
            self._ensure_watch()
        except Exception as exc:
            self._note(f"logind watch check failed ({exc.__class__.__name__})")
        self._beat = time.monotonic()

    def start_monitor(self) -> threading.Thread:
        """Start the logind watch (sleep guard and Lock signal) and the session monitor."""
        def loop() -> None:
            try:
                while not self._stopping.wait(self.session_interval):
                    self._tick()
            finally:
                # However the monitor ends, nothing held may outlive it.
                self._fail_closed("monitor-stopped")
        try:
            self._ensure_watch()
        except Exception as exc:
            self._note(f"logind watch failed to start ({exc.__class__.__name__})")
        self._beat = time.monotonic()
        thread = threading.Thread(target=loop, name="fingerlock-sessions", daemon=True)
        self._monitor = thread
        thread.start()
        return thread

    def monitor_state(self) -> str:
        thread = self._monitor
        if thread is None:
            return "not-started"
        if not thread.is_alive():
            return "stopped"
        if time.monotonic() - self._beat > max(MONITOR_STALL_SECONDS, 3 * self.session_interval):
            return "stalled"
        return "running"

    def watch_problem(self) -> str | None:
        """Why custody cannot be accepted or used now, or None."""
        if not self.custody_watch:
            return None
        state = self.monitor_state()
        if state != "running":
            return f"the session monitor is {state}"
        if not self.sleep_guard.ready():
            return f"the sleep guard is {self.sleep_guard.state()}"
        return None

    def _require_watch(self) -> None:
        problem = self.watch_problem()
        if problem is not None:
            raise ServiceFailure(f"{problem}; the custodian cannot forget the key on lock or sleep, "
                                 "so custody is refused")

    def close(self) -> None:
        self._stopping.set()
        watch = self._watch
        if watch is not None:
            try:
                watch.stop()
            except Exception:
                pass
        self.sleep_guard.close()
        self.custody.close()

    def _hardening(self, entry: Entry | None) -> dict[str, Any]:
        extra = [entry.secret.error] if entry is not None and entry.secret.error else []
        return self.hardening.as_dict(memory_locked=bool(entry and entry.secret.locked), extra=extra)

    def handle(self, request: dict[str, Any], uid: int, pid: int, body: bytes = b"") -> tuple[dict[str, Any], bytes]:
        op = request.get("op")
        try:
            if type(request.get("protocol")) is not int or request["protocol"] != PROTOCOL:
                raise ServiceFailure(f"unsupported protocol {request.get('protocol')!r}")
            if op == "health" and uid == 0:
                return {"ok": True, "uid": self.allowed_uid, "protocol": PROTOCOL}, b""
            if self.allowed_uid is not None and uid != self.allowed_uid:
                raise AccessDenied("this custodian belongs to another user")
            if op == "status":
                return self._status(uid), b""
            if op == "forget":
                return {"ok": True, "forgotten": self.custody.forget(uid)}, b""
            if op == "release":
                raise ServiceFailure("the custodian does not export the locker key")
            if op in ("deposit", "operate"):
                self._require_watch()
            session_id = self._seat(pid, uid)
            if op == "deposit":
                return self._deposit(uid, request, session_id), b""
            if op == "confirm":
                return self._confirm(uid, request, session_id), b""
            if op == "operate":
                return self._operate(uid, pid, request, body)
            raise ServiceFailure(f"unknown op {op!r}")
        except FingerlockError as error:
            return {"ok": False, "code": error.code, "error": error.message, "exit": error.exit_status}, b""

    def _status(self, uid: int) -> dict[str, Any]:
        entry = self.custody.peek(uid)
        if entry is None:
            reply: dict[str, Any] = {"ok": True, "held": False, "hardening": self._hardening(None),
                                     "watch": self._watch_status()}
            reason = self.custody.last_reason(uid)
            if reason is not None:
                reply["last_forget"] = reason
            return reply
        remaining = max(0, int(entry.expires - self.custody._now()))
        return {"ok": True, "held": True, "locker_id": entry.locker_id, "expires_in": remaining,
                "generation": entry.generation, "hardening": self._hardening(entry),
                "watch": self._watch_status()}

    def _watch_status(self) -> dict[str, Any]:
        return {"monitor": self.monitor_state(), "sleep_guard": self.sleep_guard.state(),
                "required": self.custody_watch}

    def _deposit(self, uid: int, request: dict[str, Any], session_id: str) -> dict[str, Any]:
        try:
            key = bytes.fromhex(str(request["key"]))
            locker_id = str(request["locker_id"])
            ttl = request.get("ttl", DEFAULT_TTL_SECONDS)
            if (isinstance(ttl, bool) or not isinstance(ttl, (float, int))
                    or not math.isfinite(ttl) or ttl < 0):
                raise ValueError("ttl must be a finite non-negative number")
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise ServiceFailure(f"bad deposit: {exc}") from exc
        if len(key) != SEED_BYTES or any(ch not in "0123456789abcdef" for ch in str(request["key"]).lower()):
            raise ServiceFailure("bad deposit: key must be 32 bytes")
        if not locker_id or len(locker_id) > 81:
            raise ServiceFailure("bad deposit: locker id")
        try:
            username = pwd.getpwuid(uid).pw_name
            _private, public = Locker._keypair(key)
        except KeyError as exc:
            raise AccessDenied(f"unknown uid {uid}") from exc
        except Exception as exc:
            raise ServiceFailure(f"bad deposit: {exc}") from exc
        entry = self.custody.deposit(uid, username, key, locker_id, public, ttl, session_id=session_id)
        return {"ok": True, "held": True, "locker_id": locker_id, "generation": entry.generation,
                "expires_in": int(entry.expires - self.custody._now())}

    def _confirm(self, uid: int, request: dict[str, Any], session_id: str) -> dict[str, Any]:
        try:
            claimed = bytes.fromhex(str(request["public_key"]))
            locker_id = str(request["locker_id"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ServiceFailure(f"bad confirm: {exc}") from exc
        entry = self.custody.peek(uid)
        if entry is None:
            raise AccessDenied("no key deposited for this user")
        if entry.session_id != session_id:
            raise AccessDenied("custody belongs to the session that deposited the key")
        if entry.locker_id != locker_id or entry.public_key != claimed:
            raise AccessDenied("locker metadata does not match custody; nothing was sealed")
        remaining = max(0, int(entry.expires - self.custody._now()))
        return {"ok": True, "held": True, "locker_id": entry.locker_id,
                "public_key": entry.public_key.hex(), "expires_in": remaining}

    def _binding(self, request: dict[str, Any], body: bytes) -> dict[str, Any]:
        binding = request.get("binding")
        if not isinstance(binding, dict):
            raise ServiceFailure("missing operation binding")
        kind = binding.get("kind")
        name = binding.get("name")
        digest = binding.get("sha256")
        size = binding.get("size")
        if kind not in ("unlock", "password-get"):
            raise ServiceFailure("unsupported operation")
        if not isinstance(name, str) or not name or len(name) > 256 or any(ord(ch) < 32 for ch in name):
            raise ServiceFailure("bad operation name")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ServiceFailure("bad operation digest")
        if isinstance(size, bool) or not isinstance(size, int) or size != len(body):
            raise ServiceFailure("operation size does not match the container")
        if hashlib.sha256(body).hexdigest() != digest:
            raise ServiceFailure("operation digest does not match the container")
        body_bytes = request.get("body_bytes")
        if isinstance(body_bytes, bool) or not isinstance(body_bytes, int) or body_bytes != len(body):
            raise ServiceFailure("body_bytes does not match the container")
        if len(body) > self.max_body:
            raise ServiceFailure("container exceeds the size limit")
        return {"kind": kind, "name": name, "sha256": digest, "size": size}

    def _operate(self, uid: int, pid: int, request: dict[str, Any], body: bytes) -> tuple[dict[str, Any], bytes]:
        try:
            locker_id = str(request["locker_id"])
            finger = str(request.get("finger", ANY_FINGER))
        except (KeyError, TypeError) as exc:
            raise ServiceFailure(f"bad operate: {exc}") from exc
        binding = self._binding(request, body)
        entry = self.custody.peek(uid)
        if entry is None:
            raise AccessDenied("no key deposited for this user; unlock with the passphrase first")
        if entry.locker_id != locker_id:
            raise AccessDenied("the deposited key belongs to a different locker")
        generation = entry.generation
        username = entry.username
        session_id = self._seat(pid, uid)
        if entry.session_id != session_id:
            raise AccessDenied("custody belongs to the session that deposited the key")
        if not self._peer_alive():
            raise AccessDenied("caller disconnected")

        def wanted() -> bool:
            # The approval dialog and the scan stop once the caller leaves or
            # custody for this generation is forgotten, expires or is replaced.
            return self._peer_alive() and self.custody.live(uid, generation, locker_id)

        if not self._verify_lock.acquire(timeout=self.verify_timeout):
            raise ServiceFailure("the reader is busy with another verification")
        try:
            if not self._peer_alive():
                raise AccessDenied("caller disconnected")
            self._revalidate(uid, pid, session_id, generation, locker_id)
            self.approve(uid, pid, binding, wanted)
            self._revalidate(uid, pid, session_id, generation, locker_id)
            if not self._peer_alive():
                raise AccessDenied("caller disconnected after operation approval")
            client = self.client_factory()
            device = client.default_device()
            session = client.verify(device.path, finger, username=username, timeout=self.verify_timeout,
                                    attempts=self.verify_attempts, cancelled=lambda: not wanted())
        finally:
            self._verify_lock.release()
        if not self._peer_alive():
            raise AccessDenied("caller disconnected")
        if getattr(session, "cancelled", False):
            raise AccessDenied("custody was revoked during verification")
        if session.timed_out:
            raise OperationTimeout(f"no finger within {self.verify_timeout:g}s")
        if session.result != "verify-match":
            if session.result == "verify-no-match":
                raise NoMatch("no match")
            raise OperationFailed(f"verification ended with {session.result}")
        self._revalidate(uid, pid, session_id, generation, locker_id)
        key_copy = self.custody.claim(uid, generation, locker_id)
        if key_copy is None:
            raise AccessDenied("custody was revoked")
        plain_buf: bytearray | None = None
        try:
            try:
                _header, plain = unlock_bytes(body, bytes(key_copy))
            except LockerError:
                raise
            plain_buf = bytearray(plain)
        finally:
            wipe_buffer(key_copy)
        try:
            if self.before_commit is not None:
                self.before_commit()
            if not self._peer_alive():
                raise AccessDenied("caller disconnected before the operation was released")
            self._revalidate(uid, pid, session_id, generation, locker_id)
            delivered = bytes(plain_buf)
        finally:
            wipe_buffer(plain_buf)
        return {
            "ok": True, "action": "unseal", "locker_id": locker_id, "binding": binding,
            "plain_bytes": len(delivered), "plain_sha256": hashlib.sha256(delivered).hexdigest(),
            "finger": session.selected_finger, "device": device.name, "generation": generation,
            "scans": session.attempts,
        }, delivered

    def _revalidate(self, uid: int, pid: int, session_id: str, generation: int, locker_id: str) -> None:
        current = self._seat(pid, uid)
        if current != session_id:
            raise AccessDenied("session changed during verification")
        if not self.custody.live(uid, generation, locker_id):
            raise AccessDenied("custody expired or was revoked")

    def _admit(self, uid: int) -> str | None:
        with self._admit_lock:
            if self._inflight >= self.max_concurrent:
                return "agent is saturated"
            if self._uid_inflight.get(uid, 0) >= self.max_per_uid:
                return "too many requests for this user"
            self._inflight += 1
            self._uid_inflight[uid] = self._uid_inflight.get(uid, 0) + 1
            return None

    def _release_slot(self, uid: int) -> None:
        with self._admit_lock:
            self._inflight = max(0, self._inflight - 1)
            left = self._uid_inflight.get(uid, 0) - 1
            if left > 0:
                self._uid_inflight[uid] = left
            else:
                self._uid_inflight.pop(uid, None)

    def serve(self, listener: socket.socket) -> None:
        listener.setblocking(True)
        while True:
            try:
                conn, _addr = listener.accept()
            except OSError as exc:
                # Closing the listener ends the server. Other accept errors do not.
                if exc.errno in (errno.EBADF, errno.EINVAL, errno.ENOTSOCK):
                    return
                continue
            try:
                pid, uid, _gid = _peer_cred(conn)
                pidfd = _peer_pidfd(conn)
            except (OSError, FingerlockError):
                conn.close()
                continue
            reason = self._admit(uid)
            if reason is not None:
                os.close(pidfd)
                try:
                    conn.settimeout(0.1)
                    conn.sendall(_error_line(Busy(reason)))
                except OSError:
                    pass
                conn.close()
                continue
            threading.Thread(target=self._serve_guarded, args=(conn, pid, uid, pidfd), daemon=True).start()

    def _serve_guarded(self, conn: socket.socket, pid: int, uid: int, pidfd: int) -> None:
        try:
            self._tls.alive = lambda: _process_alive(pidfd) and not _client_gone(conn)
            self._serve_connection(conn, pid, uid)
        finally:
            os.close(pidfd)
            self._release_slot(uid)
            try:
                conn.close()
            except OSError:
                pass

    def _serve_connection(self, conn: socket.socket, pid: int, uid: int) -> None:
        deadlines = {"at": time.monotonic() + self.request_deadline}
        stream = _Stream(conn, lambda: deadlines["at"])
        try:
            line = stream.read_line(MAX_LINE)
            try:
                request = json.loads(line.decode("utf-8"))
                if not isinstance(request, dict):
                    raise ValueError("not an object")
            except ValueError as exc:
                raise ServiceFailure(f"bad request: {exc}") from exc
            body = b""
            if request.get("op") == "operate":
                body_bytes = request.get("body_bytes")
                if isinstance(body_bytes, bool) or not isinstance(body_bytes, int) or body_bytes < 0 or body_bytes > self.max_body:
                    raise ServiceFailure("body_bytes out of range")
                deadlines["at"] = time.monotonic() + self.body_deadline
                body = stream.read_exact(body_bytes)
            if not self._peer_alive():
                raise AccessDenied("caller disconnected")
            response, plain = self.handle(request, uid, pid, body)
            if "key" in response:
                response = {"ok": False, "code": "ESERVICE",
                            "error": "refusing to send the locker key", "exit": 6}
                plain = b""
            conn.settimeout(self.reply_deadline)
            conn.sendall(json.dumps(response, sort_keys=True).encode("utf-8") + b"\n"
                         + (plain if response.get("ok") else b""))
        except FingerlockError as exc:
            try:
                conn.settimeout(self.reply_deadline)
                conn.sendall(_error_line(exc))
            except OSError:
                return
        except OSError:
            return


def _listener_from_systemd(uid: int) -> socket.socket:
    if os.environ.get("LISTEN_FDS") != "1" or os.environ.get("LISTEN_PID") != str(os.getpid()):
        raise ServiceFailure("the custodian requires one systemd socket")
    listener = socket.socket(fileno=3)
    if (listener.family != socket.AF_UNIX or listener.type != socket.SOCK_STREAM
            or listener.getsockname() != socket_path(uid)
            or not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)):
        listener.close()
        raise ServiceFailure("unexpected systemd socket")
    return listener


def serve(uid: int) -> int:
    if os.geteuid() != 0 or uid <= 0:
        sys.stderr.write("kilix-fingerlock-agent: root and a non-root owner uid are required\n")
        return 1
    listener = _listener_from_systemd(uid)
    agent = Agent(allowed_uid=uid)
    agent.start_monitor()
    try:
        agent.serve(listener)
    finally:
        agent.close()
        listener.close()
    return 0


def _reject_key(response: dict[str, Any]) -> None:
    if "key" in response or "private_key" in response or "seed" in response:
        raise ServiceFailure("agent offered key material; refusing")


class AgentClient:
    """The verb's side of the socket.

    `trusted_uid` defaults to 0, the root custodian. A non-zero value is the
    explicit test interface for an unprivileged server. The verb does not set it.
    Key bytes are sent only after the peer uid matches.
    """

    def __init__(self, path: str = SOCKET_PATH, *, timeout: float = APPROVAL_TIMEOUT + 2 * VERIFY_TIMEOUT_SECONDS + 30,
                 trusted_uid: int = 0) -> None:
        self.path = path
        self.timeout = timeout
        self.trusted_uid = trusted_uid

    def available(self) -> bool:
        return os.path.exists(self.path)

    def call(self, op: str, *, body: bytes = b"", **fields: Any) -> dict[str, Any]:
        response, _plain = self.transact(op, body=body, **fields)
        return response

    def _check_success(self, op: str, response: dict[str, Any], fields: dict[str, Any], body: bytes) -> None:
        if op == "deposit":
            if response.get("locker_id") != fields.get("locker_id") or response.get("held") is not True:
                raise ServiceFailure("agent deposit response did not match the request")
        elif op == "confirm":
            if response.get("locker_id") != fields.get("locker_id") or response.get("public_key") != fields.get("public_key"):
                raise ServiceFailure("agent confirm response did not match the request")
        elif op == "operate":
            binding = fields.get("binding")
            if response.get("action") != "unseal" or response.get("locker_id") != fields.get("locker_id"):
                raise ServiceFailure("agent operate response did not match the request")
            if response.get("binding") != binding:
                raise ServiceFailure("agent returned a different operation binding")
            plain_bytes = response.get("plain_bytes")
            digest = response.get("plain_sha256")
            if isinstance(plain_bytes, bool) or not isinstance(plain_bytes, int) or plain_bytes < 0:
                raise ServiceFailure("agent returned a bad plaintext length")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ServiceFailure("agent returned a bad plaintext digest")
            # The plaintext itself is read by unseal(), which checks the digest.
            response["_expect_plain"] = plain_bytes
            response["_expect_sha"] = digest
        elif op == "status":
            if not isinstance(response.get("held"), bool):
                raise ServiceFailure("agent status is missing held")
        elif op == "forget":
            if not isinstance(response.get("forgotten"), bool):
                raise ServiceFailure("agent forget response is malformed")

    def transact(self, op: str, *, body: bytes = b"", **fields: Any) -> tuple[dict[str, Any], bytes]:
        """Complete one request and, for operate, consume the plaintext frame."""
        request = {"protocol": PROTOCOL, "op": op, **fields}
        payload = json.dumps(request).encode("utf-8") + b"\n"
        try:
            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except OSError as exc:
            raise AgentUnavailable(f"fingerlock agent not reachable at {self.path} ({exc.__class__.__name__})") from exc
        try:
            conn.settimeout(self.timeout)
            try:
                conn.connect(self.path)
            except (OSError, socket.timeout) as exc:
                raise AgentUnavailable(
                    f"fingerlock agent not reachable at {self.path} ({exc.__class__.__name__}); "
                    "install it with: kilix fingerlock install --agent") from exc
            try:
                _pid, uid, _gid = _peer_cred(conn)
            except OSError as exc:
                raise AgentUnavailable(f"fingerlock agent peer could not be checked ({exc.__class__.__name__})") from exc
            if uid != self.trusted_uid:
                raise AccessDenied(
                    f"fingerlock agent peer uid {uid} is not the trusted custodian uid {self.trusted_uid}")
            send_error = None
            try:
                conn.sendall(payload)
                if body:
                    conn.sendall(body)
            except (BrokenPipeError, ConnectionResetError) as exc:
                # A full agent can send EBUSY and close before reading any
                # request bytes. Consume that refusal, but never accept success
                # for a request we could not finish sending.
                send_error = exc
            line, rest = _read_response_line(conn)
            if line is None:
                raise AgentUnavailable("fingerlock agent closed the connection")
            try:
                response = json.loads(line.decode("utf-8"))
            except ValueError as exc:
                raise ServiceFailure(f"bad reply from the fingerlock agent: {exc}") from exc
            if not isinstance(response, dict):
                raise ServiceFailure("bad reply from the fingerlock agent")
            _reject_key(response)
            if not response.get("ok"):
                _expect_eof(conn, rest, allow_reset=True)
                code = str(response.get("code"))
                cls = {"EACCESS": AccessDenied, "ENOMATCH": NoMatch, "ETIMEOUT": OperationTimeout,
                       "EOPERATION": OperationFailed, "ENODEVICE": DeviceNotFound,
                       "EDEPENDENCY": DependencyUnavailable, "EBUSY": Busy,
                       "ELOCKER": LockerError}.get(code, ServiceFailure)
                error = cls(str(response.get("error", "agent refused")))
                error.code = code
                raise error
            if send_error is not None:
                raise ServiceFailure("agent reported success before accepting the complete request") from send_error
            self._check_success(op, response, fields, body)
            plain = b""
            if op == "operate":
                plain = _read_exact_socket(conn, int(response["_expect_plain"]), rest)
                if hashlib.sha256(plain).hexdigest() != response["_expect_sha"]:
                    raise ServiceFailure("agent plaintext does not match its digest")
                response.pop("_expect_plain", None)
                response.pop("_expect_sha", None)
            _expect_eof(conn, rest if op != "operate" else b"")
            return response, plain
        except OSError as exc:
            raise ServiceFailure(f"agent connection failed ({exc.__class__.__name__})") from exc
        finally:
            conn.close()

    def status(self) -> dict[str, Any]:
        return self.call("status")

    def deposit(self, key: bytes, locker_id: str, ttl: float = DEFAULT_TTL_SECONDS) -> dict[str, Any]:
        return self.call("deposit", key=key.hex(), locker_id=locker_id, ttl=ttl)

    def confirm(self, locker_id: str, public_key: bytes) -> dict[str, Any]:
        return self.call("confirm", locker_id=locker_id, public_key=public_key.hex())

    def unseal(self, locker_id: str, container: bytes, binding: dict[str, Any],
               finger: str = ANY_FINGER) -> bytes:
        _response, plain = self.transact(
            "operate", body=container, action="unseal", locker_id=locker_id, finger=finger,
            body_bytes=len(container), binding=binding)
        return plain

    def forget(self) -> bool:
        return bool(self.call("forget").get("forgotten"))


def _read_response_line(conn: socket.socket) -> tuple[bytes | None, bytes]:
    """Return the JSON line and any bytes already read past its newline."""
    buf = bytearray()
    while len(buf) < MAX_LINE and b"\n" not in buf:
        try:
            chunk = conn.recv(4096)
        except socket.timeout as exc:
            raise OperationTimeout("timed out waiting for the agent") from exc
        if not chunk:
            return (bytes(buf) if buf else None), b""
        buf += chunk
    if b"\n" not in buf:
        raise ServiceFailure("agent reply is too large")
    line, _, rest = bytes(buf).partition(b"\n")
    return line, rest


def _expect_eof(conn: socket.socket, prefix: bytes = b"", *, allow_reset: bool = False) -> None:
    # The server releases admission before closing this one-request connection.
    # A reply alone is not completion: immediately opening another connection
    # could otherwise exhaust slots still held by successfully answered calls.
    if prefix:
        raise ServiceFailure("unexpected bytes after the agent reply")
    try:
        if conn.recv(1):
            raise ServiceFailure("unexpected bytes after the agent reply")
    except ConnectionResetError as exc:
        # Admission refusal can close with unread request bytes, producing a
        # reset after the complete error frame. Preserve that typed refusal.
        if not allow_reset:
            raise ServiceFailure("agent reset before request completion") from exc
    except socket.timeout as exc:
        raise OperationTimeout("timed out waiting for agent completion") from exc


def _read_exact_socket(conn: socket.socket, nbytes: int, prefix: bytes = b"") -> bytes:
    if len(prefix) > nbytes:
        raise ServiceFailure("unexpected bytes after the agent plaintext")
    buf = bytearray(prefix)
    while len(buf) < nbytes:
        try:
            chunk = conn.recv(min(65536, nbytes - len(buf)))
        except socket.timeout as exc:
            raise OperationTimeout("timed out waiting for the agent") from exc
        if not chunk:
            raise ServiceFailure("agent closed before the plaintext finished")
        buf += chunk
    return bytes(buf[:nbytes])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == "--serve" and argv[1].isdigit():
        return serve(int(argv[1]))
    sys.stderr.write("usage: fingerlock_agent.py --serve UID\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
