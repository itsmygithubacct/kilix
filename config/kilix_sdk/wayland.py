"""Nested Weston sessions using the host's existing process ownership boundary.

The private-bus owner supplies containment only: Waydroid keeps the caller's
session bus so its status command and Android service see the same session.
"""
import atexit
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import time

import stream
from .private_bus import PrivateBus


def session_bus_address(environment=None):
    """Preserve an explicit address or libdbus's original runtime-bus fallback.

    Do not autolaunch a bus or guess another runtime directory. Call before
    replacing XDG_RUNTIME_DIR; percent-escape filesystem bytes, not Unicode
    code points, using the D-Bus address alphabet.
    """
    env = os.environ if environment is None else environment
    explicit = env.get("DBUS_SESSION_BUS_ADDRESS")
    if explicit:
        return explicit
    runtime = env.get("XDG_RUNTIME_DIR")
    if not runtime or not os.path.isabs(runtime):
        return ""
    bus = Path(runtime) / "bus"
    try:
        info = bus.stat()
    except OSError:
        return ""
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        return ""
    safe = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-/\\.*"
    escaped = "".join(chr(byte) if byte in safe else f"%{byte:02x}"
                      for byte in os.fsencode(bus))
    return "unix:path=" + escaped


@dataclass(frozen=True)
class SessionExit:
    source: str
    returncode: int


class NestedWaylandSession:
    def __init__(self, session, *, exclusive):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", exclusive):
            raise ValueError("invalid nested session lock name")
        self.supervisor = stream.StreamSupervisor(session + "-" + secrets.token_hex(4))
        self.exclusive = exclusive
        self.lock = None
        self.owners = []
        self.compositor = None
        self.closed = False
        self.socket_name = "wayland-0"
        self.log_path = Path(self.supervisor.runtime_dir) / "weston.log"
        atexit.unregister(self.supervisor.cleanup)
        atexit.register(self.close)

    def __enter__(self):
        if self.closed or self.lock is not None:
            raise RuntimeError("nested session has already been entered or closed")
        try:
            # Per-UID exclusion is independent of caller-selected storage roots.
            parent = Path("/tmp") / f"kilix-wayland-{os.getuid()}"
            parent.mkdir(mode=0o700, exist_ok=True)
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RuntimeError("unsafe nested Wayland lock directory")
            self.lock = os.open(parent / (self.exclusive + ".lock"),
                                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            info = os.fstat(self.lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise RuntimeError("unsafe nested Wayland lock")
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except BaseException:
            self.close()
            raise

    def environment(self):
        env = dict(os.environ)
        address = session_bus_address(env)
        if address:
            env["DBUS_SESSION_BUS_ADDRESS"] = address
        previous = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        # Keep host audio reachable after changing the Wayland socket directory.
        if "PULSE_SERVER" not in env and (Path(previous) / "pulse/native").exists():
            env["PULSE_SERVER"] = "unix:" + str(Path(previous) / "pulse/native")
        # Waydroid binds this socket into Android using PULSE_RUNTIME_PATH;
        # PULSE_SERVER alone does not override its XDG_RUNTIME_DIR fallback.
        audio_socket = Path(previous) / "pulse/native"
        if "PULSE_RUNTIME_PATH" not in env and audio_socket.is_absolute():
            try:
                info = audio_socket.stat()
            except OSError:
                pass
            else:
                if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
                    env["PULSE_RUNTIME_PATH"] = str(audio_socket.parent)
        env["XDG_RUNTIME_DIR"] = self.supervisor.runtime_dir
        env["WAYLAND_DISPLAY"] = self.socket_name
        env.pop("WAYLAND_SOCKET", None)
        return env

    def _spawn(self, name, command, **kwargs):
        if self.closed or self.lock is None:
            raise RuntimeError("nested session is not open")
        env = self.environment()
        try:
            owner = PrivateBus(self.supervisor, env)
        except BaseException as error:
            retained = getattr(error, "resource", None)
            if isinstance(retained, PrivateBus):
                self.owners.append(retained)
            raise
        self.owners.append(owner)
        process = self.supervisor.spawn(name, command, env=env,
                                       _kilix_remote_launcher=owner.launch_primary, **kwargs)
        owner.bind(process)
        return process

    def start_x11(self, width, height, *, executable="weston", timeout=10):
        if self.compositor is not None:
            raise RuntimeError("compositor already started")
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("nested Weston requires an X display")
        if not (1 <= int(width) <= 16384 and 1 <= int(height) <= 16384):
            raise ValueError("invalid nested display dimensions")
        endpoint = str(Path(self.supervisor.runtime_dir) / self.socket_name)
        if len(os.fsencode(endpoint)) >= 108:
            raise RuntimeError("nested Wayland socket path is too long; use a shorter KILIX_SESSION_HOME")
        with self.log_path.open("wb") as log:
            self.compositor = self._spawn("weston", [
                executable, "--backend=x11-backend.so", "--socket=" + self.socket_name,
                "--width=" + str(int(width)), "--height=" + str(int(height)),
                "--idle-time=0", "--no-config",
            ], stdout=log, stderr=log)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.compositor.poll() is not None:
                raise RuntimeError("Weston exited before readiness: " + self.compositor_diagnostic())
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(0.1)
                try:
                    probe.connect(endpoint)
                    return self.compositor
                except OSError:
                    pass
            time.sleep(0.02)
        raise RuntimeError("Weston did not create its Wayland socket")

    def launch_client(self, name, command, **kwargs):
        if self.compositor is None or self.compositor.poll() is not None:
            raise RuntimeError("nested compositor is not running")
        return self._spawn(name, command, **kwargs)

    def wait(self, client):
        while True:
            for source, process in (("compositor", self.compositor), ("client", client)):
                status = process.poll()
                if status is not None:
                    return SessionExit(source, status)
            time.sleep(0.02)

    def compositor_diagnostic(self):
        try:
            with self.log_path.open("rb") as log:
                log.seek(0, 2)
                log.seek(max(0, log.tell() - 4096))
                return log.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return ""

    def close(self):
        if self.closed:
            return
        failures = []
        # Client owner is always closed before the compositor owner.
        for owner in reversed(self.owners):
            try:
                owner.close()
            except BaseException as error:
                failures.append(error)
        if failures:
            error = RuntimeError("nested Wayland cleanup incomplete")
            error.resource = self
            error.cleanup_errors = tuple(failures)
            raise error
        self.supervisor.cleanup()
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None
        self.closed = True
        atexit.unregister(self.close)

    def __exit__(self, *_):
        self.close()
