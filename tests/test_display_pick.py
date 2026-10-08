"""The X display picker must not hand out a display a live server still holds.

An X server listens on /tmp/.X11-unix/X<n>, creates /tmp/.X<n>-lock, and also
listens on the abstract socket of the same name. When /tmp is cleaned the two
files go but the server (and the abstract socket) stay, and Xvfb then refuses
that display with "server already running". Each sign is faked here without a
live X server: files in a scratch directory for the first two, and a real
abstract socket, bound by the test, for the third.
"""
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import stream  # noqa: E402


def free_high_display():
    """A display number nothing on this machine uses, for the abstract name."""
    for n in range(41000 + os.getpid() % 5000, 60000):
        if not stream.display_in_use(n):
            return n
    raise RuntimeError("no free display number")


class DisplayPickerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.x11 = Path(self.tmp.name, "x11")
        self.locks = Path(self.tmp.name, "tmp")
        self.runtime = Path(self.tmp.name, "locks")
        for directory in (self.x11, self.locks, self.runtime):
            directory.mkdir()
        patcher = mock.patch.multiple(stream, X11_DIR=str(self.x11), X_LOCK_DIR=str(self.locks))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.n = free_high_display()
        self.listeners = []

    def tearDown(self):
        for listener in self.listeners:
            listener.close()

    def listen_abstract(self, n):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(f"\0/tmp/.X11-unix/X{n}")
        listener.listen(1)
        self.listeners.append(listener)

    def supervisor(self):
        picker = object.__new__(stream.StreamSupervisor)
        picker.lockdir, picker._locks = str(self.runtime), []
        self.addCleanup(lambda: [os.close(fd) for fd in picker._locks])
        return picker

    def test_a_free_display_is_free(self):
        self.assertFalse(stream.display_in_use(self.n))
        self.assertEqual(self.supervisor().pick_display(self.n, self.n + 1), self.n)

    def test_the_socket_file_marks_a_display_taken(self):
        (self.x11 / f"X{self.n}").touch()
        self.assertTrue(stream.display_in_use(self.n))
        self.assertEqual(self.supervisor().pick_display(self.n, self.n + 2), self.n + 1)

    def test_the_lock_file_marks_a_display_taken(self):
        (self.locks / f".X{self.n}-lock").write_text("     123\n")
        self.assertTrue(stream.display_in_use(self.n))
        self.assertEqual(self.supervisor().pick_display(self.n, self.n + 2), self.n + 1)

    def test_a_live_abstract_socket_marks_a_display_taken_with_no_files_at_all(self):
        # The owner's Xvfb :60 after its files were cleaned: nothing on disk, still listening.
        self.listen_abstract(self.n)
        self.assertEqual(os.listdir(self.x11), [])
        self.assertEqual(os.listdir(self.locks), [])
        self.assertTrue(stream.display_in_use(self.n))
        self.assertEqual(self.supervisor().pick_display(self.n, self.n + 2), self.n + 1)

    def test_the_abstract_check_works_without_proc(self):
        # Where /proc/net/unix cannot be read, a non-blocking connect asks instead.
        real_open = open

        def refuse(path, *args, **kwargs):
            if str(path) == "/proc/net/unix":
                raise OSError("no /proc")
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", refuse):
            self.assertFalse(stream.display_in_use(self.n))
            self.listen_abstract(self.n)
            self.assertTrue(stream.display_in_use(self.n))

    def test_each_display_in_a_range_is_checked_and_an_exhausted_range_fails(self):
        (self.x11 / f"X{self.n}").touch()
        (self.locks / f".X{self.n + 1}-lock").touch()
        self.listen_abstract(self.n + 2)
        with self.assertRaises(RuntimeError):
            self.supervisor().pick_display(self.n, self.n + 3)

    def test_the_flock_still_excludes_a_second_picker(self):
        first, second = self.supervisor(), self.supervisor()
        self.assertEqual(first.pick_display(self.n, self.n + 2), self.n)
        self.assertEqual(second.pick_display(self.n, self.n + 2), self.n + 1)

    def test_a_display_that_appears_after_the_lock_is_given_up(self):
        picker = self.supervisor()
        calls = []
        real = stream.display_in_use

        def appears_late(n):
            calls.append(n)
            return real(n) or (n == self.n and calls.count(n) > 1)

        with mock.patch.object(stream, "display_in_use", appears_late):
            self.assertEqual(picker.pick_display(self.n, self.n + 2), self.n + 1)


if __name__ == "__main__":
    unittest.main()
