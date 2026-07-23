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

            # A browser paints an already mapped client window later, after
            # network responses arrive. Root damage includes visible inferior
            # drawing, so that later repaint must independently wake capture.
            while capture.pump() is not None:
                pass
            gc = window.create_gc(foreground=screen.black_pixel)
            window.fill_rectangle(gc, 0, 0, 64, 32)
            self.xd.sync()
            deadline = time.monotonic() + 3
            update = None
            while update is None and time.monotonic() < deadline:
                ready, _, _ = select.select(
                    [capture], [], [], max(0, deadline - time.monotonic()))
                if not ready:
                    break
                update = capture.pump()
            self.assertIsNotNone(
                update, "repainting a mapped child produced no damage")
            frame, rect = update
            self.assertEqual(frame[at:at + 3], b"\x00\x00\x00")
            gc.free()
        finally:
            if window is not None:
                window.destroy()
                self.xd.sync()
            capture.close()

    def test_damage_arriving_after_notify_is_not_cleared_unseen(self):
        """The server region, not an already-delivered event, is authoritative.

        This recreates the browser-startup race: consume the only NonEmpty
        notification, render again while the Damage object is still nonempty,
        then atomically extract it. The second render produces no new wakeup,
        but it must still be present in the extracted region.
        """
        try:
            capture = xcapture.XDamageCapture(
                self.display_name, self.WIDTH, self.HEIGHT,
                draw_cursor=False)
        except xcapture.CaptureUnavailable as error:
            self.skipTest(str(error))
        window = None
        try:
            screen = self.xd.screen()
            window = screen.root.create_window(
                20, 24, 96, 48, 0, screen.root_depth,
                X.InputOutput, X.CopyFromParent,
                background_pixel=screen.black_pixel)
            window.map()
            self.xd.sync()
            self.assertTrue(select.select([capture], [], [], 3)[0])
            capture.pump()

            gc = window.create_gc(foreground=screen.white_pixel)
            window.fill_rectangle(gc, 2, 3, 4, 5)
            self.xd.sync()
            self.assertTrue(select.select([capture], [], [], 3)[0])
            event = capture.events.next_event()
            self.assertEqual(event.damage, capture.damage_id)

            # No DamageSubtract has happened, so NonEmpty deliberately emits
            # no second event for this later, disjoint repaint.
            window.fill_rectangle(gc, 60, 30, 5, 6)
            self.xd.sync()
            rect = capture._take_damage()
            self.assertIsNotNone(rect)
            x, y, width, height = rect
            self.assertLessEqual(x, 22)
            self.assertLessEqual(y, 27)
            self.assertGreaterEqual(x + width, 85)
            self.assertGreaterEqual(y + height, 60)
            gc.free()
        finally:
            if window is not None:
                window.destroy()
                self.xd.sync()
            capture.close()

    def test_damage_queued_during_reply_is_drained_without_another_wakeup(self):
        """A python-xlib queued event must not strand nonempty damage.

        ReplyRequest consumes the X socket while waiting for FetchRegion.  If
        it encounters a newer DamageNotify first, python-xlib queues that event
        in memory and leaves the socket empty.  Recreate that state after the
        first extraction and verify one pump captures both paints.
        """
        try:
            capture = xcapture.XDamageCapture(
                self.display_name, self.WIDTH, self.HEIGHT,
                draw_cursor=False)
        except xcapture.CaptureUnavailable as error:
            self.skipTest(str(error))
        window = None
        try:
            screen = self.xd.screen()
            window = screen.root.create_window(
                20, 24, 96, 48, 0, screen.root_depth,
                X.InputOutput, X.CopyFromParent,
                background_pixel=screen.black_pixel)
            window.map()
            self.xd.sync()
            self.assertTrue(select.select([capture], [], [], 3)[0])
            capture.pump()

            gc = window.create_gc(foreground=screen.white_pixel)
            window.fill_rectangle(gc, 2, 3, 4, 5)
            self.xd.sync()
            self.assertTrue(select.select([capture], [], [], 3)[0])

            original_extract = capture._extract_damage
            injected = False

            def extract_then_queue_later_damage():
                nonlocal injected
                rect = original_extract()
                if not injected:
                    injected = True
                    window.fill_rectangle(gc, 60, 30, 5, 6)
                    self.xd.sync()
                    self.assertGreater(capture.events.pending_events(), 0)
                    self.assertFalse(select.select([capture], [], [], 0)[0])
                return rect

            capture._extract_damage = extract_then_queue_later_damage
            frame, rect = capture.pump()
            x, y, width, height = rect
            self.assertLessEqual(x, 22)
            self.assertLessEqual(y, 27)
            self.assertGreaterEqual(x + width, 85)
            self.assertGreaterEqual(y + height, 60)
            at = ((24 + 32) * self.WIDTH + 20 + 62) * 3
            self.assertEqual(frame[at:at + 3], b"\xff\xff\xff")
            self.assertEqual(capture.events.pending_events(), 0)
            gc.free()
        finally:
            if window is not None:
                window.destroy()
                self.xd.sync()
            capture.close()

    def test_python_event_queue_is_reported_when_socket_is_empty(self):
        """Queued DamageNotify remains actionable after its socket is drained."""
        try:
            capture = xcapture.XDamageCapture(
                self.display_name, self.WIDTH, self.HEIGHT,
                draw_cursor=False)
        except xcapture.CaptureUnavailable as error:
            self.skipTest(str(error))
        window = None
        try:
            screen = self.xd.screen()
            window = screen.root.create_window(
                20, 24, 96, 48, 0, screen.root_depth,
                X.InputOutput, X.CopyFromParent,
                background_pixel=screen.black_pixel)
            window.map()
            self.xd.sync()
            if select.select([capture], [], [], 3)[0]:
                capture.pump()

            gc = window.create_gc(foreground=screen.white_pixel)
            window.fill_rectangle(gc, 3, 4, 7, 8)
            self.xd.sync()
            self.assertTrue(select.select([capture], [], [], 3)[0])

            # pending_events() consumes available protocol data and moves the
            # notification into python-xlib's in-process queue.
            self.assertGreater(capture.events.pending_events(), 0)
            self.assertFalse(select.select([capture], [], [], 0)[0])
            self.assertTrue(capture.has_pending_damage())

            self.assertIsNotNone(capture.pump())
            self.assertFalse(capture.has_pending_damage())
            gc.free()
        finally:
            if window is not None:
                window.destroy()
                self.xd.sync()
            capture.close()


if __name__ == "__main__":
    unittest.main()
