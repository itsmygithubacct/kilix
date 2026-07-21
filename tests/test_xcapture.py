"""Live XDamage/MIT-SHM capture against an isolated Xvfb."""

import os
import select
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

try:
    from Xlib import X, display as xdisplay
    import xcapture
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

XVFB = shutil.which("Xvfb")


@unittest.skipUnless(XVFB and HAVE_DEPS, "needs Xvfb + PIL + python-xlib")
class XDamageCaptureE2E(unittest.TestCase):
    WIDTH, HEIGHT = 320, 200

    @classmethod
    def setUpClass(cls):
        rfd, wfd = os.pipe()
        cls.xvfb = subprocess.Popen(
            [XVFB, "-displayfd", str(wfd), "-screen", "0",
             f"{cls.WIDTH}x{cls.HEIGHT}x24", "-nolisten", "tcp"],
            pass_fds=(wfd,), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls._stop_xvfb)
        os.close(wfd)
        ready, _, _ = select.select([rfd], [], [], 15)
        if not ready:
            os.close(rfd)
            raise RuntimeError("Xvfb did not report a display number")
        number = os.read(rfd, 32).strip()
        os.close(rfd)
        if cls.xvfb.poll() is not None or not number:
            raise RuntimeError("Xvfb exited during startup")
        cls.display_name = f":{int(number)}"
        cls.xd = xdisplay.Display(cls.display_name)

    @classmethod
    def _stop_xvfb(cls):
        xd = getattr(cls, "xd", None)
        if xd is not None:
            xd.close()
        process = getattr(cls, "xvfb", None)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def test_damage_wakes_and_updates_exact_snapshot(self):
        try:
            capture = xcapture.XDamageCapture(
                self.display_name, self.WIDTH, self.HEIGHT,
                draw_cursor=False)
        except xcapture.CaptureUnavailable as error:
            self.skipTest(str(error))
        window = None
        try:
            self.assertIsNone(capture.pump())
            screen = self.xd.screen()
            window = screen.root.create_window(
                10, 12, 64, 32, 0, screen.root_depth,
                X.InputOutput, X.CopyFromParent,
                background_pixel=screen.white_pixel)
            window.map()
            self.xd.sync()

            deadline = time.monotonic() + 3
            update = None
            while update is None and time.monotonic() < deadline:
                ready, _, _ = select.select(
                    [capture], [], [], max(0, deadline - time.monotonic()))
                if not ready:
                    break
                update = capture.pump()
            self.assertIsNotNone(update, "mapped window produced no damage")
            frame, rect = update
            x, y, width, height = rect
            self.assertLessEqual(x, 10)
            self.assertLessEqual(y, 12)
            self.assertGreaterEqual(x + width, 74)
            self.assertGreaterEqual(y + height, 44)
            self.assertEqual(len(frame), self.WIDTH * self.HEIGHT * 3)
            at = (20 * self.WIDTH + 20) * 3
            self.assertEqual(frame[at:at + 3], b"\xff\xff\xff")
        finally:
            if window is not None:
                window.destroy()
                self.xd.sync()
            capture.close()


if __name__ == "__main__":
    unittest.main()
