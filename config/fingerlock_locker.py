#!/usr/bin/env python3
"""The fingerlock locker: passphrase-rooted keys, sealed file containers and
named passwords, on libsodium through python3-nacl.

KEYS. A locker has one X25519 keypair made from a random 32-byte seed. The
seed is stored in locker.json wrapped (XSalsa20-Poly1305 secret box) under a
key derived from the owner's passphrase with Argon2id. The public key is
stored beside that wrap. It is not a trusted recipient by itself: sealing
checks it against the wrapped seed (passphrase) or against custody the agent
already holds, and refuses before it creates a container or removes a source.
A passphrase change only rewraps the same seed. It does not rotate the key
and it does not revoke plaintext already returned to a caller.

CONTAINERS (`*.flk`). One line of magic and JSON header, then chunks:

  FLK1 {json}\\n
  chunk*   each chunk = XChaCha20-Poly1305-IETF(file_key, nonce_i, AD_i, data)

The header carries the locker id, the sealed per-file key (SealedBox to the
verified locker public key), the original name, size, mtime and the chunk
size. The file key is random per container. Each chunk's nonce is the
header's base nonce with the chunk index in its last eight bytes, and its
associated data binds the header digest, the index and a final flag.

Stdout and other sinks receive plaintext only after the whole container has
authenticated. That path is not a streaming decrypt. Unlink removes one
directory entry after the replacement is durable; it is not secure deletion.
Plaintext already returned stays exposed.

This format uses the same primitives the F112 vault chose (Argon2id,
XChaCha20-Poly1305, libsodium).
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import stat
import struct
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fingerlock_core import DependencyUnavailable, FingerlockError, UsageError  # noqa: E402

MAGIC = b"FLK1 "
SUFFIX = ".flk"
LOCKER_VERSION = 1
CHUNK_SIZE = 4 * 1024 * 1024
MIN_CHUNK = 1024
MAX_CHUNK = CHUNK_SIZE
MAX_PLAINTEXT = 64 * 1024 * 1024
MAX_CHUNKS = 65536
MAX_HEADER = 64 * 1024
MAX_SECRET = 64 * 1024
TAG_BYTES = 16
PUBLIC_KEY_BYTES = 32
SEED_BYTES = 32
NONCE_BYTES = 24
SALT_BYTES = 16
SEALED_KEY_BYTES = 80  # 32-byte file key plus libsodium sealed-box overhead
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}$")
LOCKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,80}$")
_HEX = re.compile(r"^[0-9a-f]+$")
LOCKER_PACKAGES: tuple[str, ...] = ("python3-nacl",)
KDF_POLICIES = ("production", "test")


class LockerError(FingerlockError):
    code, exit_status = "ELOCKER", 10


class WrongPassphrase(FingerlockError):
    code, exit_status = "EPASSPHRASE", 11


def sodium() -> Any:
    """python3-nacl, or a typed dependency error naming the package."""
    try:
        import nacl.bindings
        import nacl.public
        import nacl.pwhash
        import nacl.secret
        import nacl.utils
    except ImportError as exc:
        raise DependencyUnavailable(
            f"python3-nacl is required for locking ({exc}); run: kilix fingerlock install"
        ) from exc
    return nacl


def default_home() -> Path:
    override = os.environ.get("KILIX_FINGERLOCK_HOME", "")
    if override:
        if not os.path.isabs(override):
            raise UsageError("KILIX_FINGERLOCK_HOME must be an absolute path")
        return Path(override)
    return Path(os.path.expanduser("~/.local/gpu_terminal/fingerlock"))


def wipe_buffer(buf: bytearray | None) -> None:
    """Overwrite a mutable buffer. This does not erase other copies."""
    if not buf:
        return
    for index in range(len(buf)):
        buf[index] = 0


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        wrote = os.write(fd, view)
        if wrote <= 0:
            raise LockerError("short write to a locker file")
        view = view[wrote:]


def _read_exact_fd(fd: int, size: int) -> bytes:
    if size < 0:
        raise LockerError("refusing a negative read")
    buf = bytearray()
    while len(buf) < size:
        chunk = os.read(fd, min(1 << 20, size - len(buf)))
        if not chunk:
            break
        buf += chunk
    if len(buf) != size:
        raise LockerError("short read")
    return bytes(buf)


def _as_int(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LockerError(f"{name} must be an integer")
    if value < lo or value > hi:
        raise LockerError(f"{name} is outside {lo}..{hi}")
    return value


def _as_text(value: Any, name: str, pattern: re.Pattern[str] | None = None, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise LockerError(f"{name} is missing or too long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise LockerError(f"{name} contains a control character")
    if pattern is not None and not pattern.fullmatch(value):
        raise LockerError(f"{name} is not an accepted value")
    return value


def _as_hex(value: Any, name: str, size: int) -> bytes:
    if not isinstance(value, str) or len(value) != size * 2 or not _HEX.fullmatch(value):
        raise LockerError(f"{name} must be {size} bytes of lowercase hex")
    return bytes.fromhex(value)


def kdf_bounds(policy: str) -> tuple[int, int, int, int]:
    """Inclusive ops and memory bounds. Production limits are libsodium's presets.

    Test policy still rejects negative, boolean and enormous costs. It allows
    the Argon2id minimums so unit tests can avoid the production memory cost.
    """
    if policy not in KDF_POLICIES:
        raise UsageError(f"unknown kdf policy {policy!r}")
    nacl = sodium()
    argon = nacl.pwhash.argon2id
    hi_ops = int(argon.OPSLIMIT_SENSITIVE)
    hi_mem = int(argon.MEMLIMIT_SENSITIVE)
    if policy == "production":
        return int(argon.OPSLIMIT_INTERACTIVE), hi_ops, int(argon.MEMLIMIT_INTERACTIVE), hi_mem
    return 1, hi_ops, 8192, hi_mem


def validate_kdf(opslimit: Any, memlimit: Any, *, policy: str) -> tuple[int, int]:
    lo_ops, hi_ops, lo_mem, hi_mem = kdf_bounds(policy)
    ops = _as_int(opslimit, "kdf opslimit", lo_ops, hi_ops)
    mem = _as_int(memlimit, "kdf memlimit", lo_mem, hi_mem)
    # _as_int already enforced the bounds; keep the policy ends visible to callers.
    if ops < lo_ops or mem < lo_mem or ops > hi_ops or mem > hi_mem:
        raise LockerError(f"kdf parameters are outside the {policy} policy")
    return ops, mem


@dataclass(frozen=True)
class LockerInfo:
    locker_id: str
    created: str
    salt: bytes
    opslimit: int
    memlimit: int
    public_key: bytes
    wrapped_seed: bytes

    def as_dict(self) -> dict[str, Any]:
        return {"version": LOCKER_VERSION, "locker_id": self.locker_id, "created": self.created,
                "kdf": {"name": "argon2id", "salt": self.salt.hex(),
                        "opslimit": self.opslimit, "memlimit": self.memlimit},
                "public_key": self.public_key.hex(), "wrapped_seed": self.wrapped_seed.hex()}

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, policy: str = "production") -> "LockerInfo":
        nacl = sodium()
        try:
            if not isinstance(data, dict):
                raise LockerError("locker.json is not an object")
            if isinstance(data.get("version"), bool) or data.get("version") != LOCKER_VERSION:
                raise LockerError(f"unsupported locker version {data.get('version')!r}")
            kdf = data["kdf"]
            if not isinstance(kdf, dict) or kdf.get("name") != "argon2id":
                raise LockerError("unsupported kdf")
            opslimit, memlimit = validate_kdf(kdf.get("opslimit"), kdf.get("memlimit"), policy=policy)
            wrapped = _as_hex(data.get("wrapped_seed"), "wrapped_seed",
                              nacl.bindings.crypto_secretbox_NONCEBYTES + SEED_BYTES + nacl.bindings.crypto_secretbox_MACBYTES)
            return cls(locker_id=_as_text(data.get("locker_id"), "locker_id", LOCKER_ID_PATTERN, maximum=81),
                       created=_as_text(data.get("created"), "created", maximum=64),
                       salt=_as_hex(kdf.get("salt"), "kdf salt", nacl.pwhash.argon2id.SALTBYTES),
                       opslimit=opslimit, memlimit=memlimit,
                       public_key=_as_hex(data.get("public_key"), "public_key", PUBLIC_KEY_BYTES),
                       wrapped_seed=wrapped)
        except (KeyError, TypeError, ValueError) as exc:
            raise LockerError(f"locker.json is not valid: {exc}") from exc


KDF_PRESETS = {"interactive": ("OPSLIMIT_INTERACTIVE", "MEMLIMIT_INTERACTIVE"),
               "moderate": ("OPSLIMIT_MODERATE", "MEMLIMIT_MODERATE"),
               "sensitive": ("OPSLIMIT_SENSITIVE", "MEMLIMIT_SENSITIVE")}


def kdf_limits(preset: str) -> tuple[int, int]:
    nacl = sodium()
    try:
        ops, mem = KDF_PRESETS[preset]
    except KeyError as exc:
        raise UsageError(f"unknown kdf preset {preset!r}; one of " + ", ".join(KDF_PRESETS)) from exc
    return getattr(nacl.pwhash.argon2id, ops), getattr(nacl.pwhash.argon2id, mem)


def _dir_fd(path: Path) -> int:
    try:
        if path.is_symlink():
            raise LockerError(f"{path} is a symlink")
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except LockerError:
        raise
    except OSError as exc:
        raise LockerError(f"cannot open directory {path}: {exc}") from exc


def _at(dir_fd: int, name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise LockerError("refusing a path component that is not a single name")
    return f"/proc/self/fd/{dir_fd}/{name}"


def _unique_file(dir_fd: int) -> tuple[int, str]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    for _ in range(16):
        name = ".flk-" + secrets.token_hex(16)
        try:
            fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            continue
        os.fchmod(fd, 0o600)
        return fd, name
    raise LockerError("could not create a private temporary file")


def _publish(tmp_at: str, final_at: str, *, overwrite: bool) -> None:
    try:
        existing = os.lstat(final_at)
    except FileNotFoundError:
        existing = None
    if existing is not None and stat.S_ISLNK(existing.st_mode):
        raise LockerError("refusing to replace a symlink")
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise LockerError("refusing to replace a non-regular file")
    if existing is not None and not overwrite:
        raise LockerError("destination already exists")
    if existing is not None and overwrite:
        os.replace(tmp_at, final_at)
        return
    try:
        os.link(tmp_at, final_at)
    except FileExistsError as exc:
        raise LockerError("destination already exists") from exc
    os.unlink(tmp_at)


def _create_exclusive(directory: Path, final_name: str, write_cb: Callable[[int], None], *,
                      overwrite: bool) -> None:
    """Write a new 0600 file and publish it only after fsync of the file and directory."""
    _private_dir(directory)
    dir_fd = _dir_fd(directory)
    tmp_fd: int | None = None
    tmp_name: str | None = None
    try:
        tmp_fd, tmp_name = _unique_file(dir_fd)
        write_cb(tmp_fd)
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        tmp_fd = None
        os.fsync(dir_fd)
        _publish(_at(dir_fd, tmp_name), _at(dir_fd, final_name), overwrite=overwrite)
        tmp_name = None
        os.fsync(dir_fd)
    except BaseException:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_name is not None:
            try:
                os.unlink(_at(dir_fd, tmp_name))
            except OSError:
                pass
        raise
    finally:
        os.close(dir_fd)


def _open_regular(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise LockerError(f"{path} is a symlink; refusing to follow it") from exc
        raise LockerError(f"cannot read {path}: {exc}") from exc
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise LockerError(f"{path} is not a regular file")
    return fd, st


def _unlink_same(path: Path, identity: tuple[int, int]) -> None:
    """Remove path only when it still names the same regular file."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise LockerError(f"{path} changed and was left in place")
    if (st.st_dev, st.st_ino) != identity:
        raise LockerError(f"{path} no longer names the sealed file and was left in place")
    os.unlink(path)


