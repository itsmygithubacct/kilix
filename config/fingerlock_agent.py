#!/usr/bin/env python3
"""kilix-fingerlock-agent: the root custodian that turns a fingerprint match
into a key release.

fprintd answers "match" or "no match"; it never yields key material. So the
only honest way a fingerprint can unlock anything is for a privileged process
to hold the key and release it after a match. This agent is that process:

  deposit  a user proves the passphrase once per boot (the verb derives the
           locker private key) and hands it to the agent, which keeps it in
           memory only, keyed by uid, until forget, TTL expiry or exit.
  release  the same uid asks for it back; the agent verifies a finger of that
           user through fprintd (as root it may claim the reader for any user),
           and returns the key only on `verify-match`.
  forget   drop the key: everything is locked until the passphrase again.
  status   whether a key is held for the caller, and for how much longer.

Callers are identified by SO_PEERCRED on the unix socket, and must belong to
an active seat session by logind, so an SSH login cannot pull the key even as
the same user. The socket is systemd-activated (`kilix-fingerlock-agent.socket`)
and the service runs hardened, without core dumps.

What this protects against: another local process of the same user reading
your locked files without you touching the sensor; a stolen laptop's disk
(the passphrase is the root of trust; the private key is not on disk); an SSH
session or cron job unlocking anything. What it does not protect against:
root, the kernel, or someone who reads this process's memory. That is the
same boundary fprintd itself has.

Protocol: one JSON object per line, request then response, over the socket
`/run/kilix-fingerlock/agent.sock`. Keys travel as hex; the socket is local
and the peer is root.
"""

from __future__ import annotations

import ctypes
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fingerlock_core import (  # noqa: E402
    ANY_FINGER, AccessDenied, DependencyUnavailable, DeviceNotFound, FingerlockError,
    FprintdClient, NoMatch, OperationFailed, OperationTimeout, ServiceFailure, _read,
)

SOCKET_PATH = "/run/kilix-fingerlock/agent.sock"
INSTALL_DIR = "/usr/local/lib/kilix-fingerlock"
SOCKET_UNIT = "kilix-fingerlock-agent.socket"
SERVICE_UNIT = "kilix-fingerlock-agent.service"
PROTOCOL = 1
DEFAULT_TTL_SECONDS = 12 * 3600
MAX_TTL_SECONDS = 7 * 24 * 3600
VERIFY_TIMEOUT_SECONDS = 45.0
MAX_LINE = 64 * 1024
NO_SESSION_ID = "4294967295"

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


# --- entries ------------------------------------------------------------------------

@dataclass
class Entry:
    key: bytes
    locker_id: str
    deposited: float
    expires: float
    username: str


class Custody:
    """In-memory per-uid keys. Pure state, so it is testable without a socket."""

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._entries: dict[int, Entry] = {}
        self._lock = threading.Lock()
        self._now = now

    def _expire(self) -> None:
        now = self._now()
        for uid, entry in list(self._entries.items()):
            if entry.expires <= now:
                self._wipe(uid)

    def _wipe(self, uid: int) -> None:
        entry = self._entries.pop(uid, None)
        if entry is not None:
            entry.key = b"\0" * len(entry.key)

    def deposit(self, uid: int, username: str, key: bytes, locker_id: str, ttl: float) -> Entry:
        ttl = max(60.0, min(float(ttl), MAX_TTL_SECONDS))
        with self._lock:
            self._wipe(uid)
            entry = Entry(key=key, locker_id=locker_id, deposited=self._now(),
                          expires=self._now() + ttl, username=username)
            self._entries[uid] = entry
            return entry

    def peek(self, uid: int) -> Entry | None:
        with self._lock:
            self._expire()
            return self._entries.get(uid)

    def forget(self, uid: int) -> bool:
        with self._lock:
            had = uid in self._entries
            self._wipe(uid)
            return had

    def forget_all(self) -> None:
        with self._lock:
            for uid in list(self._entries):
                self._wipe(uid)


# --- session gating ------------------------------------------------------------------

def session_of_pid(pid: int, *, proc: Path = Path("/proc")) -> str:
    session = _read(proc / str(pid) / "sessionid") or ""
    return "" if session == NO_SESSION_ID else session


