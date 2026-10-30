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
the container bytes) checked before verification and again before the
plaintext is released. A passphrase rewrap does not rotate the key and does
not revoke plaintext already returned. Rewrap is not a substitute for forget.

Callers are identified by SO_PEERCRED. Deposit, confirm and operate require
an active, seated, unlocked logind session owned by that uid. The session id
comes from the cgroup session scope (`session-<id>.scope`), not from the
audit id in /proc/<pid>/sessionid. A missing scope fails closed. ACTIVE alone
is not enough. Status and forget do not require a seat; forget from any
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
the kernel accepts the setting.

The socket is world-accessible and systemd-activated. Admission is bounded
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
    FprintdClient, NoMatch, OperationFailed, OperationTimeout, ServiceFailure, _read,
)
from fingerlock_locker import Locker, LockerError, SEED_BYTES, unlock_bytes, wipe_buffer  # noqa: E402

SOCKET_PATH = "/run/kilix-fingerlock/agent.sock"
INSTALL_DIR = "/usr/local/lib/kilix-fingerlock"
SOCKET_UNIT = "kilix-fingerlock-agent.socket"
SERVICE_UNIT = "kilix-fingerlock-agent.service"
PROTOCOL = 1
DEFAULT_TTL_SECONDS = 12 * 3600
MAX_TTL_SECONDS = 7 * 24 * 3600
MIN_TTL_SECONDS = 60.0
VERIFY_TIMEOUT_SECONDS = 45.0
MAX_LINE = 64 * 1024
MAX_BODY = 64 * 1024 * 1024 + 2 * 1024 * 1024
MAX_CONCURRENT = 8
MAX_PER_UID = 2
REQUEST_DEADLINE_SECONDS = 5.0
BODY_DEADLINE_SECONDS = 30.0

SOCKET_UNIT_TEXT = f"""[Unit]
Description=Kilix fingerlock agent socket

[Socket]
ListenStream={SOCKET_PATH}
SocketMode=0666
DirectoryMode=0755
RemoveOnStop=yes

[Install]
WantedBy=sockets.target
"""

SERVICE_UNIT_TEXT = f"""[Unit]
Description=Kilix fingerlock agent (fingerprint-gated key custody)
Documentation=man:kilix(1)
Requires={SOCKET_UNIT}
After={SOCKET_UNIT} dbus.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 {INSTALL_DIR}/fingerlock_agent.py --serve
NonBlocking=no
LimitCORE=0
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_UNIX
MemoryDenyWriteExecute=yes
RestrictRealtime=yes
LockPersonality=yes
SystemCallFilter=@system-service
"""


class AgentUnavailable(DependencyUnavailable):
    """The agent is not installed or not answering; the passphrase path still works."""


class Busy(ServiceFailure):
    code, exit_status = "EBUSY", 6


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


class Custody:
    """In-memory per-uid keys. Expiry runs on a timer and again on use."""

    def __init__(self, now: Callable[[], float] = time.monotonic, *, min_ttl: float = MIN_TTL_SECONDS,
                 start_timer: bool = False, sweep_interval: float = 1.0) -> None:
        self._entries: dict[int, Entry] = {}
        self._generation: dict[int, int] = {}
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
        self.forget_all()

    def _timer_loop(self) -> None:
        while not self._stop.wait(self.sweep_interval):
            self.sweep()

    def sweep(self) -> None:
        with self._lock:
            self._expire_locked()

    def _expire_locked(self) -> None:
        now = self._now()
        for uid, entry in list(self._entries.items()):
            if entry.expires <= now:
                self._wipe_locked(uid)

    def _wipe_locked(self, uid: int) -> None:
        entry = self._entries.pop(uid, None)
        if entry is not None:
            entry.secret.wipe()

    def deposit(self, uid: int, username: str, key: bytes, locker_id: str, public_key: bytes,
                ttl: float) -> Entry:
        ttl = max(self._min_ttl, min(float(ttl), MAX_TTL_SECONDS))
        with self._lock:
            self._wipe_locked(uid)
            generation = self._generation.get(uid, 0) + 1
            self._generation[uid] = generation
            now = self._now()
            entry = Entry(secret=SecretBuffer(key), public_key=bytes(public_key), locker_id=locker_id,
                          deposited=now, expires=now + ttl, username=username, generation=generation)
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
                self._wipe_locked(uid)
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

    def forget_all(self) -> None:
        with self._lock:
            for uid in list(self._entries):
                self._wipe_locked(uid)