class Locker:
    """One user's locker: metadata on disk, keys only in memory."""

    def __init__(self, home: Path | None = None, *, kdf_policy: str = "production") -> None:
        if kdf_policy not in KDF_POLICIES:
            raise UsageError(f"unknown kdf policy {kdf_policy!r}")
        self.home = home or default_home()
        self.path = self.home / "locker.json"
        self.passwords_dir = self.home / "passwords"
        self.kdf_policy = kdf_policy

    def exists(self) -> bool:
        return self.path.is_file()

    def info(self) -> LockerInfo:
        if not self.exists():
            raise LockerError("no locker yet; run: kilix fingerlock init")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LockerError(f"cannot read {self.path}: {exc}") from exc
        return LockerInfo.from_dict(data, policy=self.kdf_policy)

    def _save(self, info: LockerInfo) -> None:
        payload = json.dumps(info.as_dict(), indent=2, sort_keys=True).encode() + b"\n"
        _create_exclusive(self.path.parent, self.path.name, lambda fd: _write_all(fd, payload), overwrite=True)

    @staticmethod
    def _keypair(seed: bytes) -> tuple[bytes, bytes]:
        nacl = sodium()
        private = nacl.public.PrivateKey(seed)
        return bytes(private), bytes(private.public_key)

    @staticmethod
    def _kek(passphrase: str, salt: bytes, opslimit: int, memlimit: int) -> bytes:
        nacl = sodium()
        if not passphrase:
            raise WrongPassphrase("empty passphrase")
        return nacl.pwhash.argon2id.kdf(nacl.secret.SecretBox.KEY_SIZE, passphrase.encode("utf-8"), salt,
                                        opslimit=opslimit, memlimit=memlimit)

    def unwrap_seed(self, passphrase: str, info: LockerInfo | None = None) -> bytes:
        nacl = sodium()
        info = info or self.info()
        kek = self._kek(passphrase, info.salt, info.opslimit, info.memlimit)
        try:
            return nacl.secret.SecretBox(kek).decrypt(info.wrapped_seed)
        except Exception as exc:
            raise WrongPassphrase("wrong passphrase") from exc

    def derive_private_key(self, passphrase: str, info: LockerInfo | None = None) -> bytes:
        """Passphrase -> X25519 private key. Refuses if locker.json does not match the seed."""
        info = info or self.info()
        private, public = self._keypair(self.unwrap_seed(passphrase, info))
        if public != info.public_key:
            raise LockerError("locker.json public key does not match the wrapped seed; nothing was sealed")
        return private

    @staticmethod
    def check_private_key(private: bytes, info: LockerInfo) -> bool:
        nacl = sodium()
        try:
            if not isinstance(private, (bytes, bytearray)) or len(private) != SEED_BYTES:
                return False
            return bytes(nacl.public.PrivateKey(bytes(private)).public_key) == info.public_key
        except Exception:
            return False

    def recipient_public(self, info: LockerInfo, *, private_key: bytes | None = None,
                         confirmed_public: bytes | None = None) -> bytes:
        """The public key to seal to, after a check that does not trust locker.json alone."""
        if private_key is not None:
            if not self.check_private_key(private_key, info):
                raise LockerError(
                    "locker.json public key does not match the verified private key; source preserved")
            return info.public_key
        if confirmed_public is not None:
            if confirmed_public != info.public_key or len(confirmed_public) != PUBLIC_KEY_BYTES:
                raise LockerError("custodian public key does not match locker.json; source preserved")
            return confirmed_public
        raise LockerError("sealing needs a verified recipient; source preserved")

    def init(self, passphrase: str, *, kdf: str = "moderate",
             opslimit: int | None = None, memlimit: int | None = None) -> LockerInfo:
        nacl = sodium()
        if self.exists():
            raise LockerError(f"a locker already exists at {self.path}")
        if len(passphrase) < 8:
            raise UsageError("the passphrase must be at least 8 characters")
        if opslimit is None or memlimit is None:
            opslimit, memlimit = kdf_limits(kdf)
        opslimit, memlimit = validate_kdf(opslimit, memlimit, policy=self.kdf_policy)
        salt = nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
        seed = nacl.utils.random(SEED_BYTES)
        wrapped = nacl.secret.SecretBox(self._kek(passphrase, salt, opslimit, memlimit)).encrypt(seed)
        _private, public = self._keypair(seed)
        info = LockerInfo(locker_id=str(uuid.uuid4()), created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          salt=salt, opslimit=opslimit, memlimit=memlimit, public_key=public,
                          wrapped_seed=bytes(wrapped))
        self._save(info)
        _private_dir(self.passwords_dir)
        return info

    def change_passphrase(self, old_passphrase: str, new_passphrase: str) -> LockerInfo:
        """Rewrap the same seed. Containers stay sealed to the same key.

        This does not rotate the X25519 key and does not revoke plaintext a
        caller already received. Custody in the agent is a separate forget.
        """
        nacl = sodium()
        old = self.info()
        seed = self.unwrap_seed(old_passphrase, old)
        _private, public = self._keypair(seed)
        if public != old.public_key:
            raise LockerError("locker.json public key does not match the wrapped seed; passphrase unchanged")
        if len(new_passphrase) < 8:
            raise UsageError("the passphrase must be at least 8 characters")
        salt = nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
        wrapped = nacl.secret.SecretBox(self._kek(new_passphrase, salt, old.opslimit, old.memlimit)).encrypt(seed)
        info = LockerInfo(locker_id=old.locker_id, created=old.created, salt=salt, opslimit=old.opslimit,
                          memlimit=old.memlimit, public_key=old.public_key, wrapped_seed=bytes(wrapped))
        self._save(info)
        return info

    def password_path(self, name: str) -> Path:
        if not NAME_PATTERN.match(name):
            raise UsageError("password names are letters, digits, and . _ @ + - (1 to 128 characters)")
        return self.passwords_dir / (name + SUFFIX)

    def password_names(self) -> list[str]:
        if not self.passwords_dir.is_dir():
            return []
        names = []
        for path in self.passwords_dir.iterdir():
            if path.name.startswith(".") or not path.name.endswith(SUFFIX):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            names.append(path.name[:-len(SUFFIX)])
        return sorted(names)

    def store_password(self, name: str, secret: bytes, *, replace: bool = False,
                       private_key: bytes | None = None,
                       confirmed_public: bytes | None = None) -> Path:
        """Encrypt a secret and only then replace any previous entry.

        The plaintext is not written to a staging file. A failure before the
        final publish leaves the previous ciphertext in place.
        """
        if not isinstance(secret, (bytes, bytearray)):
            raise UsageError("a password secret must be bytes")
        if len(secret) > MAX_SECRET:
            raise UsageError(f"a password secret must be at most {MAX_SECRET} bytes")
        info = self.info()
        public = self.recipient_public(info, private_key=private_key, confirmed_public=confirmed_public)
        path = self.password_path(name)
        if path.is_symlink():
            raise LockerError(f"{path} is a symlink; the existing entry was left in place")
        if path.exists() and not replace:
            raise UsageError(f"{name} exists (use --force to replace)")
        blob = encrypt_container(bytes(secret), info, public, name=name, chunk_size=MIN_CHUNK)
        _create_exclusive(path.parent, path.name, lambda fd: _write_all(fd, blob), overwrite=replace)
        return path

    def delete_password(self, name: str) -> None:
        path = self.password_path(name)
        try:
            st = os.lstat(path)
        except FileNotFoundError as exc:
            raise UsageError(f"no password named {name}") from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise LockerError(f"{path} is not a regular password entry; it was left in place")
        os.unlink(path)


