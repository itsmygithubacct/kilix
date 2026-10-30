"""Hash-bound Fingerlock packages and a journaled, versioned root installation.

The caller builds and confirms one byte string. The isolated root interpreter
checks its SHA-256 before executing any package code or writing files. An
installation switches a single current symlink while both units are stopped.
A durable journal restores the previous units and their activation state after
failure or an interrupted transaction. Package installation is a separate step.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import socket
import stat
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

SCHEMA = "kilix.fingerlock.package/v1"
MAX_PACKAGE = 2 * 1024 * 1024
MODULES = ("fingerlock_core.py", "fingerlock_locker.py", "fingerlock_agent.py",
           "fingerlock_policy.py", "fingerlock_install.py")
POLICY = "org.kilix.fingerlock.unseal.policy"
STORE = Path("usr/local/lib/kilix-fingerlock")
SHA = re.compile(r"[0-9a-f]{64}")

# Kept on argv, independent of all mutable source paths. The digest is the
# value displayed before consent, and stdin supplies only those exact bytes.
BOOTSTRAP = '''import base64, hashlib, json, os, sys
if os.geteuid() != 0: raise SystemExit("root is required")
data = sys.stdin.buffer.read(2097153)
if len(data) > 2097152 or hashlib.sha256(data).hexdigest() != sys.argv[1]:
    raise SystemExit("Fingerlock package digest mismatch")
package = json.loads(data)
code = base64.b64decode(package["files"]["fingerlock_install.py"], validate=True)
scope = {"__name__": "fingerlock_verified_installer"}
exec(compile(code, "<verified Fingerlock installer>", "exec"), scope)
scope["privileged_main"](data, int(sys.argv[2]), sys.argv[3])
'''


class InstallError(RuntimeError):
    pass


def unit_names(uid: int) -> tuple[str, str]:
    return (f"kilix-fingerlock-agent@{uid}.socket", f"kilix-fingerlock-agent@{uid}.service")


def unit_texts(uid: int) -> tuple[str, str]:
    sock, _service = unit_names(uid)
    current = f"/{STORE}/users/{uid}/current"
    return (f"""[Unit]
