"""Live XDamage/MIT-SHM capture against an isolated Xvfb."""

import os
import select
import shutil
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

try:
    from Xlib import X, display as xdisplay, error as xerror
    import xcapture
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

XVFB = shutil.which("Xvfb")


@unittest.skipUnless(XVFB and HAVE_DEPS, "needs Xvfb + PIL + python-xlib")
class XDamageCaptureE2E(unittest.TestCase):
    WIDTH, HEIGHT = 320, 200

    @staticmethod
    def _bounds(rects):
        x0 = min(rect[0] for rect in rects)
        y0 = min(rect[1] for rect in rects)
        x1 = max(rect[0] + rect[2] for rect in rects)
        y1 = max(rect[1] + rect[3] for rect in rects)
        return x0, y0, x1 - x0, y1 - y0

    @classmethod
    def setUpClass(cls):
        cls.xd = None
        cls.xvfb = None
        failures = []
        for _attempt in range(3):
            try:
                probe = cls._start_xvfb()
                try:
                    # Keep the readiness connection open until python-xlib
                    # completes its handshake. Otherwise an idle Xvfb may
                    # reset between the probe and the real client.
                    cls.xd = xdisplay.Display(cls.display_name)
                finally:
                    probe.close()
                cls.addClassCleanup(cls._stop_xvfb)
                return
            except (OSError, RuntimeError, ValueError,
                    xerror.ConnectionClosedError,
                    xerror.DisplayConnectionError) as error:
                diagnostics = cls._stop_xvfb()
                failures.append(
                    f"{error}"
                    + (f" (Xvfb: {diagnostics})" if diagnostics else "")
                )
                time.sleep(0.05)
        raise RuntimeError(
            "Xvfb failed to start after three attempts: "
            + "; ".join(failures)
        )

    @classmethod
    def _start_xvfb(cls):
        rfd, wfd = os.pipe()
        try:
            cls.xvfb = subprocess.Popen(
                [XVFB, "-displayfd", str(wfd), "-screen", "0",
                 f"{cls.WIDTH}x{cls.HEIGHT}x24", "-nolisten", "tcp",
                 "-noreset"],
                pass_fds=(wfd,), stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE)
        except BaseException:
            os.close(rfd)
            raise
        finally:
            os.close(wfd)
        try:
            ready, _, _ = select.select([rfd], [], [], 15)
            if not ready:
                raise RuntimeError("Xvfb did not report a display number")
            number = os.read(rfd, 32).strip()
        finally:
            os.close(rfd)
        if cls.xvfb.poll() is not None or not number:
            raise RuntimeError("Xvfb exited during startup")
        cls.display_name = f":{int(number)}"

        # Xvfb's displayfd readiness can race with a preceding Xvfb teardown
        # under a loaded test runner. Probe the exact Unix listener with
        # short-lived sockets before handing it to python-xlib; failed
        # Display() constructors otherwise leak their partially opened socket.
        socket_path = f"/tmp/.X11-unix/X{int(number)}"
        deadline = time.monotonic() + 3.0
        while True:
            if cls.xvfb.poll() is not None:
                raise RuntimeError("Xvfb exited before accepting connections")
            probe = socket.socket(socket.AF_UNIX)
            try:
                probe.connect(socket_path)
                return probe
            except OSError as error:
                probe.close()
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Xvfb did not accept connections within 3 seconds"
                    ) from error
            time.sleep(0.02)

    @classmethod
    def _stop_xvfb(cls):
        xd = getattr(cls, "xd", None)
        if xd is not None:
            try:
                xd.close()
            except Exception:
                pass
            cls.xd = None
        process = getattr(cls, "xvfb", None)
        diagnostics = ""
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if process.stderr is not None:
                diagnostics = process.stderr.read().decode(
                    errors="replace"
                ).strip()
                process.stderr.close()
            cls.xvfb = None
        return diagnostics

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
            frame, rects = update
            x, y, width, height = self._bounds(rects)
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
            frame, rects = update
            self.assertTrue(rects)
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
            rects = capture._take_damage()
            self.assertGreaterEqual(len(rects), 2)
            x, y, width, height = self._bounds(rects)
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
            frame, rects = capture.pump()
            x, y, width, height = self._bounds(rects)
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


@unittest.skipUnless(HAVE_DEPS, "needs PIL + python-xlib")
class CursorDamageTests(unittest.TestCase):
    @staticmethod
    def _cursor(x, y, serial):
        class Cursor:
            width = height = 1
            xhot = yhot = 0
            cursor_image = (0xFFFF0000,)

        cursor = Cursor()
        cursor.x, cursor.y, cursor.cursor_serial = x, y, serial
        return cursor

    def _capture(self, responses):
        capture = object.__new__(xcapture.XDamageCapture)
        capture.width = capture.height = 10
        capture.frame = bytearray(10 * 10 * 3)
        capture._cursor_supported = True
        capture._cursor_signature = None
        capture._cursor_rect = None
        capture.root = object()

        class Events:
            def xfixes_get_cursor_image(self, _root):
                response = responses.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response

        capture.events = Events()
        return capture

    def test_cursor_motion_damages_old_and_new_locations(self):
        capture = self._capture([
            self._cursor(2, 3, 1),
            self._cursor(7, 8, 1),
            self._cursor(7, 8, 1),
        ])
        frame, damage = capture._with_cursor_damage()
        self.assertEqual(damage, ((2, 3, 1, 1),))
        at = (3 * 10 + 2) * 3
        self.assertEqual(frame[at:at + 3], b"\xff\0\0")

        _, damage = capture._with_cursor_damage()
        self.assertEqual(damage, ((2, 3, 1, 1), (7, 8, 1, 1)))
        _, damage = capture._with_cursor_damage()
        self.assertEqual(damage, ())

    def test_cursor_query_failure_requests_cpu_safe_diff(self):
        capture = self._capture([RuntimeError("injected")])
        frame, damage = capture._with_cursor_damage()
        self.assertEqual(frame, bytes(capture.frame))
        self.assertIsNone(damage)

    def test_cursor_composite_buffer_is_reused_and_old_patch_is_restored(self):
        capture = self._capture([
            self._cursor(2, 3, 1),
            self._cursor(7, 8, 1),
        ])
        first, _ = capture._with_cursor_damage()
        backing = capture._cursor_frame
        second, _ = capture._with_cursor_damage()
        self.assertIs(capture._cursor_frame, backing)
        old = (3 * 10 + 2) * 3
        new = (8 * 10 + 7) * 3
        self.assertEqual(second[old:old + 3], b"\0\0\0")
        self.assertEqual(second[new:new + 3], b"\xff\0\0")
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
