import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AppRunSignalTests(unittest.TestCase):
    def test_gpu_broker_handles_the_same_termination_set(self):
        broker = (ROOT / "config" / "kilix_sdk" / "gpu_broker.py").read_text()
        self.assertIn("for termination_signal in TERMINATION_SIGNALS", broker)

    def test_termination_signals_unwind_through_finally(self):
        program = r'''
import os
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0, sys.argv[1])
from kilix_sdk.process_signals import install_cleanup_signal_handlers

marker = Path(sys.argv[2])
install_cleanup_signal_handlers()
print("ready", flush=True)
try:
    while True:
        signal.pause()
finally:
    marker.write_text("cleaned\n", encoding="ascii")
'''
        for termination_signal in (
                signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
            with self.subTest(signal=termination_signal.name), \
                    tempfile.TemporaryDirectory() as temporary:
                marker = Path(temporary) / "cleaned"
                process = subprocess.Popen(
                    (sys.executable, "-c", program, str(ROOT / "config"),
                     str(marker)), stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(process.stdout.readline().strip(), "ready")
                    os.kill(process.pid, termination_signal)
                    stdout, stderr = process.communicate(timeout=3)
                    self.assertEqual(process.returncode, 0, (stdout, stderr))
                    self.assertEqual(marker.read_text(encoding="ascii"),
                                     "cleaned\n")
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    unittest.main()