def session_is_active_seat(session_id: str, *, run: Callable[..., Any] = subprocess.run,
                           sessions_dir: Path = Path("/run/systemd/sessions")) -> tuple[bool, str]:
    """logind's own record first; loginctl as the fallback."""
    if not session_id:
        return False, "no logind session"
    record = _read(sessions_dir / session_id)
    if record:
        facts = dict(line.split("=", 1) for line in record.splitlines() if "=" in line)
        active = facts.get("ACTIVE") == "1" or facts.get("STATE") == "active"
        seat = facts.get("SEAT", "")
        return (active and bool(seat)), f"session {session_id} active={active} seat={seat or '-'}"
    try:
        proc = run(["loginctl", "show-session", session_id, "-p", "Active", "-p", "Seat"],
                   capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"loginctl unavailable ({exc.__class__.__name__})"
    if proc.returncode != 0:
        return False, f"loginctl cannot describe session {session_id}"
    facts = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    seat = facts.get("Seat", "")
    return (facts.get("Active") == "yes" and bool(seat)), f"session {session_id} Active={facts.get('Active')} Seat={seat or '-'}"


# --- the server ---------------------------------------------------------------------------

class Agent:
    def __init__(self, *, custody: Custody | None = None,
                 client_factory: Callable[[], FprintdClient] = FprintdClient,
                 verify_timeout: float = VERIFY_TIMEOUT_SECONDS,
                 proc: Path = Path("/proc"),
                 session_check: Callable[[str], tuple[bool, str]] = session_is_active_seat) -> None:
        self.custody = custody or Custody()
        self.client_factory = client_factory
        self.verify_timeout = verify_timeout
        self.proc = proc
        self.session_check = session_check
        self._verify_lock = threading.Lock()

    # -- one request -------------------------------------------------------------

    def handle(self, request: dict[str, Any], uid: int, pid: int) -> dict[str, Any]:
        op = request.get("op")
        try:
            if request.get("protocol") != PROTOCOL:
                raise ServiceFailure(f"unsupported protocol {request.get('protocol')!r}")
            if op == "status":
                return self._status(uid)
            if op == "forget":
                return {"ok": True, "forgotten": self.custody.forget(uid)}
            active, detail = self.session_check(session_of_pid(pid, proc=self.proc))
            if not active:
                raise AccessDenied(f"caller is not in an active seat session ({detail})")
            if op == "deposit":
                return self._deposit(uid, request)
            if op == "release":
                return self._release(uid, request)
            raise ServiceFailure(f"unknown op {op!r}")
        except FingerlockError as error:
            return {"ok": False, "code": error.code, "error": error.message, "exit": error.exit_status}

    def _status(self, uid: int) -> dict[str, Any]:
        entry = self.custody.peek(uid)
        if entry is None:
            return {"ok": True, "held": False}
        remaining = max(0, int(entry.expires - self.custody._now()))
        return {"ok": True, "held": True, "locker_id": entry.locker_id, "expires_in": remaining}

    def _deposit(self, uid: int, request: dict[str, Any]) -> dict[str, Any]:
        try:
            key = bytes.fromhex(str(request["key"]))
            locker_id = str(request["locker_id"])
            ttl = float(request.get("ttl", DEFAULT_TTL_SECONDS))
        except (KeyError, ValueError, TypeError) as exc:
            raise ServiceFailure(f"bad deposit: {exc}") from exc
        if len(key) != 32:
            raise ServiceFailure("bad deposit: key must be 32 bytes")
        try:
            username = pwd.getpwuid(uid).pw_name
        except KeyError as exc:
            raise AccessDenied(f"unknown uid {uid}") from exc
        entry = self.custody.deposit(uid, username, key, locker_id, ttl)
        return {"ok": True, "held": True, "locker_id": locker_id,
                "expires_in": int(entry.expires - self.custody._now())}

    def _release(self, uid: int, request: dict[str, Any]) -> dict[str, Any]:
        entry = self.custody.peek(uid)
        if entry is None:
            raise AccessDenied("no key deposited for this user; unlock with the passphrase first")
        wanted = str(request.get("locker_id", entry.locker_id))
        if wanted != entry.locker_id:
            raise AccessDenied("the deposited key belongs to a different locker")
        finger = str(request.get("finger", ANY_FINGER))
        if not self._verify_lock.acquire(timeout=self.verify_timeout):
            raise ServiceFailure("the reader is busy with another verification")
        try:
            client = self.client_factory()
            device = client.default_device()
            session = client.verify(device.path, finger, username=entry.username, timeout=self.verify_timeout)
        finally:
            self._verify_lock.release()
        if session.timed_out:
            raise OperationTimeout(f"no finger within {self.verify_timeout:g}s")
        if session.result != "verify-match":
            if session.result == "verify-no-match":
                raise NoMatch("no match")
            raise OperationFailed(f"verification ended with {session.result}")
        return {"ok": True, "key": entry.key.hex(), "locker_id": entry.locker_id,
                "finger": session.selected_finger, "device": device.name}

    # -- the socket loop -----------------------------------------------------------

    def serve(self, listener: socket.socket) -> None:
        listener.setblocking(True)
        while True:
            try:
                conn, _addr = listener.accept()
            except OSError:
                continue
            threading.Thread(target=self._serve_connection, args=(conn,), daemon=True).start()

    def _serve_connection(self, conn: socket.socket) -> None:
        with conn:
            try:
                creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                pid, uid, _gid = struct.unpack("3i", creds)
                conn.settimeout(self.verify_timeout + 30)
                line = _readline(conn)
                if line is None:
                    return
                try:
                    request = json.loads(line.decode("utf-8"))
                    if not isinstance(request, dict):
                        raise ValueError("not an object")
                except ValueError as exc:
                    response: dict[str, Any] = {"ok": False, "code": "ESERVICE", "error": f"bad request: {exc}", "exit": 6}
                else:
                    response = self.handle(request, uid, pid)
                conn.sendall(json.dumps(response, sort_keys=True).encode("utf-8") + b"\n")
            except OSError:
                return


def _readline(conn: socket.socket) -> bytes | None:
    buffer = bytearray()
    while len(buffer) < MAX_LINE:
        chunk = conn.recv(4096)
        if not chunk:
            return bytes(buffer) if buffer else None
        buffer += chunk
        if b"\n" in buffer:
            line, _sep, _rest = bytes(buffer).partition(b"\n")
            return line
    return None


def _harden_process() -> None:
    """No core dumps and no ptrace of this process."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4
    except (OSError, AttributeError):
        pass
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass


def _listener_from_systemd_or_path(path: str) -> socket.socket:
    fds = int(os.environ.get("LISTEN_FDS", "0") or 0)
    if fds >= 1 and os.environ.get("LISTEN_PID", "") == str(os.getpid()):
        return socket.socket(fileno=3)
    Path(path).parent.mkdir(mode=0o755, parents=True, exist_ok=True)
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
    _harden_process()
    listener = _listener_from_systemd_or_path(path)
    Agent().serve(listener)
    return 0


# --- client -----------------------------------------------------------------------------

class AgentClient:
    """The verb's side of the socket."""

    def __init__(self, path: str = SOCKET_PATH, *, timeout: float = VERIFY_TIMEOUT_SECONDS + 30) -> None:
        self.path = path
        self.timeout = timeout

    def available(self) -> bool:
        return os.path.exists(self.path)

    def call(self, op: str, **fields: Any) -> dict[str, Any]:
        request = {"protocol": PROTOCOL, "op": op, **fields}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self.timeout)
                conn.connect(self.path)
                conn.sendall(json.dumps(request).encode("utf-8") + b"\n")
                line = _readline(conn)
        except (OSError, socket.timeout) as exc:
            raise AgentUnavailable(
                f"fingerlock agent not reachable at {self.path} ({exc.__class__.__name__}); "
                "install it with: kilix fingerlock install --agent") from exc
        if line is None:
            raise AgentUnavailable("fingerlock agent closed the connection")
        try:
            response = json.loads(line.decode("utf-8"))
        except ValueError as exc:
            raise ServiceFailure(f"bad reply from the fingerlock agent: {exc}") from exc
        if not response.get("ok"):
            cls = {"EACCESS": AccessDenied, "ENOMATCH": NoMatch, "ETIMEOUT": OperationTimeout,
                   "EOPERATION": OperationFailed, "ENODEVICE": DeviceNotFound,
                   "EDEPENDENCY": DependencyUnavailable}.get(str(response.get("code")), ServiceFailure)
            raise cls(str(response.get("error", "agent refused")))
        return response

    def status(self) -> dict[str, Any]:
        return self.call("status")

    def deposit(self, key: bytes, locker_id: str, ttl: float = DEFAULT_TTL_SECONDS) -> dict[str, Any]:
        return self.call("deposit", key=key.hex(), locker_id=locker_id, ttl=ttl)

    def release(self, locker_id: str, finger: str = ANY_FINGER) -> bytes:
        response = self.call("release", locker_id=locker_id, finger=finger)
        return bytes.fromhex(str(response["key"]))

    def forget(self) -> bool:
        return bool(self.call("forget").get("forgotten"))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--serve"]:
        return serve(argv[1] if len(argv) > 1 else SOCKET_PATH)
    sys.stderr.write("usage: fingerlock_agent.py --serve [SOCKET]\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