def _header_digest(header: dict[str, Any]) -> bytes:
    canonical = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).digest()


def _chunk_nonce(base: bytes, index: int) -> bytes:
    return base[:16] + struct.pack(">Q", index)


def _chunk_ad(digest: bytes, index: int, final: bool) -> bytes:
    return digest + struct.pack(">Q?", index, final)


def _aead_header(header: dict[str, Any]) -> dict[str, Any]:
    """The header fields the chunks are bound to: everything but the seal."""
    return {k: v for k, v in header.items() if k != "sealed_key"}


def chunk_count(size: int, chunk_size: int) -> int:
    if size <= 0:
        return 1
    return (size + chunk_size - 1) // chunk_size


def expected_body_length(size: int, chunk_size: int) -> int:
    count = chunk_count(size, chunk_size)
    if count > MAX_CHUNKS:
        raise LockerError("container has too many chunks")
    if size <= 0:
        return TAG_BYTES
    full = count - 1
    last = size - full * chunk_size
    return full * (chunk_size + TAG_BYTES) + last + TAG_BYTES


def _validate_header_fields(header: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(header, dict):
        raise LockerError("container header is not an object")
    if isinstance(header.get("version"), bool) or header.get("version") != 1:
        raise LockerError(f"unsupported container version {header.get('version')!r}")
    chunk_size = _as_int(header.get("chunk_size"), "chunk_size", MIN_CHUNK, MAX_CHUNK)
    size = _as_int(header.get("size"), "size", 0, MAX_PLAINTEXT)
    if chunk_count(size, chunk_size) > MAX_CHUNKS:
        raise LockerError("container has too many chunks")
    nonce = _as_hex(header.get("nonce"), "nonce", NONCE_BYTES)
    sealed = _as_hex(header.get("sealed_key"), "sealed_key", SEALED_KEY_BYTES)
    name = _as_text(header.get("name"), "name", maximum=256)
    locker_id = _as_text(header.get("locker_id"), "locker_id", LOCKER_ID_PATTERN, maximum=81)
    checked = dict(header)
    checked["chunk_size"] = chunk_size
    checked["size"] = size
    checked["nonce"] = nonce.hex()
    checked["sealed_key"] = sealed.hex()
    checked["name"] = name
    checked["locker_id"] = locker_id
    if "mtime" in header and header["mtime"] is not None:
        checked["mtime"] = _as_int(header["mtime"], "mtime", 0, 2**62)
    return checked


def _parse_header_line(line: bytes) -> dict[str, Any]:
    if len(line) > MAX_HEADER or not line.startswith(MAGIC) or not line.endswith(b"\n"):
        raise LockerError("not a fingerlock container")
    try:
        header = json.loads(line[len(MAGIC):].decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise LockerError(f"container header is damaged: {exc}") from exc
    return _validate_header_fields(header)


def split_container(blob: bytes) -> tuple[dict[str, Any], bytes]:
    if not isinstance(blob, (bytes, bytearray)):
        raise LockerError("container is not bytes")
    if len(blob) > MAX_HEADER + MAX_PLAINTEXT + MAX_CHUNKS * TAG_BYTES:
        raise LockerError("container exceeds the size limit")
    newline = blob.find(b"\n", 0, MAX_HEADER)
    if newline < 0:
        raise LockerError("container header is missing or too long")
    header = _parse_header_line(bytes(blob[:newline + 1]))
    body = bytes(blob[newline + 1:])
    expect = expected_body_length(header["size"], header["chunk_size"])
    if len(body) != expect:
        raise LockerError("container length does not match its header")
    return header, body


def read_container_bytes(path: Path) -> tuple[dict[str, Any], bytes]:
    """Read one regular container, refusing symlinks and oversized files, then validate."""
    fd, st = _open_regular(path)
    try:
        if st.st_size > MAX_HEADER + MAX_PLAINTEXT + MAX_CHUNKS * TAG_BYTES:
            raise LockerError(f"{path} exceeds the container size limit")
        blob = _read_exact_fd(fd, st.st_size)
    finally:
        os.close(fd)
    header, _body = split_container(blob)
    return header, blob


def read_header(path: Path) -> tuple[dict[str, Any], bytes, int]:
    """Parse and validate the header. The body is not read here."""
    fd, st = _open_regular(path)
    try:
        line = b""
        while len(line) <= MAX_HEADER and b"\n" not in line:
            chunk = os.read(fd, min(4096, MAX_HEADER + 1 - len(line)))
            if not chunk:
                break
            line += chunk
    finally:
        os.close(fd)
    if b"\n" not in line:
        raise LockerError(f"{path} header is missing or too long")
    line = line.split(b"\n", 1)[0] + b"\n"
    header = _parse_header_line(line)
    expect = len(line) + expected_body_length(header["size"], header["chunk_size"])
    if st.st_size != expect:
        raise LockerError(f"{path} length does not match its header")
    return header, line, len(line)


def seal_file_key(public_key: bytes, file_key: bytes) -> bytes:
    nacl = sodium()
    return nacl.public.SealedBox(nacl.public.PublicKey(public_key)).encrypt(file_key)


def unseal_file_key(header: dict[str, Any], private_key: bytes) -> bytes:
    nacl = sodium()
    try:
        return nacl.public.SealedBox(nacl.public.PrivateKey(private_key)).decrypt(bytes.fromhex(header["sealed_key"]))
    except Exception as exc:
        raise LockerError("this key does not open the container") from exc


def _iter_chunks(handle: Any, chunk_size: int) -> Any:
    index = 0
    current = handle.read(chunk_size)
    while True:
        nxt = handle.read(chunk_size)
        yield index, current, not nxt
        if not nxt:
            return
        index += 1
        current = nxt


def encrypt_container(plaintext: bytes, info: LockerInfo, public_key: bytes, *,
                      name: str, chunk_size: int = MIN_CHUNK, mtime: int | None = None) -> bytes:
    """Seal bytes in memory. The caller has already verified `public_key`."""
    if public_key != info.public_key:
        raise LockerError("recipient does not match locker.json; nothing was sealed")
    if len(plaintext) > MAX_PLAINTEXT:
        raise LockerError("plaintext exceeds the size limit")
    chunk_size = _as_int(chunk_size, "chunk_size", MIN_CHUNK, MAX_CHUNK)
    nacl = sodium()
    aead = nacl.bindings
    file_key = nacl.utils.random(aead.crypto_aead_xchacha20poly1305_ietf_KEYBYTES)
    base_nonce = nacl.utils.random(aead.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES)
    header: dict[str, Any] = {
        "version": 1, "locker_id": info.locker_id, "nonce": base_nonce.hex(),
        "name": name, "size": len(plaintext), "mtime": int(time.time() if mtime is None else mtime),
        "chunk_size": chunk_size,
    }
    _validate_header_fields({**header, "sealed_key": "00" * SEALED_KEY_BYTES})
    digest = _header_digest(_aead_header(header))
    header["sealed_key"] = seal_file_key(public_key, file_key).hex()
    line = MAGIC + json.dumps(header, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    body = bytearray()
    view = memoryview(plaintext)
    offset = 0
    index = 0
    total = len(plaintext)
    while True:
        chunk = view[offset:offset + chunk_size]
        offset += len(chunk)
        final = offset >= total
        # An empty plaintext is one final empty chunk. A non-empty exact multiple
        # yields its last full chunk with final set, matching _iter_chunks.
        if total == 0 and index > 0:
            break
        body += aead.crypto_aead_xchacha20poly1305_ietf_encrypt(
            bytes(chunk), _chunk_ad(digest, index, final), _chunk_nonce(base_nonce, index), file_key)
        if final:
            break
        index += 1
    return line + bytes(body)


def decrypt_container(blob: bytes, private_key: bytes) -> tuple[dict[str, Any], bytes]:
    """Authenticate every chunk before returning plaintext. No prefix is returned on failure."""
    nacl = sodium()
    aead = nacl.bindings
    header, body = split_container(blob)
    file_key = unseal_file_key(header, private_key)
    digest = _header_digest(_aead_header(header))
    base_nonce = bytes.fromhex(header["nonce"])
    chunk_size = header["chunk_size"]
    sealed_chunk = chunk_size + TAG_BYTES
    view = memoryview(body)
    offset = 0
    index = 0
    out = bytearray()
    while offset < len(view):
        # The final chunk is shorter than a full sealed chunk. Take the remainder
        # only when it is the last slice; a short middle chunk fails authentication.
        remaining = len(view) - offset
        take = sealed_chunk if remaining > sealed_chunk else remaining
        if take < TAG_BYTES:
            raise LockerError("container chunk is truncated")
        current = bytes(view[offset:offset + take])
        offset += take
        final = offset == len(view)
        try:
            data = aead.crypto_aead_xchacha20poly1305_ietf_decrypt(
                current, _chunk_ad(digest, index, final), _chunk_nonce(base_nonce, index), file_key)
        except Exception as exc:
            raise LockerError(f"chunk {index} failed authentication (damaged, truncated or tampered)") from exc
        out += data
        if final:
            break
        index += 1
    if len(out) != header["size"]:
        raise LockerError(f"size mismatch ({len(out)} != {header['size']})")
    return header, bytes(out)


def unlock_bytes(blob: bytes, private_key: bytes) -> tuple[dict[str, Any], bytes]:
    return decrypt_container(blob, private_key)


def binding_for(kind: str, name: str, blob: bytes) -> dict[str, Any]:
    if kind not in ("unlock", "password-get"):
        raise UsageError(f"unsupported operation {kind!r}")
    _as_text(name, "name", maximum=256)
    return {"kind": kind, "name": name, "sha256": hashlib.sha256(blob).hexdigest(), "size": len(blob)}


def lock_file(source: Path, destination: Path, info: LockerInfo, *,
              private_key: bytes | None = None, confirmed_public: bytes | None = None,
              chunk_size: int = CHUNK_SIZE, remove_source: bool = False,
              before_commit: Callable[[], None] | None = None) -> dict[str, Any]:
    """Seal `source` into `destination` for a recipient that was verified first.

    Without `private_key` or a custody `confirmed_public` that matches
    locker.json, this refuses and does not create the destination or remove
    the source. Temporary names are unique 0600 files. A pre-existing symlink,
    including the old predictable `.name.part` name, is not opened.
    """
    chunk_size = _as_int(chunk_size, "chunk_size", MIN_CHUNK, MAX_CHUNK)
    public = Locker.recipient_public(Locker(), info, private_key=private_key, confirmed_public=confirmed_public)
    # recipient_public on a throwaway Locker does not read disk; the check is against `info`.
    if destination.exists() or destination.is_symlink():
        raise LockerError(f"{destination} already exists")
    src_fd, src_stat = _open_regular(source)
    identity = (src_stat.st_dev, src_stat.st_ino)
    try:
        if src_stat.st_size > MAX_PLAINTEXT:
            raise LockerError(f"{source} exceeds the plaintext size limit")
        nacl = sodium()
        aead = nacl.bindings
        file_key = nacl.utils.random(aead.crypto_aead_xchacha20poly1305_ietf_KEYBYTES)
        base_nonce = nacl.utils.random(aead.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES)
        header: dict[str, Any] = {
            "version": 1, "locker_id": info.locker_id, "nonce": base_nonce.hex(),
            "name": source.name, "size": src_stat.st_size, "mtime": int(src_stat.st_mtime),
            "chunk_size": chunk_size,
        }
        digest = _header_digest(_aead_header(header))
        header["sealed_key"] = seal_file_key(public, file_key).hex()
        line = MAGIC + json.dumps(header, sort_keys=True, separators=(",", ":")).encode() + b"\n"

        def write(tmp_fd: int) -> None:
            _write_all(tmp_fd, line)
            os.lseek(src_fd, 0, os.SEEK_SET)
            handle = os.fdopen(os.dup(src_fd), "rb", closefd=True)
            try:
                for index, data, final in _iter_chunks(handle, chunk_size):
                    _write_all(tmp_fd, aead.crypto_aead_xchacha20poly1305_ietf_encrypt(
                        data, _chunk_ad(digest, index, final), _chunk_nonce(base_nonce, index), file_key))
            finally:
                handle.close()

        _create_exclusive(destination.parent, destination.name, write, overwrite=False)
        if before_commit is not None:
            before_commit()
        if remove_source:
            now = os.fstat(src_fd)
            if (now.st_dev, now.st_ino) != identity or now.st_size != src_stat.st_size:
                raise LockerError(f"{source} changed while sealing and was left in place")
            _unlink_same(source, identity)
        return header
    finally:
        os.close(src_fd)


def write_plaintext(destination: Path, plaintext: bytes, *, overwrite: bool = False,
                    mtime: int | None = None) -> None:
    """Publish plaintext only as a complete file. The bytes are already authenticated."""
    if destination.is_symlink():
        raise LockerError(f"{destination} is a symlink; nothing was written")
    if destination.exists() and not overwrite:
        raise LockerError(f"{destination} already exists (use --force to overwrite)")

    def write(tmp_fd: int) -> None:
        _write_all(tmp_fd, plaintext)

    _create_exclusive(destination.parent, destination.name, write, overwrite=overwrite)
    if mtime is not None:
        try:
            os.utime(destination, (time.time(), int(mtime)))
        except OSError:
            pass


def unlock_file(container: Path, destination: Path | None, private_key: bytes,
                *, overwrite: bool = False, remove_container: bool = False,
                sink: Any = None) -> dict[str, Any]:
    """Open `container`. A sink is written only after every chunk authenticates."""
    if destination is None and sink is None:
        raise UsageError("unlock needs a destination or a sink")
    fd, identity_stat = _open_regular(container)
    identity = (identity_stat.st_dev, identity_stat.st_ino)
    try:
        blob = _read_exact_fd(fd, identity_stat.st_size)
    finally:
        os.close(fd)
    header, plaintext = decrypt_container(blob, private_key)
    if sink is not None and destination is None:
        sink.write(plaintext)
        return header
    if destination is None:
        raise UsageError("unlock needs a destination")
    write_plaintext(destination, plaintext, overwrite=overwrite, mtime=header.get("mtime"))
    if remove_container:
        _unlink_same(container, identity)
    return header


def container_for(path: Path) -> Path:
    return path.with_name(path.name + SUFFIX)


def original_for(container: Path) -> Path:
    if container.name.endswith(SUFFIX):
        return container.with_name(container.name[:-len(SUFFIX)])
    return container.with_name(container.name + ".unlocked")


def remove_regular(path: Path) -> None:
    fd, st = _open_regular(path)
    os.close(fd)
    _unlink_same(path, (st.st_dev, st.st_ino))
