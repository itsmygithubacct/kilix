"""Process and socket facts the host kernel answers differently on each platform.

Linux answers through /proc, prctl(2) and SO_PEERCRED. FreeBSD mounts no /proc by
default (and its procfs has no stat, environ, exe or children files); it answers
through sysctl(3) ``kern.proc.*``, procctl(2) and LOCAL_PEERCRED / getpeereid(3).
Callers ask the question here, once, instead of reading one kernel's files.

Every function returns ``None`` (or raises ``OSError`` for the control calls) when
the host cannot answer; none of them guesses.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import socket
import struct
import sys

IS_LINUX = sys.platform.startswith("linux")
IS_FREEBSD = sys.platform.startswith("freebsd")

# --- FreeBSD ABI constants (sys/sysctl.h, sys/procctl.h, sys/un.h) -----------
_CTL_KERN = 1
_KERN_PROC = 14
_KERN_PROC_PID = 1
_KERN_PROC_ARGS = 7
_KERN_PROC_PATHNAME = 12
_KERN_PROC_ENV = 35
_KERN_PROC_CWD = 42
_KERN_PROC_PROC = 8
_P_PID = 0
_PROC_REAP_ACQUIRE = 2
_PROC_PDEATHSIG_CTL = 11
_PROC_NO_NEW_PRIVS_CTL = 19
_PROC_NO_NEW_PRIVS_ENABLE = 1
_SOL_LOCAL = 0
_LOCAL_PEERCRED = 1
_XUCRED_SIZE = 88          # LP64 (amd64, arm64); cr_pid follows the groups
_XUCRED_PID_OFFSET = 80
# struct kinfo_proc (sys/user.h), LP64. Offsets measured on 15.1 amd64/arm64;
# every read checks ki_structsize and ki_pid before trusting any other field.
_KINFO_SIZE = 1088
_KI_PID, _KI_PPID, _KI_SID = 72, 76, 88
_KI_UID, _KI_RUID = 168, 172
_KI_START = 336            # struct timeval {time_t tv_sec; suseconds_t tv_usec}
_KI_STAT = 388
_FREEBSD_STATES = {1: "I", 2: "R", 3: "S", 4: "T", 5: "Z", 6: "W", 7: "L"}

# --- Linux prctl(2) options ------------------------------------------------
_PR_SET_PDEATHSIG = 1
_PR_SET_CHILD_SUBREAPER = 36
_PR_SET_NO_NEW_PRIVS = 38

_libc_handle = None


def _libc():
    global _libc_handle
    if _libc_handle is None:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
        if IS_FREEBSD:
            libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
                                    ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                                    ctypes.c_void_p, ctypes.c_size_t]
            libc.sysctl.restype = ctypes.c_int
            # int procctl(idtype_t, id_t, int, void *); id_t is 64 bits wide.
            libc.procctl.argtypes = [ctypes.c_int, ctypes.c_int64, ctypes.c_int, ctypes.c_void_p]
            libc.procctl.restype = ctypes.c_int
        _libc_handle = libc
    return _libc_handle


def _sysctl(mib: list[int], limit: int = 1 << 22) -> bytes | None:
    """Read one sysctl node as bytes; None when it is absent or refused."""
    libc = _libc()
    array = (ctypes.c_int * len(mib))(*mib)
    size = ctypes.c_size_t(0)
    if libc.sysctl(array, len(mib), None, ctypes.byref(size), None, 0) != 0:
        return None
    for _ in range(4):
        if size.value > limit:
            return None
        buffer = ctypes.create_string_buffer(size.value + 256)
        size = ctypes.c_size_t(len(buffer))
        if libc.sysctl(array, len(mib), buffer, ctypes.byref(size), None, 0) == 0:
            return buffer.raw[:size.value]
        if ctypes.get_errno() != 12:  # ENOMEM: grew between the two calls
            return None
        size = ctypes.c_size_t(0)
        if libc.sysctl(array, len(mib), None, ctypes.byref(size), None, 0) != 0:
            return None
        size = ctypes.c_size_t(size.value + size.value // 4)
    return None


def _kinfo(pid: int) -> bytes | None:
    raw = _sysctl([_CTL_KERN, _KERN_PROC, _KERN_PROC_PID, int(pid)])
    if not raw or len(raw) < _KINFO_SIZE:
        return None
    structsize, = struct.unpack_from("i", raw, 0)
    found, = struct.unpack_from("i", raw, _KI_PID)
    if structsize != _KINFO_SIZE or found != int(pid):
        return None
    return raw


def _linux_stat_fields(pid: int) -> list[str] | None:
    """Fields of /proc/PID/stat after the comm; [0] is the state."""
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8", errors="replace") as handle:
            record = handle.read()
    except OSError:
        return None
    _, sep, rest = record.rpartition(")")
    return rest.split() if sep else None


def process_state(pid: int) -> str | None:
    """One-letter scheduler state ("R", "S", "Z", ...), or None if not running."""
    if IS_FREEBSD:
        raw = _kinfo(pid)
        return None if raw is None else _FREEBSD_STATES.get(raw[_KI_STAT], "?")
    fields = _linux_stat_fields(pid)
    return fields[0] if fields else None


def parent_pid(pid: int) -> int | None:
    if IS_FREEBSD:
        raw = _kinfo(pid)
        return None if raw is None else struct.unpack_from("i", raw, _KI_PPID)[0]
    fields = _linux_stat_fields(pid)
    try:
        return int(fields[1]) if fields else None
    except (IndexError, ValueError):
        return None


def session_id(pid: int) -> int | None:
    try:
        return os.getsid(int(pid))
    except OSError:
        return None


def start_ticks(pid: int) -> int | None:
    """Names one incarnation of PID: stable while it lives, new if PID is reused.

    None when PID is not running or is a zombie. Linux: the start time in clock
    ticks (field 22 of /proc/PID/stat). FreeBSD: ki_start in microseconds.
    Compare only values read on the same host.
    """
    if IS_FREEBSD:
        raw = _kinfo(pid)
        if raw is None or raw[_KI_STAT] == 5:
            return None
        seconds, micros = struct.unpack_from("qq", raw, _KI_START)
        return seconds * 1_000_000 + micros
    fields = _linux_stat_fields(pid)
    if not fields or fields[0] in {"Z", "X"} or len(fields) < 20:
        return None
    return int(fields[19]) if fields[19].isdigit() else None


def start_token(pid: int) -> str | None:
    """start_ticks() as text, for records that store it as a string."""
    ticks = start_ticks(pid)
    return None if ticks is None else str(ticks)


def owner_uid(pid: int) -> int | None:
    """Real uid that owns PID (the /proc/PID directory owner on Linux)."""
    if IS_FREEBSD:
        raw = _kinfo(pid)
        return None if raw is None else struct.unpack_from("I", raw, _KI_RUID)[0]
    try:
        return os.stat(f"/proc/{int(pid)}").st_uid
    except OSError:
        return None


def executable(pid: int) -> str | None:
    """Resolved path of PID's executable."""
    if IS_FREEBSD:
        raw = _sysctl([_CTL_KERN, _KERN_PROC, _KERN_PROC_PATHNAME, int(pid)])
        return os.fsdecode(raw.rstrip(b"\0")) if raw and raw.rstrip(b"\0") else None
    try:
        return os.path.realpath(f"/proc/{int(pid)}/exe", strict=True)
    except (OSError, TypeError):
        try:
            return os.path.realpath(os.readlink(f"/proc/{int(pid)}/exe"))
        except OSError:
            return None


