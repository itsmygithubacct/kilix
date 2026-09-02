#!/usr/bin/env python3
"""The fingerlock locker: passphrase-rooted keys, sealed file containers and
named passwords, on libsodium through python3-nacl.

KEYS. A locker has one X25519 keypair made from a random 32-byte seed. The
seed is stored in locker.json wrapped (XSalsa20-Poly1305 secret box) under a
key derived from the owner's passphrase with Argon2id, whose salt and cost
live beside it; the public key is stored in the clear. Locking therefore
needs no secret at all: any process of the owner can seal a file to the public
key, so `kilix fingerlock lock` never asks for anything. Unlocking needs the
private key, which comes either from the passphrase (unwrap the seed) or,
once deposited for this boot, from the root agent after a fingerprint match.
A passphrase change only rewraps the seed, so every container stays openable.

CONTAINERS (`*.flk`). One line of magic and JSON header, then chunks:

  FLK1 {json}\\n
  chunk*   each chunk = XChaCha20-Poly1305-IETF(file_key, nonce_i, AD_i, data)

The header carries the locker id, the sealed per-file key (SealedBox to the
locker public key), the original name, size, mtime and the chunk size. The
file key is random per container. Each chunk's nonce is the header's base nonce with the chunk
index in its last eight bytes, and its associated data binds the header digest,
the index and a final flag, so chunks cannot be reordered, dropped or truncated
without failing authentication.

PASSWORDS are small containers under the locker's `passwords/` directory,
named by the record name, holding the secret text.

This format is deliberately built from the same primitives the F112 vault
chose (Argon2id, XChaCha20-Poly1305, libsodium), so the locker can migrate its
keys into that vault as a presence-gated record once the vault is qualified.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fingerlock_core import DependencyUnavailable, FingerlockError, UsageError  # noqa: E402

MAGIC = b"FLK1 "
SUFFIX = ".flk"
LOCKER_VERSION = 1
CHUNK_SIZE = 4 * 1024 * 1024
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}$")
LOCKER_PACKAGES: tuple[str, ...] = ("python3-nacl",)


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


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _write_private(path: Path, data: bytes) -> None:
    _private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
    def from_dict(cls, data: dict[str, Any]) -> "LockerInfo":
        try:
            if int(data["version"]) != LOCKER_VERSION:
                raise LockerError(f"unsupported locker version {data['version']}")
            kdf = data["kdf"]
            if kdf["name"] != "argon2id":
                raise LockerError(f"unsupported kdf {kdf['name']}")
            return cls(locker_id=str(data["locker_id"]), created=str(data["created"]),
                       salt=bytes.fromhex(kdf["salt"]), opslimit=int(kdf["opslimit"]),
                       memlimit=int(kdf["memlimit"]), public_key=bytes.fromhex(data["public_key"]),
                       wrapped_seed=bytes.fromhex(data["wrapped_seed"]))
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


class Locker:
    """One user's locker: metadata on disk, keys only in memory."""

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or default_home()
        self.path = self.home / "locker.json"
        self.passwords_dir = self.home / "passwords"

    # -- metadata ------------------------------------------------------------

    def exists(self) -> bool:
        return self.path.is_file()

    def info(self) -> LockerInfo:
        if not self.exists():
            raise LockerError("no locker yet; run: kilix fingerlock init")
        try:
            return LockerInfo.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise LockerError(f"cannot read {self.path}: {exc}") from exc

    def _save(self, info: LockerInfo) -> None:
        _write_private(self.path, json.dumps(info.as_dict(), indent=2, sort_keys=True).encode() + b"\n")

    # -- keys --------------------------------------------------------------------

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
        """Passphrase -> X25519 private key; raises WrongPassphrase on mismatch."""
        info = info or self.info()
        private, public = self._keypair(self.unwrap_seed(passphrase, info))
        if public != info.public_key:
            raise LockerError("locker.json is inconsistent: the seed does not match the public key")
        return private

    @staticmethod
    def check_private_key(private: bytes, info: LockerInfo) -> bool:
        nacl = sodium()
        try:
            return bytes(nacl.public.PrivateKey(private).public_key) == info.public_key
        except Exception:
            return False

    def init(self, passphrase: str, *, kdf: str = "moderate",
             opslimit: int | None = None, memlimit: int | None = None) -> LockerInfo:
        nacl = sodium()
        if self.exists():
            raise LockerError(f"a locker already exists at {self.path}")
        if len(passphrase) < 8:
            raise UsageError("the passphrase must be at least 8 characters")
        if opslimit is None or memlimit is None:
            opslimit, memlimit = kdf_limits(kdf)
        salt = nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
        seed = nacl.utils.random(32)
        wrapped = nacl.secret.SecretBox(self._kek(passphrase, salt, opslimit, memlimit)).encrypt(seed)
        _private, public = self._keypair(seed)
        info = LockerInfo(locker_id=str(uuid.uuid4()), created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          salt=salt, opslimit=opslimit, memlimit=memlimit, public_key=public, wrapped_seed=bytes(wrapped))
        self._save(info)
        _private_dir(self.passwords_dir)
        return info

    def change_passphrase(self, old_passphrase: str, new_passphrase: str) -> LockerInfo:
        """Rewrap the seed under the new passphrase; keys and containers are unchanged."""
        nacl = sodium()
        old = self.info()
        seed = self.unwrap_seed(old_passphrase, old)
        if len(new_passphrase) < 8:
            raise UsageError("the passphrase must be at least 8 characters")
        salt = nacl.utils.random(nacl.pwhash.argon2id.SALTBYTES)
        wrapped = nacl.secret.SecretBox(self._kek(new_passphrase, salt, old.opslimit, old.memlimit)).encrypt(seed)
        info = LockerInfo(locker_id=old.locker_id, created=old.created, salt=salt, opslimit=old.opslimit,
                          memlimit=old.memlimit, public_key=old.public_key, wrapped_seed=bytes(wrapped))
        self._save(info)
        return info

    # -- passwords -----------------------------------------------------------------

    def password_path(self, name: str) -> Path:
        if not NAME_PATTERN.match(name):
            raise UsageError("password names are letters, digits, and . _ @ + - (1 to 128 characters)")
        return self.passwords_dir / (name + SUFFIX)

    def password_names(self) -> list[str]:
        if not self.passwords_dir.is_dir():
            return []
        return sorted(p.name[:-len(SUFFIX)] for p in self.passwords_dir.iterdir()
                      if p.name.endswith(SUFFIX) and not p.name.startswith("."))


