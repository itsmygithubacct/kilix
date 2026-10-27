"""Real pipe framing controls for Xvfb readiness; no display or synthetic input."""
import os
import threading
import unittest

import x11_sandbox


class DisplayNumberTests(unittest.TestCase):
    def test_partial_number_is_not_ready_before_its_newline(self):
        read, write = os.pipe()
        started, done = threading.Event(), threading.Event()
        observed = []

        def reader():
            started.set()
            try:
                observed.append(x11_sandbox.read_display_number(read, 2))
            except BaseException as error:
                observed.append(error)
            finally:
                done.set()

        worker = threading.Thread(target=reader)
        try:
            os.write(write, b"0")
            worker.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(done.wait(.1), "a digit without its newline is not readiness")
            os.write(write, b"\n")
            self.assertTrue(done.wait(2))
            self.assertEqual(observed, [0])
        finally:
            os.close(write)
            worker.join(3)
            os.close(read)
        self.assertFalse(worker.is_alive())

    def test_partial_eof_and_invalid_lines_refuse(self):
        for data in (b"12", b"\n", b"12\nextra", b"x\n", b"1" * 33 + b"\n"):
            with self.subTest(data=data):
                read, write = os.pipe()
                try:
                    os.write(write, data)
                    os.close(write)
                    with self.assertRaises(RuntimeError):
                        x11_sandbox.read_display_number(read, 1)
                finally:
                    os.close(read)

    def test_no_writer_data_reaches_the_existing_deadline(self):
        read, write = os.pipe()
        try:
            with self.assertRaisesRegex(RuntimeError, "complete display number"):
                x11_sandbox.read_display_number(read, .03)
        finally:
            os.close(write)
            os.close(read)


if __name__ == '__main__':
    unittest.main()
