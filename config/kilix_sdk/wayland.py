"""Supervised nested Wayland compositors for Kilix-hosted applications.

Kilix providers already own the X display, capture, presentation, and input
path.  This module adds one reusable bridge for native Wayland clients: a
private Weston runtime whose X11 backend appears as an ordinary application
window on that provider-owned display.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import os
import re
import shutil
import stat
import subprocess
import time
from typing import Iterable, Mapping

import stream

from ._process import stop_process


_PLAIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_UNIX_SOCKET_PATH_LIMIT = 107


class WaylandSessionError(RuntimeError):
    """A nested compositor or one of its invariants failed."""


class WaylandSessionBusy(WaylandSessionError):
    """Another owner holds the requested exclusive session identity."""


@dataclass(frozen=True)
class SessionExit:
    """The first supervised side that exited."""

    source: str
    returncode: int


def _plain_name(value: str, label: str) -> str:
    if not isinstance(value, str) or _PLAIN_NAME.fullmatch(value) is None:
        raise ValueError(f"{label} must be a short, plain name")
    return value


def _dimension(value: int, label: str) -> int:
    selected = int(value)
    if not 1 <= selected <= 16384:
        raise ValueError(f"{label} must be between 1 and 16384 pixels")
    return selected


def _executable(command: str) -> str:
    if not isinstance(command, str) or not command:
        raise ValueError("compositor command must not be empty")
    if os.path.isabs(command):
        if os.path.isfile(command) and os.access(command, os.X_OK):
            return command
        raise WaylandSessionError(
            f"configured Wayland compositor is not executable: {command}"
        )
    if os.sep in command or (os.path.altsep and os.path.altsep in command):
        raise ValueError("compositor command must be absolute or a command name")
    resolved = shutil.which(command)
    if not resolved:
        raise WaylandSessionError(
            f"{command} is required for nested Wayland applications"
        )
    return resolved


def _private_log(path: str):
    flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "wb")


def _runtime_services(environment: dict[str, str]) -> None:
    """Keep audio sockets reachable after XDG_RUNTIME_DIR becomes private."""
    host_runtime = environment.get("XDG_RUNTIME_DIR", "")
    if not host_runtime or not os.path.isabs(host_runtime):
        return
    pulse = os.path.join(host_runtime, "pulse")
    if not environment.get("PULSE_RUNTIME_PATH") and os.path.isdir(pulse):
        environment["PULSE_RUNTIME_PATH"] = pulse
    if not environment.get("PIPEWIRE_RUNTIME_DIR") and (
        os.path.exists(os.path.join(host_runtime, "pipewire-0"))
        or os.path.exists(os.path.join(host_runtime, "pipewire-0-manager"))
    ):
        environment["PIPEWIRE_RUNTIME_DIR"] = host_runtime


class NestedWaylandSession:
    """Own one private Weston-on-X11 compositor and its client processes."""

    def __init__(
        self,
        session: str,
        *,
        socket_name: str = "wayland-0",
        exclusive: str | None = None,
        supervisor=None,
    ) -> None:
        self.session = _plain_name(session, "Wayland session name")
        self.socket_name = _plain_name(socket_name, "Wayland socket name")
        self.supervisor = supervisor or stream.StreamSupervisor(self.session)
        self.compositor = None
        self.renderer = None
        self.clients = []
        self._exclusive_fd = None
        self._closed = False
        if exclusive is not None:
            try:
                self._acquire_exclusive(_plain_name(
                    exclusive, "exclusive Wayland identity"))
            except Exception:
                self.supervisor.cleanup()
                raise

    @property
    def runtime_dir(self) -> str:
        return self.supervisor.runtime_dir

    @property
    def socket_path(self) -> str:
        return os.path.join(self.runtime_dir, self.socket_name)

    @property
    def compositor_log(self) -> str:
        return os.path.join(self.runtime_dir, "weston.log")

    def _acquire_exclusive(self, identity: str) -> None:
        lockdir = getattr(self.supervisor, "lockdir", "")
        if not lockdir:
            raise WaylandSessionError(
                "the process supervisor has no shared lock directory"
            )
        path = os.path.join(lockdir, f"wayland-{identity}.lock")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise WaylandSessionError("unsafe nested-Wayland lock file")
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WaylandSessionBusy(
                    f"nested Wayland session {identity!r} is already running"
                ) from error
        except Exception:
            os.close(fd)
            raise
        self._exclusive_fd = fd

    def _base_environment(
        self, extra: Mapping[str, str] | None
    ) -> dict[str, str]:
        environment = dict(os.environ)
        if extra:
            environment.update({str(key): str(value) for key, value in extra.items()})
        _runtime_services(environment)
        return environment

    def compositor_environment(
        self, extra: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        environment = self._base_environment(extra)
        environment["XDG_RUNTIME_DIR"] = self.runtime_dir
        # DISPLAY selects Weston's X11 backend. A parent Wayland connection or
        # inherited socket descriptor would silently select a different backend.
        environment.pop("WAYLAND_DISPLAY", None)
        environment.pop("WAYLAND_SOCKET", None)
        environment.pop("WESTON_CONFIG_FILE", None)
        return environment

    def client_environment(
        self, extra: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        environment = self._base_environment(extra)
        environment["XDG_RUNTIME_DIR"] = self.runtime_dir
        environment["WAYLAND_DISPLAY"] = self.socket_name
        environment["XDG_SESSION_TYPE"] = "wayland"
        environment.pop("WAYLAND_SOCKET", None)
        return environment

    def start_x11(
        self,
        width: int,
        height: int,
        *,
        executable: str = "weston",
        timeout: float = 10.0,
        env: Mapping[str, str] | None = None,
        renderer: str = "auto",
    ):
        """Start a config-isolated Weston X11 backend and await its socket.

        ``auto`` keeps Weston's accelerated renderer when it starts cleanly,
        then retries with Pixman for X servers without a usable EGL path.
        """
        if self.compositor is not None:
            raise WaylandSessionError("nested Wayland compositor is already started")
        width = _dimension(width, "Wayland output width")
        height = _dimension(height, "Wayland output height")
        timeout = float(timeout)
        if timeout <= 0:
            raise ValueError("Wayland compositor timeout must be positive")
        if renderer not in ("auto", "gl", "pixman"):
            raise ValueError("Wayland renderer must be auto, gl, or pixman")
        if len(os.fsencode(self.socket_path)) > _UNIX_SOCKET_PATH_LIMIT:
            raise WaylandSessionError(
                "nested Wayland socket path is too long for a Unix socket"
            )
        environment = self.compositor_environment(env)
        if not environment.get("DISPLAY"):
            raise WaylandSessionError(
                "an X DISPLAY is required for the nested Wayland X11 backend"
            )
        try:
            metadata = os.lstat(self.socket_path)
        except FileNotFoundError:
            pass
        else:
            kind = "socket" if stat.S_ISSOCK(metadata.st_mode) else "path"
            raise WaylandSessionError(
                f"nested Wayland {kind} already exists before startup"
            )

        # These options are shared by Weston 10 (Debian 12) through Weston 16.
        # With DISPLAY set and WAYLAND_DISPLAY absent Weston selects its X11
        # backend, avoiding backend module-name churn between those releases.
        base_argv = [
            _executable(executable),
            "--no-config",
            f"--socket={self.socket_name}",
            f"--width={width}",
            f"--height={height}",
            "--idle-time=0",
        ]
        attempts = {
            "auto": (("gl", False), ("pixman", True)),
            "gl": (("gl", False),),
            "pixman": (("pixman", True),),
        }[renderer]
        failures = []
        for selected_renderer, use_pixman in attempts:
            argv = list(base_argv)
            if use_pixman:
                argv.append("--use-pixman")
            log = _private_log(self.compositor_log)
            try:
                self.compositor = self.supervisor.spawn(
                    "weston",
                    argv,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            except Exception:
                try:
                    log.close()
                except Exception:
                    pass
                raise

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                returncode = self.compositor.poll()
                if returncode is not None:
                    diagnostic = self.compositor_diagnostic()
                    detail = f": {diagnostic}" if diagnostic else ""
                    failures.append(
                        f"{selected_renderer} renderer exited with "
                        f"rc={returncode}{detail}"
                    )
                    self.compositor = None
                    break
                try:
                    metadata = os.stat(self.socket_path)
                except FileNotFoundError:
                    metadata = None
                if metadata is not None:
                    if stat.S_ISSOCK(metadata.st_mode):
                        self.renderer = selected_renderer
                        return self.compositor
                    self.close()
                    raise WaylandSessionError(
                        "nested Wayland compositor created a non-socket display path"
                    )
                time.sleep(0.05)
            else:
                stop_process(self.compositor, timeout=1.0)
                failures.append(
                    f"{selected_renderer} renderer did not create "
                    f"{self.socket_name} within {timeout:g} seconds"
                )
                self.compositor = None

            # A failed Weston may leave its socket inode behind. The runtime
            # directory is private and the name was validated, so removing a
            # socket here is bounded and lets the Pixman retry bind cleanly.
            try:
                stale = os.lstat(self.socket_path)
            except FileNotFoundError:
                stale = None
            if stale is not None:
                if not stat.S_ISSOCK(stale.st_mode):
                    self.close()
                    raise WaylandSessionError(
                        "failed nested compositor left a non-socket display path"
                    )
                os.unlink(self.socket_path)

        self.close()
        raise WaylandSessionError(
            "nested Wayland compositor failed to start: " + "; ".join(failures)
        )

    def launch_client(
        self,
        name: str,
        command: Iterable[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ):
        if self.compositor is None or self.compositor.poll() is not None:
            raise WaylandSessionError("nested Wayland compositor is not running")
        name = _plain_name(name, "Wayland client process name")
        argv = [str(value) for value in command]
        if not argv:
            raise ValueError("Wayland client command must not be empty")
        process = self.supervisor.spawn(
            name,
            argv,
            env=self.client_environment(env),
            cwd=cwd,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
        self.clients.append(process)
        return process

    def wait(
        self,
        client=None,
        *,
        poll_interval: float = 0.1,
    ) -> SessionExit:
        """Wait until the compositor window or one selected client exits."""
        if self.compositor is None:
            raise WaylandSessionError("nested Wayland compositor is not started")
        if client is None:
            if not self.clients:
                raise WaylandSessionError("nested Wayland session has no client")
            client = self.clients[-1]
        interval = float(poll_interval)
        if interval <= 0:
            raise ValueError("poll interval must be positive")
        while True:
            returncode = client.poll()
            if returncode is not None:
                return SessionExit("client", int(returncode))
            returncode = self.compositor.poll()
            if returncode is not None:
                return SessionExit("compositor", int(returncode))
            time.sleep(interval)

    def compositor_diagnostic(self, limit: int = 2048) -> str:
        try:
            with open(self.compositor_log, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - max(1, int(limit))))
                data = handle.read()
        except OSError:
            return ""
        lines = data.decode("utf-8", "replace").strip().splitlines()
        return lines[-1].strip() if lines else ""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            # Clients get a bounded grace period while their compositor is
            # still alive.  Keep it below XAppSession's outer three-second
            # shutdown budget so the nested owner can finish its own cleanup.
            for process in reversed(self.clients):
                stop_process(process, timeout=1.5)
            self.clients.clear()
            self.supervisor.cleanup()
        finally:
            if self._exclusive_fd is not None:
                try:
                    os.close(self._exclusive_fd)
                except OSError:
                    pass
                self._exclusive_fd = None

    def __enter__(self):
        return self

    def __exit__(self, _kind, _value, _traceback):
        self.close()


__all__ = [
    "NestedWaylandSession",
    "SessionExit",
    "WaylandSessionBusy",
    "WaylandSessionError",
]