# --- containers -------------------------------------------------------------------------

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


def seal_file_key(public_key: bytes, file_key: bytes) -> bytes:
    nacl = sodium()
    return nacl.public.SealedBox(nacl.public.PublicKey(public_key)).encrypt(file_key)


def unseal_file_key(header: dict[str, Any], private_key: bytes) -> bytes:
    nacl = sodium()
    try:
        return nacl.public.SealedBox(nacl.public.PrivateKey(private_key)).decrypt(bytes.fromhex(header["sealed_key"]))
    except Exception as exc:
        raise LockerError("this key does not open the container") from exc


def read_header(path: Path) -> tuple[dict[str, Any], bytes, int]:
    """Parse the header line; returns (header, raw line, body offset)."""
    try:
        with open(path, "rb") as handle:
            line = handle.readline(1 << 16)
    except OSError as exc:
        raise LockerError(f"cannot read {path}: {exc}") from exc
    if not line.startswith(MAGIC) or not line.endswith(b"\n"):
        raise LockerError(f"{path} is not a fingerlock container")
    try:
        header = json.loads(line[len(MAGIC):].decode("utf-8"))
        for key in ("version", "locker_id", "sealed_key", "nonce", "name", "size", "chunk_size"):
            header[key]
    except (ValueError, KeyError, TypeError) as exc:
        raise LockerError(f"{path} has a damaged header: {exc}") from exc
    if header["version"] != 1:
        raise LockerError(f"{path}: unsupported container version {header['version']}")
    return header, line, len(line)


def _iter_chunks(handle: Any, chunk_size: int) -> Iterator[tuple[int, bytes, bool]]:
    index = 0
    current = handle.read(chunk_size)
    while True:
        nxt = handle.read(chunk_size)
        yield index, current, not nxt
        if not nxt:
            return
        index += 1
        current = nxt


