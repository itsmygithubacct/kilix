"""Integration tests for the shared presenter in each Kilix renderer."""

import base64
import os
import re
import sys
import time
import unittest
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
sys.path.insert(0, str(ROOT / "desktop"))

import gfx


class FakeTerm:
    cols, rows = 100, 26
    cell_w, cell_h = 10.0, 20.0
    SHM = re.compile(r"\x1b_G([^;]*\bt=s\b[^;]*);([A-Za-z0-9+/=]+)\x1b\\")

    def __init__(self, consume_shm=True):
        self.writes = []
        self.payloads = []
        self.shm_names = []
        self.consume_shm = consume_shm

    def write(self, value):
        self.writes.append(value)
        if not self.consume_shm:
            return
        for match in self.SHM.finditer(value):
            name = base64.b64decode(match.group(2)).decode("ascii")
            path = "/dev/shm/" + name.lstrip("/")
            with open(path, "rb") as stream:
                self.payloads.append(stream.read())
            self.shm_names.append(name)
            os.unlink(path)

    def escapes(self):
        return "".join(self.writes)


def make_apppane(width=100, height=50, stream=False, warmup=False):
    import apprun
    pane = object.__new__(apprun.AppPane)
    pane.term = FakeTerm()
    pane.app_w, pane.app_h = width, height
    pane.stream = stream
    pane.img_id = 7
    pane.wid = "42"
    pane.fps = 0
    pane.frames = 0
    pane.debug = False
    pane._dbg = {"t0": time.time(), "cap": 0, "blit": 0, "bytes": 0,
                 "cfps": 0.0, "fps": 0.0, "kbps": 0.0}
    pane.img_cols, pane.img_rows = 10, 5
    pane.off_col = pane.off_row = 0
    pane.presenter = gfx.FramePresenter(
        pane.term, pane.img_id, stream=stream,
        stream_warmup_seconds=4 if warmup else 0)
    return pane


def solid(pane, value=0):
    return bytes([value]) * (pane.app_w * pane.app_h * 3)


def poke(frame, width, x, y, value=255):
    changed = bytearray(frame)
    at = (y * width + x) * 3
    changed[at:at + 3] = bytes((value, value, value))
    return bytes(changed)


class AppPanePresenterTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("TMUX", None)

    def test_first_frame_is_full_then_exact_rect(self):
        pane = make_apppane(stream=True)
        try:
            first = solid(pane)
            pane.blit(first)
            pane.term.writes.clear()
            result = pane.blit(poke(first, pane.app_w, 17, 11))
            self.assertEqual(result.rects, ((17, 11, 1, 1),))
            self.assertIn("a=f", pane.term.escapes())
            self.assertNotIn("a=T", pane.term.escapes())
        finally:
            pane.presenter.close()

    def test_full_height_local_change_stays_in_place(self):
        pane = make_apppane(stream=False)
        try:
            pane.blit(solid(pane))
            pane.term.writes.clear()
            pane.blit(solid(pane, 2))
            self.assertIn("a=f", pane.term.escapes())
            self.assertNotIn("a=T", pane.term.escapes())
            self.assertIn("t=s", pane.term.escapes())
            self.assertNotIn("t=t", pane.term.escapes())
        finally:
            pane.presenter.close()

    def test_local_updates_do_not_periodically_replace_base(self):
        pane = make_apppane(stream=False)
        try:
            first = solid(pane)
            pane.blit(first)
            pane.presenter._last_full_at -= 60
            pane.term.writes.clear()
            pane.blit(poke(first, pane.app_w, 2, 3))
            self.assertIn("a=f", pane.term.escapes())
            self.assertNotIn("a=T", pane.term.escapes())
        finally:
            pane.presenter.close()

    def test_size_change_forces_full_placement(self):
        pane = make_apppane(stream=True)
        try:
            pane.blit(solid(pane))
            pane.app_w, pane.app_h = 80, 40
            pane.term.writes.clear()
            pane.blit(solid(pane, 1))
            self.assertIn("a=T", pane.term.escapes())
            self.assertNotIn("a=f", pane.term.escapes())
        finally:
            pane.presenter.close()

    def test_stream_warmup_and_periodic_keyframes(self):
        pane = make_apppane(stream=True, warmup=True)
        try:
            first = solid(pane)
            pane.blit(first)
            pane.term.writes.clear()
            pane.blit(poke(first, pane.app_w, 1, 1))
            self.assertIn("a=T", pane.term.escapes())
            pane.presenter.started_at -= 10
            pane.presenter._last_full_at -= 10
            pane.term.writes.clear()
            pane.blit(poke(first, pane.app_w, 2, 2))
            self.assertIn("a=T", pane.term.escapes())
        finally:
            pane.presenter.close()

    def test_shared_memory_ring_reuses_only_consumed_names(self):
        pane = make_apppane(width=4, height=4, stream=False)
        try:
            for value in range(10):
                pane.blit(solid(pane, value), force_full=True)
            self.assertEqual(len(pane.term.payloads), 10)
            self.assertLessEqual(len(set(pane.term.shm_names)), 3)
            self.assertEqual(pane.term.payloads[-1], solid(pane, 9))
        finally:
            pane.presenter.close()


