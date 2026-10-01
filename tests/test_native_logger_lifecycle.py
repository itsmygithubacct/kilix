import importlib.util
import os
from pathlib import Path
import select
import subprocess
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/native-window-log.py'
spec = importlib.util.spec_from_file_location('native_logger_lifetime', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NativeLoggerLifecycleTests(unittest.TestCase):
    def test_kernel_handle_observes_real_parent_exit(self):
        owner = subprocess.Popen(['sleep', '60'])
        try:
            with module.ParentLifetime(owner.pid) as parent:
                self.assertTrue(parent.active())
                fd = parent.fd
                owner.terminate()
                owner.wait(timeout=2)
                self.assertTrue(select.select([fd], [], [], 2)[0])
                self.assertFalse(parent.active())
                # A later numeric PID lookup cannot change the held handle.
                with mock.patch.object(module.os, 'kill', side_effect=AssertionError('numeric lookup')):
                    self.assertFalse(parent.active())
            with self.assertRaises(OSError):
                os.fstat(fd)
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait()

    def test_pre_acquisition_pid_reuse_is_rejected_by_expected_start_tick(self):
        owner = subprocess.Popen(['sleep', '60'])
        try:
            tick = int(Path(f'/proc/{owner.pid}/stat').read_text().rsplit(') ', 1)[1].split()[19])
            with module.ParentLifetime(owner.pid, tick + 1) as parent:
                self.assertFalse(parent.active())
                self.assertIsNone(parent.fd)
            self.assertIsNone(owner.poll())
            with module.ParentLifetime(owner.pid, tick) as parent:
                self.assertTrue(parent.active())
        finally:
            owner.terminate()
            owner.wait(timeout=2)

    def test_explicit_no_parent_observer_stays_persistent(self):
        for value in (None, 0):
            with mock.patch.object(module.os, 'pidfd_open', side_effect=AssertionError('no owner')):
                with module.ParentLifetime(value) as parent:
                    self.assertTrue(parent.active())
                    self.assertIsNone(parent.fd)

    def test_exited_parent_is_not_replaced_by_persistent_mode(self):
        with mock.patch.object(module.os, 'pidfd_open', side_effect=ProcessLookupError):
            with module.ParentLifetime(12345) as parent:
                self.assertFalse(parent.active())
                self.assertIsNone(parent.fd)


if __name__ == '__main__':
    unittest.main()
