"""Lifecycle for one application slot in Kilix's shared GPU host."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import select
import subprocess
import tempfile
import time

from . import gpu_broker, gpu_host, wayland_input


class Session:
    def __init__(self, runtime: gpu_host.GpuHostRuntime, command: tuple[str, ...],
                 width: int, height: int, session_home: Path, fps: int = 60):
        self.runtime, self.command = runtime, command
        self.width, self.height = width, height
        self.requested_width, self.requested_height = width, height
        self.session_home = session_home
        self.fps = min(240, max(1, int(fps)))
        self.runtime_dir = self.local_dir = None
        self.frame_socket = self.input_socket = None
        self.wayland_socket = ""
        self.environment = {}
        self.processes: list[subprocess.Popen] = []
        self.logs = []
        self.capture = self.app = self.weston = None
        self.injector = None
        self.lease = None
        self.slot = -1

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
                if self.local_dir:
                    for log in self.local_dir.glob("*.stderr"):
                        try:
                            text = log.read_text(errors="replace").strip()
                        except OSError:
                            continue
                        if text:
                            details.append(f"{log.stem}: {text[-500:]}")
                suffix = f" ({'; '.join(details)})" if details else ""
                raise RuntimeError(
                    f"GPU slot exited while waiting for {kind}{suffix}")
            time.sleep(0.02)
        raise TimeoutError(f"GPU slot timed out waiting for {kind}")

    def start(self, timeout: float = 8.0) -> "Session":
        deadline = time.monotonic() + timeout
        try:
            self.lease, allocation = gpu_broker.acquire(
                self.session_home, self.width, self.height, timeout)
            self.slot = int(allocation["slot"])
            self.width, self.height = (int(allocation["width"]),
                                       int(allocation["height"]))
            self.runtime_dir = Path(allocation["runtime_dir"])
            self.wayland_socket = str(allocation["wayland_socket"])
            self.input_socket = Path(allocation["input_socket"])
            self.local_dir = Path(tempfile.mkdtemp(
                prefix=f"gpu-slot-{self.slot}-", dir=self.session_home))
            self.local_dir.chmod(0o700)
            self.frame_socket = self.runtime_dir / (
                f"frame-{self.slot}-{os.getpid()}-{self.local_dir.name[-6:]}.sock")
            self.environment = self.runtime.environment(self.runtime_dir)
            self.environment.update(gpu_host.app_environment(self.command))
            self.environment["WAYLAND_DISPLAY"] = self.wayland_socket
            node_name = f"kilix-pw-capture-{self.slot}"
            self.environment["KILIX_CAPTURE_NODE_NAME"] = node_name

            capture_log = open(self.local_dir / "capture.stderr", "wb")
            app_log = open(self.local_dir / "app.stderr", "wb")
            self.logs.extend((capture_log, app_log))
            source_name = f"weston.pipewire-{self.slot}"
            self.capture = self._spawn(
                (str(self.runtime.capture), "--dmabuf-server",
                 str(self.frame_socket), source_name,
                 str(self.width), str(self.height), str(self.fps)),
                stdout=subprocess.PIPE, stderr=capture_log)
            os.set_blocking(self.capture.stdout.fileno(), False)
            self._wait_path(self.frame_socket, deadline, "DMA-BUF transport")
            self.injector = wayland_input.Injector(
                self.input_socket, self.width, self.height,
                int(allocation["offset_x"]), int(allocation["offset_y"]))
            # Move the shared seat to this output before the first toplevel is
            # created. Kiosk shell then assigns the root surface to the focused
            # output; dialogs inherit their parent's assignment.
            self.injector.position()
            self.injector.frame_rate(self.fps)
            self.app = self._spawn(
                self.command, stdout=subprocess.DEVNULL, stderr=app_log)
            self.weston = self.app  # AppPane monitors the application lifetime.
            gpu_host.link_capture_ports(
                self.runtime, self.environment,
                source=f"{source_name}:output_1",
                sink=f"{node_name}:input_1",
                timeout=max(0.1, deadline - time.monotonic()))
            ready, _, _ = select.select(
                (self.capture.stdout,), (), (),
                max(0.1, deadline - time.monotonic()))
            if not ready:
                raise TimeoutError("GPU slot timed out waiting for first frame")
            self.lease.sendall(b"R")
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
            if process.poll() is not None:
                continue
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
        if self.lease is not None:
            self.lease.close()
            self.lease = None
        if self.local_dir is not None:
            shutil.rmtree(self.local_dir, ignore_errors=True)
            self.local_dir = None
