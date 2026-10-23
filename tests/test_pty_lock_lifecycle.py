"""The lock helper returns every descriptor when used in-process."""
import contextlib
import io
import fcntl
import json
import time
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import kilix_pty as pty
import kilix_pty_request as request


class LockLifecycle(unittest.TestCase):
    def test_eof_releases_descriptor_and_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            before = len(os.listdir('/proc/self/fd'))
            for _ in range(3):
                with mock.patch.object(sys, 'stdin', io.StringIO('')), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(pty.cmd_hold_lock([str(Path(directory) / 'lock')]), 0)
                self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_contended_store_lock_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = request.Store(directory)
            fd = store.open()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                started = time.monotonic()
                with mock.patch.object(request, 'WAIT_FOR_OWNER', 0.05):
                    with self.assertRaises(OSError):
                        with store.locked():
                            self.fail('acquired an owned lock')
                self.assertLess(time.monotonic() - started, 1)
            finally:
                os.close(fd)

    def test_mismatched_record_is_not_a_missing_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = request.Store(directory)
            with store.locked():
                store.write('key', {'operation_id': 'other'})
                with self.assertRaises(ValueError):
                    store.read('key')