def lock_file(source: Path, destination: Path, info: LockerInfo, *, name: str | None = None,
              chunk_size: int = CHUNK_SIZE, remove_source: bool = False) -> dict[str, Any]:
    """Seal `source` into `destination`; needs only the locker's public key."""
    nacl = sodium()
    aead = nacl.bindings
    if destination.exists():
        raise LockerError(f"{destination} already exists")
    try:
        st = source.stat()
    except OSError as exc:
        raise LockerError(f"cannot read {source}: {exc}") from exc
    file_key = nacl.utils.random(aead.crypto_aead_xchacha20poly1305_ietf_KEYBYTES)
    base_nonce = nacl.utils.random(aead.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES)
    header: dict[str, Any] = {
        "version": 1, "locker_id": info.locker_id, "nonce": base_nonce.hex(),
        "name": name or source.name, "size": st.st_size, "mtime": int(st.st_mtime),
        "chunk_size": chunk_size,
    }
    digest = _header_digest(_aead_header(header))
    header["sealed_key"] = seal_file_key(info.public_key, file_key).hex()
    line = MAGIC + json.dumps(header, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    tmp = destination.with_name("." + destination.name + ".part")
    try:
        with open(source, "rb") as src, open(tmp, "wb") as dst:
            os.chmod(tmp, 0o600)
            dst.write(line)
            for index, data, final in _iter_chunks(src, chunk_size):
                dst.write(aead.crypto_aead_xchacha20poly1305_ietf_encrypt(
                    data, _chunk_ad(digest, index, final), _chunk_nonce(base_nonce, index), file_key))
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp, destination)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if remove_source:
        os.unlink(source)
    return header


def unlock_file(container: Path, destination: Path | None, private_key: bytes,
                *, overwrite: bool = False, remove_container: bool = False,
                sink: Any = None) -> dict[str, Any]:
    """Open `container` with the locker's private key into `destination` or `sink`."""
    nacl = sodium()
    aead = nacl.bindings
    header, _line, offset = read_header(container)
    file_key = unseal_file_key(header, private_key)
    digest = _header_digest(_aead_header(header))
    base_nonce = bytes.fromhex(header["nonce"])
    chunk_size = int(header["chunk_size"])
    sealed_chunk = chunk_size + aead.crypto_aead_xchacha20poly1305_ietf_ABYTES
    if destination is not None and destination.exists() and not overwrite:
        raise LockerError(f"{destination} already exists (use --force to overwrite)")
    tmp = destination.with_name("." + destination.name + ".part") if destination is not None else None
    out = open(tmp, "wb") if tmp is not None else sink
    try:
        if tmp is not None:
            os.chmod(tmp, 0o600)
        with open(container, "rb") as src:
            src.seek(offset)
            index = 0
            written = 0
            current = src.read(sealed_chunk)
            while True:
                nxt = src.read(sealed_chunk)
                final = not nxt
                try:
                    data = aead.crypto_aead_xchacha20poly1305_ietf_decrypt(
                        current, _chunk_ad(digest, index, final), _chunk_nonce(base_nonce, index), file_key)
                except Exception as exc:
                    raise LockerError(f"{container}: chunk {index} failed authentication "
                                      "(damaged, truncated or tampered)") from exc
                out.write(data)
                written += len(data)
                if final:
                    break
                index += 1
                current = nxt
        if written != int(header["size"]):
            raise LockerError(f"{container}: size mismatch ({written} != {header['size']})")
        if tmp is not None:
            out.flush()
            os.fsync(out.fileno())
            out.close()
            os.replace(tmp, destination)
            try:
                os.utime(destination, (time.time(), int(header.get("mtime", time.time()))))
            except OSError:
                pass
    except BaseException:
        if tmp is not None:
            try:
                out.close()
            except Exception:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    if remove_container:
        os.unlink(container)
    return header


def container_for(path: Path) -> Path:
    return path.with_name(path.name + SUFFIX)


def original_for(container: Path) -> Path:
    if container.name.endswith(SUFFIX):
        return container.with_name(container.name[:-len(SUFFIX)])
    return container.with_name(container.name + ".unlocked")
