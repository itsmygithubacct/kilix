"""Capability-gated private Wayland/PipeWire GPU host for Kilix apps."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Mapping


SOFTWARE_RENDERERS = ("llvmpipe", "softpipe", "swrast", "swiftshader")
_RENDER_NODE = re.compile(r"renderD[0-9]+\Z")


@dataclass(frozen=True)
class GpuHostRuntime:
    root: Path
    weston: Path
    pipewire: Path
    pw_dump: Path
    pw_link: Path
    xwayland: Path
    module_map: str
    library_path: str
    render_nodes: tuple[Path, ...]

    def environment(self, runtime_dir: Path) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "LD_LIBRARY_PATH": self.library_path,
            "PIPEWIRE_CONFIG_DIR": str(self.root / "usr/share/pipewire"),
            "PIPEWIRE_MODULE_DIR": str(
                self.root / "usr/lib/x86_64-linux-gnu/pipewire-0.3"),
            "WESTON_MODULE_MAP": self.module_map,
            "PATH": f"{self.root / 'usr/bin'}:{env.get('PATH', '')}",
        })
        return env


@dataclass(frozen=True)
class GpuProbe:
    available: bool
    reason: str
    renderer: str = ""
    render_node: str = ""
    dmabuf: bool = False
    pbo: bool = False


def _safe_executable(path: Path) -> bool:
    try:
        info = path.stat()
    except OSError:
        return False
    return (
        path.is_file()
        and not path.is_symlink()
        and bool(info.st_mode & stat.S_IXUSR)
        and info.st_uid in (0, os.geteuid())
    )


def _runtime_root() -> Path | None:
    explicit = os.environ.get("KILIX_GPU_HOST_ROOT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    storage = Path(os.environ.get(
        "KILIX_STORAGE_HOME", "~/.local/gpu_terminal/kilix")).expanduser()
    staged = storage / "dependencies/gpu-host/root"
    if staged.is_dir():
        return staged.resolve()
    weston = shutil.which("weston")
    pipewire = shutil.which("pipewire")
    if weston and pipewire:
        return Path("/")
    return None


def discover_runtime() -> GpuHostRuntime | None:
    root = _runtime_root()
    if root is None:
        return None
    prefix = root / "usr" if root != Path("/") else Path("/usr")
    weston = prefix / "bin/weston"
    pipewire = prefix / "bin/pipewire"
    pw_dump = prefix / "bin/pw-dump"
    pw_link = prefix / "bin/pw-link"
    xwayland = prefix / "bin/Xwayland"
    required = (weston, pipewire, pw_dump, pw_link, xwayland)
    if not all(_safe_executable(path) for path in required):
        return None
    multiarch = prefix / "lib/x86_64-linux-gnu"
    weston_modules = multiarch / "weston"
    libweston_modules = multiarch / "libweston-14"
    pipewire_modules = multiarch / "pipewire-0.3"
    module_paths = {
        "pipewire-backend.so": libweston_modules / "pipewire-backend.so",
        "gl-renderer.so": libweston_modules / "gl-renderer.so",
        "xwayland.so": libweston_modules / "xwayland.so",
        "kiosk-shell.so": weston_modules / "kiosk-shell.so",
        "weston-keyboard": prefix / "libexec/weston-keyboard",
    }
    if not all(path.is_file() for path in module_paths.values()):
        return None
    if not pipewire_modules.is_dir():
        return None
    render_nodes = tuple(sorted(
        path for path in Path("/dev/dri").glob("renderD*")
        if _RENDER_NODE.fullmatch(path.name) and os.access(path, os.R_OK | os.W_OK)
    ))
    library_path = ":".join(map(str, (
        multiarch, weston_modules, libweston_modules)))
    inherited = os.environ.get("LD_LIBRARY_PATH")
    if inherited:
        library_path += f":{inherited}"
    module_map = ";".join(
        f"{name}={path}" for name, path in module_paths.items())
    return GpuHostRuntime(
        root, weston, pipewire, pw_dump, pw_link, xwayland,
        module_map, library_path, render_nodes)


_PORT_NAME = re.compile(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+\Z")


def link_capture_ports(runtime: GpuHostRuntime, environment: Mapping[str, str],
                       source: str = "weston.pipewire:output_1",
                       sink: str = "kilix-pw-capture:input_1",
                       timeout: float = 3.0) -> None:
    """Link the private graph without a resident desktop session manager."""
    if not _PORT_NAME.fullmatch(source) or not _PORT_NAME.fullmatch(sink):
        raise ValueError("unsafe PipeWire port name")
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        outputs = subprocess.run(
            (str(runtime.pw_link), "-o"), env=dict(environment),
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=1, check=False)
        inputs = subprocess.run(
            (str(runtime.pw_link), "-i"), env=dict(environment),
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=1, check=False)
        if source in outputs.stdout.splitlines() and sink in inputs.stdout.splitlines():
            linked = subprocess.run(
                (str(runtime.pw_link), source, sink), env=dict(environment),
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=1, check=False)
            if linked.returncode == 0:
                return
            raise RuntimeError(linked.stderr.strip() or "PipeWire link failed")
        time.sleep(0.02)
    raise TimeoutError("PipeWire capture ports did not appear")


def parse_weston_log(text: str) -> GpuProbe:
    renderer = render_node = ""
    dmabuf = pbo = False
    for line in text.splitlines():
        stripped = line.strip()
        if "Using rendering device:" in stripped:
            render_node = stripped.split("Using rendering device:", 1)[1].strip()
        elif "GL renderer:" in stripped:
            renderer = stripped.split("GL renderer:", 1)[1].strip()
        elif "dmabuf support:" in stripped:
            dmabuf = stripped.rsplit(":", 1)[1].strip().lower() != "no"
        elif "glReadPixels supports PBO:" in stripped:
            pbo = stripped.rsplit(":", 1)[1].strip().lower() == "yes"
    if not renderer:
        return GpuProbe(False, "Weston did not report an OpenGL renderer")
    if any(name in renderer.casefold() for name in SOFTWARE_RENDERERS):
        return GpuProbe(False, f"software renderer rejected: {renderer}", renderer,
                        render_node, dmabuf, pbo)
    if not render_node:
        return GpuProbe(False, "Weston did not report a DRM render node",
                        renderer, render_node, dmabuf, pbo)
    if not dmabuf:
        return GpuProbe(False, "renderer lacks DMA-BUF support",
                        renderer, render_node, dmabuf, pbo)
    return GpuProbe(True, "hardware renderer with DMA-BUF", renderer,
                    render_node, dmabuf, pbo)


def weston_command(runtime: GpuHostRuntime, width: int, height: int,
                   socket_name: str, log_path: Path,
                   command: tuple[str, ...]) -> tuple[str, ...]:
    if width <= 0 or height <= 0:
        raise ValueError("GPU host dimensions must be positive")
    if not re.fullmatch(r"wayland-[A-Za-z0-9_.-]+", socket_name):
        raise ValueError("unsafe Wayland socket name")
    if not command:
        raise ValueError("GPU host application command must not be empty")
    return (
        str(runtime.weston), "--backend=pipewire", "--renderer=gl",
        f"--width={width}", f"--height={height}", f"--socket={socket_name}",
        "--shell=kiosk", "--xwayland", "--idle-time=0",
        f"--log={log_path}", "--", *command,
    )


def app_environment(command: tuple[str, ...],
                    extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Select native Wayland browser backends without unsafe GPU switches."""
    env = dict(extra or {})
    executable = Path(command[0]).name.casefold() if command else ""
    if executable in {"firefox", "firefox-esr"}:
        env["MOZ_ENABLE_WAYLAND"] = "1"
    elif executable in {"chrome", "google-chrome", "chromium", "chromium-browser"}:
        env["OZONE_PLATFORM"] = "wayland"
    return env


