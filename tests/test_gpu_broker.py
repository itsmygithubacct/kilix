import os
from pathlib import Path
import tempfile
import unittest

from kilix_sdk import gpu_broker


class GpuBrokerTests(unittest.TestCase):
    def test_idle_shutdown_is_bounded_and_never_interrupts_a_lease(self):
        self.assertEqual(gpu_broker.IDLE_TIMEOUT, 5.0)
        started = 100.0
        self.assertFalse(gpu_broker._idle_expired(0, started, 104.999))
        self.assertTrue(gpu_broker._idle_expired(0, started, 105.0))
        self.assertFalse(gpu_broker._idle_expired(1, started, 1000.0))

    def test_shared_host_has_six_uniform_outputs(self):
        self.assertEqual(gpu_broker.SLOTS, ((1280, 720),) * 6)
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "weston.ini"
            gpu_broker._write_config(config)
            text = config.read_text(encoding="utf-8")
            self.assertIn("num-outputs=6", text)
            for slot in range(6):
                self.assertIn(f"name=pipewire-{slot}\nmode=1280x720", text)
            self.assertEqual(os.stat(config).st_mode & 0o777, 0o600)

    def test_control_parent_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "host"
            gpu_broker._private_parent(parent)
            self.assertEqual(os.stat(parent).st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
