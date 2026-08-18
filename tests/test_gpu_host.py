import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from kilix_sdk import gpu_host


HARDWARE_LOG = """
Using rendering device: /dev/dri/renderD128
EGL vendor: Mesa Project
               dmabuf support: modifiers
               glReadPixels supports PBO: yes
GL renderer: NV166
"""


class GpuHostTests(unittest.TestCase):
    def test_documented_installer_builds_every_required_helper(self):
        installer = (ROOT / "scripts/install-gpu-host.sh").read_text()
        probe = (ROOT / "config/gpu_host_probe.py").read_text()
        for builder in ("build-weston-input.sh", "build-gpu-capture.sh",
                        "build-kilix-kiosk-shell.sh"):
            self.assertIn(builder, installer)
        self.assertIn('"install": "scripts/install-gpu-host.sh"', probe)

    def test_hardware_probe_requires_real_renderer_and_dmabuf(self):
        probe = gpu_host.parse_weston_log(HARDWARE_LOG)
        self.assertTrue(probe.available)
        self.assertEqual(probe.renderer, "NV166")
        self.assertTrue(probe.dmabuf)
        self.assertTrue(probe.pbo)

        software = gpu_host.parse_weston_log(
            HARDWARE_LOG.replace("NV166", "llvmpipe (LLVM 19.1.7)"))
        self.assertFalse(software.available)
        self.assertIn("software renderer rejected", software.reason)

        no_dmabuf = gpu_host.parse_weston_log(
            HARDWARE_LOG.replace("dmabuf support: modifiers", "dmabuf support: no"))
        self.assertFalse(no_dmabuf.available)
        self.assertIn("DMA-BUF", no_dmabuf.reason)

    def test_staged_runtime_is_discovered_without_system_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = (
                "usr/bin/weston", "usr/bin/pipewire", "usr/bin/pw-dump",
                "usr/bin/pw-link",
                "usr/bin/Xwayland", "usr/lib/x86_64-linux-gnu/weston/kiosk-shell.so",
                "usr/lib/x86_64-linux-gnu/libweston-14/pipewire-backend.so",
                "usr/lib/x86_64-linux-gnu/libweston-14/gl-renderer.so",
                "usr/lib/x86_64-linux-gnu/libweston-14/xwayland.so",
                "usr/libexec/weston-keyboard",
                "usr/lib/x86_64-linux-gnu/pipewire-0.3/placeholder.so",
                "build/libraries/gpu-host/kilix-weston-input.so",
                "build/libraries/gpu-host/kilix-pw-capture",
                "build/libraries/gpu-host/kilix-kiosk-shell.so",
            )
            for relative in paths:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
                if ("/bin/" in relative or "/libexec/" in relative
                        or relative.endswith("kilix-weston-input.so")
                        or relative.endswith("kilix-pw-capture")
                        or relative.endswith("kilix-kiosk-shell.so")):
                    path.chmod(0o700)
            with patch.dict(os.environ, {
                    "KILIX_GPU_HOST_ROOT": str(root),
                    "KILIX_BUILD_DIRECTORY": str(root / "build")}):
                runtime = gpu_host.discover_runtime()
            self.assertIsNotNone(runtime)
            self.assertIn("pipewire-backend.so=", runtime.module_map)

    def test_weston_command_is_private_gpu_pipewire_host(self):
        runtime = gpu_host.GpuHostRuntime(
            Path("/runtime"), Path("/runtime/weston"), Path("/runtime/pipewire"),
            Path("/runtime/pw-dump"), Path("/runtime/pw-link"),
            Path("/runtime/Xwayland"), Path("/runtime/kilix-input.so"),
            Path("/runtime/kilix-pw-capture"),
            "modules", "/runtime/lib", (Path("/dev/dri/renderD128"),))
        command = gpu_host.weston_command(
            runtime, 1280, 720, "wayland-kilix-1", Path("/runtime/weston.log"),
            ("firefox-esr", "about:blank"))
        self.assertIn("--backend=pipewire", command)
        self.assertIn("--renderer=gl", command)
        self.assertIn("--xwayland", command)
        self.assertIn("--modules=kilix-weston-input.so", command)
        self.assertNotIn("--no-sandbox", command)

    def test_browser_environment_selects_native_wayland(self):
        self.assertEqual(
            gpu_host.app_environment(("firefox-esr",))["MOZ_ENABLE_WAYLAND"], "1")
        self.assertEqual(
            gpu_host.app_environment(("google-chrome",))["OZONE_PLATFORM"], "wayland")
        self.assertEqual(gpu_host.app_environment(("xterm",)), {})

    def test_pipewire_link_retries_until_formats_are_negotiated(self):
        runtime = gpu_host.GpuHostRuntime(
            Path("/runtime"), Path("/runtime/weston"), Path("/runtime/pipewire"),
            Path("/runtime/pw-dump"), Path("/runtime/pw-link"),
            Path("/runtime/Xwayland"), Path("/runtime/kilix-input.so"),
            Path("/runtime/kilix-pw-capture"),
            "modules", "/runtime/lib", (Path("/dev/dri/renderD128"),))
        ready_out = subprocess.CompletedProcess([], 0,
            "weston.pipewire:output_1\n", "")
        ready_in = subprocess.CompletedProcess([], 0,
            "kilix-pw-capture:input_1\n", "")
        negotiating = subprocess.CompletedProcess([], 1, "", "failed: Invalid argument")
        linked = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(gpu_host.subprocess, "run", side_effect=(
                ready_out, ready_in, negotiating, ready_out, ready_in, linked)) as run, \
                patch.object(gpu_host.time, "sleep"):
            gpu_host.link_capture_ports(runtime, {}, timeout=1)
        self.assertEqual(run.call_count, 6)

    def test_probe_cache_is_keyed_by_runtime_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = [root / name for name in ("weston", "pipewire", "pw-dump",
                                               "pw-link", "Xwayland", "input.so",
                                               "capture", "renderD128")]
            for path in files:
                path.write_bytes(b"one")
            runtime = gpu_host.GpuHostRuntime(
                root, *files[:7], "modules", "/runtime/lib", (files[7],))
            expected = gpu_host.GpuProbe(True, "cached", "NV166", str(files[7]),
                                         True, True)
            with patch.dict(os.environ, {"KILIX_CACHE_HOME": str(root / "cache"),
                                         "KILIX_BUILD_DIRECTORY": str(root / "build")}), \
                    patch.object(gpu_host, "probe_runtime", return_value=expected) as probe:
                self.assertEqual(gpu_host.probe_cached(runtime), expected)
                self.assertEqual(gpu_host.probe_cached(runtime), expected)
                self.assertEqual(probe.call_count, 1)
                files[6].write_bytes(b"changed")
                self.assertEqual(gpu_host.probe_cached(runtime), expected)
                self.assertEqual(probe.call_count, 2)


if __name__ == "__main__":
    unittest.main()