def probe_runtime(runtime: GpuHostRuntime, timeout: float = 8.0) -> GpuProbe:
    """Launch a private GL compositor and prove its renderer and DMA-BUF path."""
    if not runtime.render_nodes:
        return GpuProbe(False, "no accessible DRM render node")
    session_home = Path(os.environ.get(
        "KILIX_SESSION_HOME", "~/.local/gpu_terminal/kilix/session")).expanduser()
    session_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="gpu-probe-", dir=session_home) as temporary:
        runtime_dir = Path(temporary)
        runtime_dir.chmod(0o700)
        env = runtime.environment(runtime_dir)
        log_path = runtime_dir / "weston.log"
        demo = runtime.root / "usr/bin/weston-simple-egl"
        if not _safe_executable(demo):
            return GpuProbe(False, "Weston EGL probe client is unavailable")
        pipewire = subprocess.Popen(
            (str(runtime.pipewire),), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        weston = None
        deadline = time.monotonic() + max(1.0, timeout)
        try:
            socket_path = runtime_dir / "pipewire-0"
            while not socket_path.is_socket():
                if pipewire.poll() is not None:
                    return GpuProbe(False, "private PipeWire daemon exited")
                if time.monotonic() >= deadline:
                    return GpuProbe(False, "private PipeWire daemon timed out")
                time.sleep(0.025)
            command = weston_command(
                runtime, 320, 200, f"wayland-kilix-probe-{os.getpid()}",
                log_path, (str(demo),))
            weston = subprocess.Popen(
                command, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            while time.monotonic() < deadline:
                try:
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                probe = parse_weston_log(text)
                if probe.available:
                    return probe
                if weston.poll() is not None:
                    return GpuProbe(False, f"Weston exited: {probe.reason}",
                                    probe.renderer, probe.render_node,
                                    probe.dmabuf, probe.pbo)
                time.sleep(0.025)
            return GpuProbe(False, "Weston GPU probe timed out")
        finally:
            for process in (weston, pipewire):
                if process is None or process.poll() is not None:
                    continue
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
