"""One credential-gated Weston/PipeWire host shared by local Kilix panes."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

from . import gpu_host
from .process_signals import TERMINATION_SIGNALS


SLOTS = ((1280, 720),) * 6
IDLE_TIMEOUT = 5.0


def _paths(session_home: Path) -> tuple[Path, Path]:
    return session_home / "gpu-host.sock", session_home / "gpu-host.lock"


def _private_parent(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _idle_expired(client_count: int, idle_since: float, now: float) -> bool:
    """Return true only after the last lease has been absent for the bound."""
    return client_count == 0 and now - idle_since >= IDLE_TIMEOUT


def _connect(path: Path, timeout: float) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    client.connect(str(path))
    return client


def acquire(session_home: Path, width: int, height: int,
            timeout: float = 8.0) -> tuple[socket.socket, dict]:
    """Start the shared host if needed and lease its closest free output."""
    _private_parent(session_home)
    control, lock_path = _paths(session_home)
    deadline = time.monotonic() + timeout
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            client = _connect(control, 0.25)
        except OSError:
            try:
                control.unlink()
            except FileNotFoundError:
                pass
            daemon_env = dict(os.environ)
            package_root = str(Path(__file__).resolve().parents[1])
            inherited = daemon_env.get("PYTHONPATH")
            daemon_env["PYTHONPATH"] = (f"{package_root}:{inherited}"
                                         if inherited else package_root)
            subprocess.Popen(
                (sys.executable, "-m", "kilix_sdk.gpu_broker", "--daemon",
                 str(session_home)), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=daemon_env, start_new_session=True, close_fds=True)
            while True:
                try:
                    client = _connect(control, 0.25)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("shared GPU host did not start")
                    time.sleep(0.025)
    finally:
        os.close(lock_fd)
    request = json.dumps({"width": int(width), "height": int(height)}) + "\n"
    client.sendall(request.encode("ascii"))
    response = bytearray()
    while b"\n" not in response:
        chunk = client.recv(4096)
        if not chunk:
            client.close()
            raise RuntimeError("shared GPU host closed during allocation")
        response.extend(chunk)
        if len(response) > 16384:
            client.close()
            raise RuntimeError("invalid shared GPU host response")
    payload = json.loads(response.split(b"\n", 1)[0])
    if "error" in payload:
        client.close()
        raise RuntimeError(payload["error"])
    client.settimeout(None)
    return client, payload


def _wait_path(path: Path, processes: tuple[subprocess.Popen, ...],
               deadline: float) -> None:
    while not path.is_socket():
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("shared GPU host process exited during startup")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"shared GPU host timed out waiting for {path.name}")
        time.sleep(0.025)


def _write_config(path: Path) -> None:
    lines = ["[pipewire]", f"num-outputs={len(SLOTS)}", ""]
    for index, (width, height) in enumerate(SLOTS):
        lines.extend(("[output]", f"name=pipewire-{index}",
                      f"mode={width}x{height}", ""))
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o600)


def run_daemon(session_home: Path) -> int:
    runtime = gpu_host.discover_runtime()
    if runtime is None:
        return 1
    _private_parent(session_home)
    control_path, _ = _paths(session_home)
    runtime_dir = Path(tempfile.mkdtemp(prefix="shared-gpu-", dir=session_home))
    runtime_dir.chmod(0o700)
    input_path = runtime_dir / "input.sock"
    wayland_socket = f"wayland-kilix-shared-{os.getpid()}"
    environment = runtime.environment(runtime_dir)
    environment["KILIX_WESTON_INPUT_SOCKET"] = str(input_path)
    config_path = runtime_dir / "weston.ini"
    _write_config(config_path)
    logs = []
    processes = []
    server = None
    selector = selectors.DefaultSelector()
    clients: dict[socket.socket, int] = {}
    used: set[int] = set()
    pending_client = None
    stopping = False

    def stop(_signal=None, _frame=None):
        nonlocal stopping
        stopping = True

    for termination_signal in TERMINATION_SIGNALS:
        signal.signal(termination_signal, stop)
    try:
        pipewire_log = open(runtime_dir / "pipewire.stderr", "wb")
        weston_log = open(runtime_dir / "weston.stderr", "wb")
        logs.extend((pipewire_log, weston_log))
        pipewire = subprocess.Popen(
            (str(runtime.pipewire),), env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=pipewire_log, start_new_session=True)
        processes.append(pipewire)
        deadline = time.monotonic() + 8.0
        _wait_path(runtime_dir / "pipewire-0", (pipewire,), deadline)
        weston = subprocess.Popen(
            gpu_host.shared_weston_command(
                runtime, wayland_socket, runtime_dir / "weston.log", config_path),
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=weston_log, start_new_session=True)
        processes.append(weston)
        _wait_path(input_path, (pipewire, weston), deadline)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.setblocking(False)
        old_mask = os.umask(0o077)
        try:
            server.bind(str(control_path))
        finally:
            os.umask(old_mask)
        server.listen(len(SLOTS))
        selector.register(server, selectors.EVENT_READ)
        idle_since = time.monotonic()
        offsets = []
        next_x = 0
        for width, _height in SLOTS:
            offsets.append(next_x)
            next_x += width

        while not stopping and all(p.poll() is None for p in processes):
            if _idle_expired(len(clients), idle_since, time.monotonic()):
                break
            for key, _mask in selector.select(0.5):
                if key.fileobj is server:
                    if pending_client is not None:
                        continue
                    client, _ = server.accept()
                    credentials = client.getsockopt(
                        socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    uid = int.from_bytes(credentials[4:8], sys.byteorder)
                    if uid != os.geteuid():
                        client.close()
                        continue
                    client.settimeout(1.0)
                    try:
                        raw = bytearray()
                        while b"\n" not in raw and len(raw) <= 4096:
                            chunk = client.recv(4096)
                            if not chunk:
                                break
                            raw.extend(chunk)
                        request = json.loads(raw.split(b"\n", 1)[0])
                        wanted = (max(1, int(request["width"])),
                                  max(1, int(request["height"])))
                        available = [i for i in range(len(SLOTS)) if i not in used]
                        if not available:
                            raise RuntimeError("all shared GPU outputs are in use")
                        # The routing shell assigns new clients to its first
                        # free output. Serialize through first paint so broker
                        # and shell observe exactly the same allocation order.
                        slot = available[0]
                        used.add(slot)
                        clients[client] = slot
                        width, height = SLOTS[slot]
                        payload = {
                            "slot": slot, "width": width, "height": height,
                            "offset_x": offsets[slot], "offset_y": 0,
                            "runtime_dir": str(runtime_dir),
                            "wayland_socket": wayland_socket,
                            "input_socket": str(input_path),
                        }
                        client.sendall(json.dumps(payload).encode("ascii") + b"\n")
                        client.setblocking(False)
                        selector.register(client, selectors.EVENT_READ)
                        pending_client = client
                    except Exception as error:
                        try:
                            client.sendall(json.dumps({"error": str(error)}).encode() + b"\n")
                        except OSError:
                            pass
                        client.close()
                else:
                    client = key.fileobj
                    try:
                        data = client.recv(1)
                    except OSError:
                        data = b""
                    if data.startswith(b"R"):
                        if pending_client is client:
                            pending_client = None
                    elif not data:
                        selector.unregister(client)
                        used.discard(clients.pop(client))
                        if pending_client is client:
                            pending_client = None
                        client.close()
                        if not clients:
                            idle_since = time.monotonic()
        return 0 if not stopping else 0
    finally:
        for client in list(clients):
            client.close()
        if server is not None:
            server.close()
        selector.close()
        try:
            control_path.unlink()
        except FileNotFoundError:
            pass
        for process in reversed(processes):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in reversed(processes):
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        for log in logs:
            log.close()
        shutil.rmtree(runtime_dir, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--daemon":
        raise SystemExit("usage: python -m kilix_sdk.gpu_broker --daemon SESSION_HOME")
    raise SystemExit(run_daemon(Path(sys.argv[2]).expanduser().resolve()))