Description=Fingerlock socket for user {uid}
[Socket]
ListenStream=/run/kilix-fingerlock/{uid}.sock
SocketUser={uid}
SocketMode=0600
DirectoryMode=0755
RemoveOnStop=yes
[Install]
WantedBy=sockets.target
""", f"""[Unit]
Description=Fingerlock custodian for user {uid}
Requires={sock}
After={sock} dbus.service
[Service]
ExecStart=/usr/bin/python3 -I -B {current}/fingerlock_agent.py --serve {uid}
LimitCORE=0
LimitNOFILE=128
TasksMax=32
MemoryMax=512M
UMask=0077
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
RestrictSUIDSGID=yes
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
TimeoutStopSec=10
""")


def _uid(uid: int) -> None:
    if type(uid) is not int or not 0 < uid < 2**31:
        raise InstallError("a non-root user uid is required")


def build_package(source: Path, uid: int, policy: str) -> bytes:
    _uid(uid)
    files = {}
    for name in MODULES:
        fd = os.open(source / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InstallError("package sources must be regular files")
            data = stream.read(MAX_PACKAGE + 1)
        if len(data) > MAX_PACKAGE:
            raise InstallError("package source is too large")
        files[name] = data
    files[POLICY] = policy.encode("utf-8")
    files.update({name: text.encode() for name, text in zip(unit_names(uid), unit_texts(uid))})
    raw = json.dumps({"schema": SCHEMA, "uid": uid,
                      "files": {k: base64.b64encode(v).decode("ascii") for k, v in files.items()}},
                     sort_keys=True, separators=(",", ":")).encode()
    decode_package(raw, uid)
    return raw


def decode_package(raw: bytes, uid: int) -> dict[str, bytes]:
    _uid(uid)
    if len(raw) > MAX_PACKAGE:
        raise InstallError("package is too large")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InstallError("duplicate package field")
            result[key] = value
        return result
    try:
        data = json.loads(raw, object_pairs_hook=unique)
        if (set(data) != {"schema", "uid", "files"} or data["schema"] != SCHEMA
                or type(data["uid"]) is not int or data["uid"] != uid
                or set(data["files"]) != set((*MODULES, POLICY, *unit_names(uid)))):
            raise InstallError("unexpected package contents or owner")
        return {k: base64.b64decode(v, validate=True) for k, v in data["files"].items()}
    except (ValueError, TypeError, KeyError) as exc:
        raise InstallError("invalid package") from exc


def install_argv(raw: bytes, uid: int, *, rollback: bool = False) -> list[str]:
    return ["/usr/bin/python3", "-I", "-c", BOOTSTRAP, hashlib.sha256(raw).hexdigest(), str(uid),
            "rollback" if rollback else "install"]


class Installer:
    def __init__(self, root: Path = Path("/"), *, owner: int = 0,
                 run: Callable = subprocess.run, health: Callable | None = None) -> None:
        # root/owner/runner injection is for temporary-tree tests. The privileged
        # entry point below always uses the real filesystem and root ownership.
        self.root, self.owner, self.run = root, owner, run
        self.health = health or self._health

    def _check(self, path: Path, *, directory: bool = False) -> None:
        info = path.lstat()
        kind = stat.S_ISDIR if directory else stat.S_ISREG
        if not kind(info.st_mode) or info.st_uid != self.owner or info.st_mode & 0o022:
            raise InstallError(f"unsafe installation path: {path}")
        if not directory and info.st_nlink != 1:
            raise InstallError(f"installation file has hard links: {path}")

    def _directory(self, relative: Path | str) -> Path:
        path = self.root
        self._check(path, directory=True)
        for part in Path(relative).parts:
            if part in ("/", ".", ".."):
                raise InstallError("invalid installation directory")
            path /= part
            try:
                path.mkdir(mode=0o755)
                self._sync(path.parent)
            except FileExistsError:
                pass
            self._check(path, directory=True)
        return path

    @staticmethod
    def _sync(directory: Path) -> None:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write(self, path: Path, data: bytes, mode: int = 0o644) -> None:
        if path.exists() or path.is_symlink():
            self._check(path)
        fd, temporary = tempfile.mkstemp(prefix=".fingerlock-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._sync(path.parent)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)

    def _link(self, path: Path, target: str | None) -> None:
        if os.path.lexists(path):
            info = path.lstat()
            if not stat.S_ISLNK(info.st_mode) or info.st_uid != self.owner:
                raise InstallError(f"unmanaged installation path: {path}")
        if target is None:
            path.unlink(missing_ok=True)
        else:
            temporary = path.with_name(".fingerlock-" + os.urandom(12).hex())
            try:
                temporary.symlink_to(target)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        self._sync(path.parent)

    def _target(self, path: Path) -> str | None:
        if not os.path.lexists(path):
            return None
        info = path.lstat()
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != self.owner:
            raise InstallError(f"unmanaged version pointer: {path}")
        target = os.readlink(path)
        if not target.startswith("../../versions/") or not SHA.fullmatch(target[15:]):
            raise InstallError("invalid version pointer")
        return target[15:]

    def _stored(self, digest: str, uid: int) -> bytes:
        if not SHA.fullmatch(digest):
            raise InstallError("invalid package digest")
        directory = self.root / STORE / "versions" / digest
        self._check(directory, directory=True)
        self._check(directory / ".bundle")
        raw = (directory / ".bundle").read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise InstallError("stored package digest mismatch")
        files = decode_package(raw, uid)
        if {p.name for p in directory.iterdir()} != set(files) | {".bundle"}:
            raise InstallError("unexpected file in stored version")
        for name, data in files.items():
            self._check(directory / name)
            if (directory / name).read_bytes() != data:
                raise InstallError("stored version differs from its package")
        return raw

    def _store(self, raw: bytes, uid: int) -> str:
        files = decode_package(raw, uid)
        digest = hashlib.sha256(raw).hexdigest()
        versions = self._directory(STORE / "versions")
        if (versions / digest).exists() or (versions / digest).is_symlink():
            self._stored(digest, uid)
            return digest
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=versions))
        for name, data in {**files, ".bundle": raw}.items():
            self._write(stage / name, data, 0o444)
        stage.chmod(0o555)
        self._sync(stage)
        stage.rename(versions / digest)
        self._sync(versions)
        return digest

    def _systemctl(self, *args: str) -> str:
        result = self.run(["/usr/bin/systemctl", *args], capture_output=True, text=True,
                          timeout=30, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        if result.returncode:
            raise InstallError(f"systemctl {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def _health(self, uid: int) -> None:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            conn.settimeout(5)
            conn.connect(str(self.root / f"run/kilix-fingerlock/{uid}.sock"))
            _pid, owner, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if owner != 0:
                raise InstallError("health check reached an untrusted peer")
            conn.sendall(b'{"protocol":1,"op":"health"}\n')
            reply = bytearray()
            while b"\n" not in reply and len(reply) < 4096:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                reply.extend(chunk)
            result = json.loads(reply.partition(b"\n")[0])
            if result != {"ok": True, "uid": uid, "protocol": 1}:
                raise InstallError("custodian health check failed")
        finally:
            conn.close()

    def _restore(self, uid: int, user: Path, units: Path, journal: dict) -> None:
        if (set(journal) != {"schema", "before", "previous", "enabled", "socket_active", "service_active"}
                or journal["schema"] != "kilix.fingerlock.transaction/v1"
                or any(value is not None and (not isinstance(value, str) or not SHA.fullmatch(value))
                       for value in (journal["before"], journal["previous"]))
                or any(type(journal[key]) is not bool for key in ("enabled", "socket_active", "service_active"))):
            raise InstallError("invalid recovery journal; manual review required")
        sock, service = unit_names(uid)
        # A crash can precede creation/reload of either unit. Do not make a
        # never-installed unit prevent recovery of that first transaction.
        loaded = [name for name in (sock, service)
                  if self._systemctl("show", name, "-p", "LoadState", "--value") != "not-found"]
        if loaded:
            self._systemctl("stop", *loaded)
        before = journal["before"]
        if before is not None:
            self._stored(before, uid)
        for name, digest in (("current", before), ("previous", journal["previous"])):
            self._link(user / name, f"../../versions/{digest}" if digest else None)
        if before is None:
            if os.path.lexists(units / sock):
                self._systemctl("disable", sock)
            for name in (sock, service):
                self._link(units / name, None)
        self._systemctl("daemon-reload")
        if before is not None:
            self._systemctl("enable" if journal["enabled"] else "disable", sock)
            if journal["socket_active"]:
                self._systemctl("start", sock)
            if journal["service_active"]:
                self._systemctl("start", service)
                self.health(uid)
        (user / "pending.json").unlink()
        self._sync(user)

    def apply(self, raw: bytes, uid: int, *, rollback: bool = False) -> str:
        decode_package(raw, uid)
        runtime = self._directory("run/kilix-fingerlock")
        fd = os.open(runtime / "install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            self._check(runtime / "install.lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self._apply_locked(raw, uid, rollback)
        finally:
            os.close(fd)

    def _apply_locked(self, raw: bytes, uid: int, rollback: bool) -> str:
        user = self._directory(STORE / "users" / str(uid))
        units = self._directory("etc/systemd/system")
        policy_dir = self._directory("usr/share/polkit-1/actions")
        sock, service = unit_names(uid)
        for base in ("etc/systemd/system", "run/systemd/system", "usr/lib/systemd/system"):
            for name in ("kilix-fingerlock-agent.socket", "kilix-fingerlock-agent.service"):
                if os.path.lexists(self.root / base / name):
                    raise InstallError("review the legacy shared agent before installing per-user custody")
        if os.path.lexists(self.root / "run/kilix-fingerlock/agent.sock"):
            raise InstallError("the legacy agent socket is still present")
        links = {name: f"/{STORE}/users/{uid}/current/{name}" for name in (sock, service)}
        for name, target in links.items():
            path = units / name
            if os.path.lexists(path) and (not path.is_symlink() or os.readlink(path) != target):
                raise InstallError("refusing to replace unmanaged units")
            if os.path.lexists(path) and path.lstat().st_uid != self.owner:
                raise InstallError("unit link is not root-owned")
            # A drop-in could change ExecStart or the socket access mode.
            for base in ("etc/systemd/system", "run/systemd/system", "usr/lib/systemd/system"):
                for candidate in (name, name.replace("@" + str(uid), "@"),
                                  "service" if name.endswith(".service") else "socket"):
                    if os.path.lexists(self.root / base / (candidate + ".d")):
                        raise InstallError("review systemd drop-ins before installing Fingerlock")
        pending = user / "pending.json"
        if os.path.lexists(pending):
            self._check(pending)
            self._restore(uid, user, units, json.loads(pending.read_text()))
        before, previous = self._target(user / "current"), self._target(user / "previous")
        for name in (sock, service):
            if before is None and self._systemctl("show", name, "-p", "LoadState", "--value") != "not-found":
                raise InstallError("refusing to replace an existing unmanaged systemd unit")
            if self._systemctl("show", name, "-p", "DropInPaths", "--value"):
                raise InstallError("effective systemd drop-ins require review")
        if before:
            self._stored(before, uid)
        if rollback:
            if previous is None:
                raise InstallError("no previous Fingerlock version")
            raw = self._stored(previous, uid)
        files = decode_package(raw, uid)
        policy_path = policy_dir / POLICY
        if os.path.lexists(policy_path):
            self._check(policy_path)
            if policy_path.read_bytes() != files[POLICY]:
                raise InstallError("installed polkit policy differs; review it before upgrading")
        check = self.run(["/usr/bin/python3", "-I", "-c", "import nacl.bindings, dbus, gi"],
                         capture_output=True, text=True, timeout=10)
        if check.returncode:
            raise InstallError("install python3-nacl, python3-dbus and python3-gi first")
        digest = self._store(raw, uid)
        if before == digest:
            self.health(uid)
            return digest
        journal = {"schema": "kilix.fingerlock.transaction/v1", "before": before, "previous": previous, "enabled": False,
                   "socket_active": False, "service_active": False}
        if before:
            journal["enabled"] = self._systemctl("show", sock, "-p", "UnitFileState", "--value") == "enabled"
            journal["socket_active"] = self._systemctl("show", sock, "-p", "ActiveState", "--value") == "active"
            journal["service_active"] = self._systemctl("show", service, "-p", "ActiveState", "--value") == "active"
        self._write(pending, json.dumps(journal, sort_keys=True).encode(), 0o600)
        try:
            if before:
                self._systemctl("stop", sock, service)
            if not policy_path.exists():
                self._write(policy_path, files[POLICY])
            self._link(user / "current", f"../../versions/{digest}")
            for name, target in links.items():
                self._link(units / name, target)
            self._systemctl("daemon-reload")
            for name in (sock, service):
                if self._systemctl("show", name, "-p", "DropInPaths", "--value"):
                    raise InstallError("effective systemd drop-ins require review")
            self._systemctl("enable", "--now", sock)
            self._systemctl("start", service)
            self.health(uid)
            self._link(user / "previous", f"../../versions/{before}" if before else None)
            pending.unlink()
            self._sync(user)
        except Exception as exc:
            try:
                self._restore(uid, user, units, journal)
            except Exception as recovery:
                raise InstallError(f"activation failed ({exc}); recovery incomplete ({recovery}); journal retained") from exc
            raise InstallError(f"activation failed; previous installation restored: {exc}") from exc
        return digest


def privileged_main(raw: bytes, uid: int, operation: str) -> None:
    if os.geteuid() != 0 or operation not in ("install", "rollback"):
        raise SystemExit("invalid privileged installation request")
    try:
        digest = Installer().apply(raw, uid, rollback=operation == "rollback")
    except Exception as exc:
        raise SystemExit(f"Fingerlock install: {exc}") from exc
    print(json.dumps({"ok": True, "sha256": digest, "uid": uid, "operation": operation}))