class BrowserPresenterTests(unittest.TestCase):
    def test_screencast_and_pointer_repaint_share_presenter(self):
        import browse
        from PIL import Image

        browser = object.__new__(browse.Browse)
        browser.term = FakeTerm()
        browser.wid = "browser-test"
        browser.stream = False
        browser.img_id = 9
        browser.view_rows = browser.term.rows - 1
        browser.page_w = browser.page_h = 2
        browser.cursor = False
        browser.last_img = None
        browser._cur_saved = None
        browser.frames = 0
        browser.scroll_x = browser.scroll_y = 0
        browser.glyph_dirty = False
        browser.presenter = gfx.FramePresenter(browser.term, browser.img_id)
        try:
            browser.last_img = Image.new("RGB", (2, 2), (1, 1, 1))
            browser._present()
            encoded = BytesIO()
            Image.new("RGB", (2, 2), (2, 2, 2)).save(encoded, format="PNG")
            browser.blit(base64.b64encode(encoded.getvalue()).decode(), {})
            self.assertIn("a=T", browser.term.escapes())
            self.assertIn("a=f", browser.term.escapes())
            self.assertTrue(all("t=s" in write for write in browser.term.writes))
        finally:
            browser.presenter.close()


class DeskPresenterTests(unittest.TestCase):
    def setUp(self):
        os.environ["KILIX_STREAM"] = "1"
        import main as desk_main
        self.desk = desk_main.Desk(term=None, size=(320, 200))
        self.desk.term = FakeTerm()
        self.desk.stream = True
        self.desk.img_id = 3

    def tearDown(self):
        self.desk.cleanup_shm()
        os.environ.pop("KILIX_STREAM", None)

    def test_fb_rect_then_system_image_rearms_content(self):
        from PIL import Image, ImageDraw
        desk = self.desk
        desk.blit()
        desk.term.writes.clear()
        ImageDraw.Draw(desk.fb).rectangle([10, 50, 30, 60], fill=(255, 0, 0))
        result = desk.blit()
        self.assertEqual(result.rects, ((10, 50, 21, 11),))
        self.assertIn("a=f", desk.term.escapes())
        desk.term.writes.clear()
        desk.blit(img=Image.new("RGB", (320, 200), (9, 9, 9)))
        self.assertIn("a=T", desk.term.escapes())
        desk.term.writes.clear()
        desk.blit()
        self.assertIn("a=T", desk.term.escapes())

    def test_unchanged_frame_sends_nothing_but_keepalive_heals(self):
        desk = self.desk
        desk.blit()
        desk.term.writes.clear()
        desk.blit()
        self.assertEqual(desk.term.escapes(), "")
        desk.blit(force_full=True)
        self.assertIn("a=T", desk.term.escapes())

    def test_last_blit_changes_only_on_transmit(self):
        desk = self.desk
        desk.blit()
        first = desk._last_blit
        time.sleep(0.01)
        desk.blit()
        self.assertEqual(desk._last_blit, first)
        desk.blit(force_full=True)
        self.assertGreater(desk._last_blit, first)


if __name__ == "__main__":
    unittest.main()
