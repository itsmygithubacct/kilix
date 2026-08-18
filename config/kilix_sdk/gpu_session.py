"""Lifecycle for a private, DMA-BUF-backed Kilix Wayland application."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

from . import gpu_host, wayland_input


class Session:
    def __init__(self, runtime: gpu_host.GpuHostRuntime, command: tuple[str, ...],
                 width: int, height: int, session_home: Path, fps: int = 60):
        self.runtime, self.command = runtime, command
        self.width, self.height = width, height
        self.fps = min(240, max(1, int(fps)))
        session_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.runtime_dir = Path(tempfile.mkdtemp(prefix="gpu-run-", dir=session_home))
        self.runtime_dir.chmod(0o700)
        self.frame_socket = self.runtime_dir / "frame.sock"
        self.input_socket = self.runtime_dir / "input.sock"
        self.wayland_socket = f"wayland-kilix-{os.getpid()}-{self.runtime_dir.name[-6:]}"
        self.environment = runtime.environment(self.runtime_dir)
        self.environment.update(gpu_host.app_environment(command))
        self.environment["WAYLAND_DISPLAY"] = self.wayland_socket
        self.environment["KILIX_WESTON_INPUT_SOCKET"] = str(self.input_socket)
        self.processes: list[subprocess.Popen] = []
        self.logs = []
        self.pipewire = self.capture = self.weston = None
        self.injector = None

    def _spawn(self, argv, **kwargs):
        process = subprocess.Popen(
            argv, env=self.environment, stdin=subprocess.DEVNULL,
            start_new_session=True, **kwargs)
        self.processes.append(process)
        return process

    def _wait_path(self, path: Path, deadline: float, kind: str) -> None:
        while time.monotonic() < deadline:
            if path.is_socket():
                return
            if any(p.poll() is not None for p in self.processes):
                details = []
                for log in (*self.runtime_dir.glob("*.stderr"),
                            self.runtime_dir / "weston.log"):
                    try:
                        text = log.read_text(errors="replace").strip()
                    except OSError:
                        continue
                    if text:
                        details.append(f"{log.stem}: {text[-500:]}")
                suffix = f" ({'; '.join(details)})" if details else ""
                raise RuntimeError(
                    f"GPU host exited while waiting for {kind}{suffix}")
            time.sleep(0.02)
        raise TimeoutError(f"GPU host timed out waiting for {kind}")

    def start(self, timeout: float = 8.0) -> "Session":
        deadline = time.monotonic() + timeout
        log_path = self.runtime_dir / "weston.log"
        try:
            pipewire_log = open(self.runtime_dir / "pipewire.stderr", "wb")
            self.logs.append(pipewire_log)
            self.pipewire = self._spawn(
                (str(self.runtime.pipewire),), stdout=subprocess.DEVNULL,
                stderr=pipewire_log)
            self._wait_path(self.runtime_dir / "pipewire-0", deadline, "PipeWire")
            capture_log = open(self.runtime_dir / "capture.stderr", "wb")
            self.logs.append(capture_log)
            self.capture = self._spawn(
                (str(self.runtime.capture), "--dmabuf-server",
                 str(self.frame_socket), "-", str(self.width), str(self.height),
                 str(self.fps)),
                stdout=subprocess.PIPE, stderr=capture_log)
            os.set_blocking(self.capture.stdout.fileno(), False)
            self._wait_path(self.frame_socket, deadline, "DMA-BUF transport")
            weston_argv = gpu_host.weston_command(
                self.runtime, self.width, self.height, self.wayland_socket,
                log_path, self.command)
            weston_stderr = open(self.runtime_dir / "weston.stderr", "wb")
            self.logs.append(weston_stderr)
            self.weston = self._spawn(
                weston_argv, stdout=subprocess.DEVNULL, stderr=weston_stderr)
            self._wait_path(self.input_socket, deadline, "native input")
            gpu_host.link_capture_ports(
                self.runtime, self.environment,
                timeout=max(0.1, deadline - time.monotonic()))
            self.injector = wayland_input.Injector(
                self.input_socket, self.width, self.height)
            return self
        except Exception:
            self.close()
            raise

    def fileno(self) -> int:
        if self.capture is None or self.capture.stdout is None:
            raise RuntimeError("GPU session has not started")
        return self.capture.stdout.fileno()

    def consume_ready(self) -> bool:
        """Consume one coalesced readiness notification from the capture node."""
        fd = self.fileno()
        ready = False
        while True:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                break
            if not chunk:
                raise EOFError("GPU capture transport closed")
            ready = True
            if len(chunk) < 4096:
                break
        return ready

    def close(self) -> None:
        if self.injector is not None:
            self.injector.close()
            self.injector = None
        for process in reversed(self.processes):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in reversed(self.processes):
            if process.poll() is None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
        self.processes.clear()
        for log in self.logs:
            log.close()
        self.logs.clear()
        shutil.rmtree(self.runtime_dir, ignore_errors=True)