def _nul_list(raw: bytes | None) -> list[bytes] | None:
    if raw is None:
        return None
    items = raw.split(b"\0")
    if items and items[-1] == b"":
        items.pop()
    return items


def cmdline(pid: int) -> list[bytes] | None:
    if IS_FREEBSD:
        return _nul_list(_sysctl([_CTL_KERN, _KERN_PROC, _KERN_PROC_ARGS, int(pid)]))
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as handle:
            return _nul_list(handle.read())
    except OSError:
        return None


def process_environment(pid: int, limit: int = 262144) -> list[bytes] | None:
    """PID's environment as NAME=VALUE byte strings; None if unreadable or larger
    than LIMIT bytes (the kernel only answers for a process the caller owns)."""
    if IS_FREEBSD:
        raw = _sysctl([_CTL_KERN, _KERN_PROC, _KERN_PROC_ENV, int(pid)], limit=limit)
        return _nul_list(raw)
    try:
        with open(f"/proc/{int(pid)}/environ", "rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        return None
    return None if len(raw) > limit else _nul_list(raw)


def processes() -> list[int]:
    """Every visible PID (best effort; [] when the host cannot say)."""
    if IS_FREEBSD:
        raw = _sysctl([_CTL_KERN, _KERN_PROC, _KERN_PROC_PROC], limit=1 << 26)
        found = []
        for offset in range(0, len(raw or b"") - _KINFO_SIZE + 1, _KINFO_SIZE):
            if struct.unpack_from("i", raw, offset)[0] != _KINFO_SIZE:
                return []
            found.append(struct.unpack_from("i", raw, offset + _KI_PID)[0])
        return found
    try:
        return [int(entry) for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        return []


def children(pid: int) -> list[int]:
    """Direct children of PID (best effort; [] when the host cannot say)."""
    if IS_FREEBSD:
        # One kinfo_proc per process (KERN_PROC_PROC skips threads). Read in
        # this process, never through ps(1): a ps child would list itself.
        raw = _sysctl([_CTL_KERN, _KERN_PROC, _KERN_PROC_PROC], limit=1 << 26)
        if not raw:
            return []
        found = []
        for offset in range(0, len(raw) - _KINFO_SIZE + 1, _KINFO_SIZE):
            structsize, = struct.unpack_from("i", raw, offset)
            if structsize != _KINFO_SIZE:
                return []
            child, ppid = struct.unpack_from("ii", raw, offset + _KI_PID)
            if ppid == int(pid) and child != int(pid):
                found.append(child)
        return found
    found: list[int] = []
    try:
        tasks = os.listdir(f"/proc/{int(pid)}/task")
    except OSError:
        return found
    for task in tasks:
        try:
            with open(f"/proc/{int(pid)}/task/{task}/children", encoding="ascii") as handle:
                found.extend(int(item) for item in handle.read().split())
        except (OSError, ValueError):
            continue
    return found


# --- Process controls ---------------------------------------------------------

def _procctl(command: int, value: int | None) -> None:
    libc = _libc()
    argument = None if value is None else ctypes.byref(ctypes.c_int(value))
    if libc.procctl(_P_PID, os.getpid(), command, argument) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _prctl(option: int, value: int) -> None:
    if _libc().prctl(option, value, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def set_parent_death_signal(signal_number: int) -> None:
    """Deliver SIGNAL_NUMBER to this process when its parent exits."""
    if IS_FREEBSD:
        _procctl(_PROC_PDEATHSIG_CTL, int(signal_number))
    elif IS_LINUX:
        _prctl(_PR_SET_PDEATHSIG, int(signal_number))
    else:
        raise OSError(38, "no parent-death signal on this platform")


def set_child_subreaper() -> None:
    """Adopt orphaned descendants instead of letting init reap them."""
    if IS_FREEBSD:
        _procctl(_PROC_REAP_ACQUIRE, None)
    elif IS_LINUX:
        _prctl(_PR_SET_CHILD_SUBREAPER, 1)
    else:
        raise OSError(38, "no subreaper control on this platform")


def set_no_new_privs() -> None:
    """Forbid this process and its descendants from gaining privileges on exec."""
    if IS_FREEBSD:
        _procctl(_PROC_NO_NEW_PRIVS_CTL, _PROC_NO_NEW_PRIVS_ENABLE)
    elif IS_LINUX:
        _prctl(_PR_SET_NO_NEW_PRIVS, 1)
    else:
        raise OSError(38, "no no-new-privileges control on this platform")


# --- Unix-socket peers ----------------------------------------------------------

def peer_credentials(sock: socket.socket) -> tuple[int | None, int, int]:
    """(pid, uid, gid) of a connected Unix-socket peer. pid is None when the
    kernel does not report it. Raises OSError when nothing can be read."""
    if hasattr(socket, "SO_PEERCRED"):
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", raw)
        return pid, uid, gid
    if IS_FREEBSD:
        raw = sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, _XUCRED_SIZE)
        if len(raw) >= 12:
            version, uid, ngroups = struct.unpack_from("IIh", raw, 0)
            gid = struct.unpack_from("I", raw, 12)[0] if ngroups > 0 else -1
            pid = None
            if version == 0 and len(raw) >= _XUCRED_PID_OFFSET + 4:
                pid = struct.unpack_from("i", raw, _XUCRED_PID_OFFSET)[0] or None
            return pid, uid, gid
    uid_value, gid_value = ctypes.c_uint32(), ctypes.c_uint32()
    if _libc().getpeereid(sock.fileno(), ctypes.byref(uid_value), ctypes.byref(gid_value)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return None, uid_value.value, gid_value.value


def peer_is_owner(sock: socket.socket) -> bool:
    """True only when the peer's credentials were read and match our euid."""
    try:
        return peer_credentials(sock)[1] == os.geteuid()
    except OSError:
        return False