def session_of_pid(pid: int, *, proc: Path = Path("/proc")) -> str:
    """Logind session from the cgroup scope. No scope, including a user manager, fails closed.

    /proc/<pid>/sessionid is not consulted: with auditing it is an audit id,
    which this gate does not treat as a logind session.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return ""
    text = _read(proc / str(pid) / "cgroup") or ""
    found = []
    for line in text.splitlines():
        for part in line.split("/"):
            if part.startswith("session-") and part.endswith(".scope") and part[8:-6].isdigit():
                found.append(part[8:-6])
    return found[-1] if found else ""


def _session_facts(record: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in record.splitlines() if "=" in line)


def _locked(facts: dict[str, str]) -> bool:
    return facts.get("LOCKED_HINT") in ("1", "yes") or facts.get("LockedHint") == "yes"


def session_owner_uid(session_id: str, *, sessions_dir: Path = Path("/run/systemd/sessions")) -> int | None:
    if not session_id or not session_id.isdigit():
        return None
    record = _read(sessions_dir / session_id)
    if not record:
        return None
    uid = _session_facts(record).get("UID", "")
    if not uid.isdigit():
        return None
    return int(uid)


def session_is_active_seat(session_id: str, *, run: Callable[..., Any] = subprocess.run,
                           sessions_dir: Path = Path("/run/systemd/sessions")) -> tuple[bool, str]:
    """Active, seated and unlocked. A lock hint refuses the session."""
    if not session_id:
        return False, "no logind session"
    record = _read(sessions_dir / session_id)
    if record:
        facts = _session_facts(record)
        active = facts.get("ACTIVE") == "1" and facts.get("STATE") == "active"
        seat = facts.get("SEAT", "")
        locked = _locked(facts)
        ok = active and bool(seat) and not locked
        why = "locked" if locked else f"active={active} seat={seat or '-'}"
        return ok, f"session {session_id} {why}"
    try:
        proc = run(["loginctl", "show-session", session_id, "-p", "Active", "-p", "State",
                    "-p", "Seat", "-p", "LockedHint"],
                   capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"loginctl unavailable ({exc.__class__.__name__})"
    if proc.returncode != 0:
        return False, f"loginctl cannot describe session {session_id}"
    facts = _session_facts(proc.stdout)
    seat = facts.get("Seat", "")
    locked = _locked(facts)
    active = facts.get("Active") == "yes" and facts.get("State", "active") == "active"
    ok = active and bool(seat) and not locked
    return ok, f"session {session_id} Active={facts.get('Active')} Seat={seat or '-'}"


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
                 proc: Path = Path("/proc"),
                 session_check: Callable[[str], tuple[bool, str]] = session_is_active_seat,
                 session_owner: Callable[[str], int | None] | None = None,
                 max_concurrent: int = MAX_CONCURRENT,
                 max_per_uid: int = MAX_PER_UID,
                 request_deadline: float = REQUEST_DEADLINE_SECONDS,
                 body_deadline: float = BODY_DEADLINE_SECONDS,
                 max_body: int = MAX_BODY) -> None:
        self.custody = custody or Custody(start_timer=True)
        self.client_factory = client_factory
        self.verify_timeout = verify_timeout
        self.proc = proc
        self.session_check = session_check
        self.session_owner = session_owner if session_owner is not None else session_owner_uid
        self.max_concurrent = max_concurrent
        self.max_per_uid = max_per_uid
        self.request_deadline = request_deadline
        self.body_deadline = body_deadline
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
        session_id = session_of_pid(pid, proc=self.proc)
        active, detail = self.session_check(session_id)
        if not active:
            raise AccessDenied(f"caller is not in an active seat session ({detail})")
        owner = self.session_owner(session_id)
        if owner is None or int(owner) != int(uid):
            raise AccessDenied("session is not owned by the caller")
        return session_id

    def _hardening(self, entry: Entry | None) -> dict[str, Any]:
        extra = [entry.secret.error] if entry is not None and entry.secret.error else []
        return self.hardening.as_dict(memory_locked=bool(entry and entry.secret.locked), extra=extra)

    def handle(self, request: dict[str, Any], uid: int, pid: int, body: bytes = b"") -> tuple[dict[str, Any], bytes]:
        op = request.get("op")
        try:
            if request.get("protocol") != PROTOCOL:
                raise ServiceFailure(f"unsupported protocol {request.get('protocol')!r}")
            if op == "status":
                return self._status(uid), b""
            if op == "forget":
                return {"ok": True, "forgotten": self.custody.forget(uid)}, b""
            if op == "release":
                raise ServiceFailure("the custodian does not export the locker key")
            self._seat(pid, uid)
            if op == "deposit":
                return self._deposit(uid, request), b""
            if op == "confirm":
                return self._confirm(uid, request), b""
            if op == "operate":
                return self._operate(uid, pid, request, body)
            raise ServiceFailure(f"unknown op {op!r}")
        except FingerlockError as error:
            return {"ok": False, "code": error.code, "error": error.message, "exit": error.exit_status}, b""

    def _status(self, uid: int) -> dict[str, Any]:
        entry = self.custody.peek(uid)
        if entry is None:
            return {"ok": True, "held": False, "hardening": self._hardening(None)}
        remaining = max(0, int(entry.expires - self.custody._now()))
        return {"ok": True, "held": True, "locker_id": entry.locker_id, "expires_in": remaining,
                "generation": entry.generation, "hardening": self._hardening(entry)}

    def _deposit(self, uid: int, request: dict[str, Any]) -> dict[str, Any]:
        try:
            key = bytes.fromhex(str(request["key"]))
            locker_id = str(request["locker_id"])
            ttl = float(request.get("ttl", DEFAULT_TTL_SECONDS))
        except (KeyError, ValueError, TypeError) as exc:
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
        entry = self.custody.deposit(uid, username, key, locker_id, public, ttl)
        return {"ok": True, "held": True, "locker_id": locker_id, "generation": entry.generation,
                "expires_in": int(entry.expires - self.custody._now())}

    def _confirm(self, uid: int, request: dict[str, Any]) -> dict[str, Any]:
        try:
            claimed = bytes.fromhex(str(request["public_key"]))
            locker_id = str(request["locker_id"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ServiceFailure(f"bad confirm: {exc}") from exc
        entry = self.custody.peek(uid)
        if entry is None:
            raise AccessDenied("no key deposited for this user")
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
        if not self._peer_alive():
            raise AccessDenied("caller disconnected")
        if not self._verify_lock.acquire(timeout=self.verify_timeout):
            raise ServiceFailure("the reader is busy with another verification")
        try:
            if not self._peer_alive():
                raise AccessDenied("caller disconnected")
            client = self.client_factory()
            device = client.default_device()
            session = client.verify(device.path, finger, username=username, timeout=self.verify_timeout)
        finally:
            self._verify_lock.release()
        if not self._peer_alive():
            raise AccessDenied("caller disconnected")
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
            except OSError:
                conn.close()
                continue
            reason = self._admit(uid)
            if reason is not None:
                try:
                    conn.sendall(_error_line(Busy(reason)))
                except OSError:
                    pass
                conn.close()
                continue
            threading.Thread(target=self._serve_guarded, args=(conn, pid, uid), daemon=True).start()

    def _serve_guarded(self, conn: socket.socket, pid: int, uid: int) -> None:
        try:
            self._tls.alive = lambda: not _client_gone(conn)
            self._serve_connection(conn, pid, uid)
        finally:
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
                conn.sendall(_error_line(ServiceFailure(f"bad request: {exc}")))
                return
            body = b""
            if request.get("op") == "operate":
                body_bytes = request.get("body_bytes")
                if isinstance(body_bytes, bool) or not isinstance(body_bytes, int) or body_bytes < 0 or body_bytes > self.max_body:
                    conn.sendall(_error_line(ServiceFailure("body_bytes out of range")))
                    return
                deadlines["at"] = time.monotonic() + self.body_deadline
                body = stream.read_exact(body_bytes)
            if not self._peer_alive():
                conn.sendall(_error_line(AccessDenied("caller disconnected")))
                return
            response, plain = self.handle(request, uid, pid, body)
            if "key" in response:
                response = {"ok": False, "code": "ESERVICE",
                            "error": "refusing to send the locker key", "exit": 6}
                plain = b""
            conn.sendall(json.dumps(response, sort_keys=True).encode("utf-8") + b"\n")
            if response.get("ok") and plain:
                conn.sendall(plain)
        except FingerlockError as exc:
            try:
                conn.sendall(_error_line(exc))
            except OSError:
                return
        except OSError:
            return


def _listener_from_systemd_or_path(path: str) -> socket.socket:
    fds = int(os.environ.get("LISTEN_FDS", "0") or 0)
    if fds >= 1 and os.environ.get("LISTEN_PID", "") == str(os.getpid()):
        return socket.socket(fileno=3)
    parent = Path(path).parent
    parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    os.chmod(path, 0o666)
    listener.listen(16)
    return listener


def serve(path: str = SOCKET_PATH) -> int:
    if os.geteuid() != 0:
        sys.stderr.write("kilix-fingerlock-agent: must run as root\n")
        return 1
    listener = _listener_from_systemd_or_path(path)
    Agent().serve(listener)
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

    def __init__(self, path: str = SOCKET_PATH, *, timeout: float = VERIFY_TIMEOUT_SECONDS + 30,
                 trusted_uid: int = 0) -> None:
        self.path = path
        self.timeout = timeout
        self.trusted_uid = trusted_uid

    def available(self) -> bool:
        return os.path.exists(self.path)

    def call(self, op: str, *, body: bytes = b"", **fields: Any) -> dict[str, Any]:
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
            conn.sendall(payload)
            if body:
                conn.sendall(body)
            line, _rest = _read_response_line(conn)
        finally:
            conn.close()
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
            code = str(response.get("code"))
            cls = {"EACCESS": AccessDenied, "ENOMATCH": NoMatch, "ETIMEOUT": OperationTimeout,
                   "EOPERATION": OperationFailed, "ENODEVICE": DeviceNotFound,
                   "EDEPENDENCY": DependencyUnavailable, "EBUSY": Busy,
                   "ELOCKER": LockerError}.get(code, ServiceFailure)
            error = cls(str(response.get("error", "agent refused")))
            error.code = code
            raise error
        self._check_success(op, response, fields, body)
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
        """call() plus, for operate, the plaintext frame. Used by unseal."""
        request = {"protocol": PROTOCOL, "op": op, **fields}
        payload = json.dumps(request).encode("utf-8") + b"\n"
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
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
            conn.sendall(payload)
            if body:
                conn.sendall(body)
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
                code = str(response.get("code"))
                cls = {"EACCESS": AccessDenied, "ENOMATCH": NoMatch, "ETIMEOUT": OperationTimeout,
                       "EOPERATION": OperationFailed, "ENODEVICE": DeviceNotFound,
                       "EDEPENDENCY": DependencyUnavailable, "EBUSY": Busy,
                       "ELOCKER": LockerError}.get(code, ServiceFailure)
                error = cls(str(response.get("error", "agent refused")))
                error.code = code
                raise error
            self._check_success(op, response, fields, body)
            plain = b""
            if op == "operate":
                plain = _read_exact_socket(conn, int(response["_expect_plain"]), rest)
                if hashlib.sha256(plain).hexdigest() != response["_expect_sha"]:
                    raise ServiceFailure("agent plaintext does not match its digest")
                response.pop("_expect_plain", None)
                response.pop("_expect_sha", None)
            return response, plain
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


def _read_exact_socket(conn: socket.socket, nbytes: int, prefix: bytes = b"") -> bytes:
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
    if argv[:1] == ["--serve"]:
        return serve(argv[1] if len(argv) > 1 else SOCKET_PATH)
    sys.stderr.write("usage: fingerlock_agent.py --serve [SOCKET]\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
