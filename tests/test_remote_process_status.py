"""The root waiter can be between collecting exit and publishing its status."""
import os
from pathlib import Path
import signal
import struct
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_sdk.process_pin import RemoteProcess, UnknownExitStatus


class RemoteStatusPublication(unittest.TestCase):
    def test_reaped_process_waits_for_its_still_open_status_writer(self):
        for code in (0, 7, -signal.SIGKILL):
            with self.subTest(code=code):
                read_fd, write_fd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
                identity_fd = os.open(os.devnull, os.O_RDONLY)
                try:
                    process = RemoteProcess(100, identity_fd, read_fd, ['fixture'],
                                            ops=SimpleNamespace(reaped=lambda fd: True))
                    self.assertIsNone(process.poll())
                    os.write(write_fd, struct.pack('!i', code))
                    self.assertEqual(process.wait(timeout=1), code)
                    self.assertEqual(process.poll(), code)
                finally:
                    os.close(identity_fd)
                    os.close(read_fd)
                    os.close(write_fd)

    def test_missing_status_is_unknown_only_after_writer_eof_and_exact_reap(self):
        read_fd, write_fd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        identity_fd = os.open(os.devnull, os.O_RDONLY)
        try:
            process = RemoteProcess(100, identity_fd, read_fd, ['fixture'],
                                    ops=SimpleNamespace(reaped=lambda fd: True))
            os.close(write_fd)
            self.assertIs(process.poll(), UnknownExitStatus.REAPED_ELSEWHERE)
        finally:
            os.close(identity_fd)
            os.close(read_fd)
