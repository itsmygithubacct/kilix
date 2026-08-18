import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import apprun
from kilix_sdk import gpu_host
import stream


class DirectEncodeTests(unittest.TestCase):
    def test_encoded_mux_input_copies_h264_without_second_encoder(self):
        supervisor = object.__new__(stream.StreamSupervisor)
        supervisor.runtime_dir = "/tmp"
        argv = supervisor._enc_argv(
            "hls", 1, 1280, 720, 20, 0.5, False, None, True,
            encoded=True)
        self.assertIn("h264", argv)
        self.assertEqual(argv[argv.index("-c:v") + 1], "copy")
        self.assertNotIn("rawvideo", argv)
        self.assertNotIn("libx264", argv)

    def test_encoded_feed_reliably_fans_out_identical_bytes(self):
        source_r, source_w = os.pipe()
        sink1_r, sink1_w = os.pipe()
        sink2_r, sink2_w = os.pipe()
        class Endpoint:
            def __init__(self, fd):
                self._fd = fd
            def fileno(self):
                return self._fd
        class Process:
            pass
        source = Process()
        source.stdout = Endpoint(source_r)
        sinks = []
        for fd in (sink1_w, sink2_w):
            process = Process()
            process.stdin = Endpoint(fd)
            sinks.append(process)
        feed = apprun.EncodedFeed()
        feed.attach(source)
        for sink in sinks:
            feed.add(sink)
        payload = b"\x00\x00\x00\x01encoded-once" * 1024
        os.write(source_w, payload)
        feed.read()
        feed.pump()
        self.assertEqual(os.read(sink1_r, len(payload)), payload)
        self.assertEqual(os.read(sink2_r, len(payload)), payload)
        for fd in (source_r, source_w, sink1_r, sink1_w, sink2_r, sink2_w):
            os.close(fd)

    def test_encoder_probe_cache_is_keyed_by_helper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = [root / name for name in (
                "weston", "pipewire", "pw-dump", "pw-link", "Xwayland",
                "input.so", "capture", "renderD128", "encoder")]
            for path in files:
                path.write_bytes(b"one")
            runtime = gpu_host.GpuHostRuntime(
                root, *files[:7], "modules", "/runtime/lib", (files[7],),
                files[8])
            expected = gpu_host.EncoderProbe(True, "direct", str(files[7]))
            with patch.dict(os.environ, {
                    "KILIX_CACHE_HOME": str(root / "cache"),
                    "KILIX_BUILD_DIRECTORY": str(root / "build")}), \
                    patch.object(gpu_host, "probe_encoder",
                                 return_value=expected) as probe:
                self.assertEqual(gpu_host.probe_encoder_cached(runtime), expected)
                self.assertEqual(gpu_host.probe_encoder_cached(runtime), expected)
                self.assertEqual(probe.call_count, 1)
                files[8].write_bytes(b"changed")
                self.assertEqual(gpu_host.probe_encoder_cached(runtime), expected)
                self.assertEqual(probe.call_count, 2)


if __name__ == "__main__":
    unittest.main()
