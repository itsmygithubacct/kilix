"""Blit state machines: band-vs-full decisions in AppPane.blit and Desk.blit.

These pin the invariants the escape builders can't see on their own:
- the 65%-of-height threshold flips band edits to full placements
- a stale base image (size mismatch after a resize) forces a full placement
- the periodic full re-place fires even while frames keep changing
- the desktops' fb/img (_blit_base) machine re-arms after img= blits and
  force_full keepalives actually transmit
"""
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
sys.path.insert(0, str(ROOT / "desktop"))


class FakeTerm:
    cols, rows = 100, 26
    cell_w, cell_h = 10.0, 20.0

    def __init__(self):
        self.writes = []

    def write(self, s):
        self.writes.append(s)

    def escapes(self):
        return "".join(self.writes)


def make_apppane(w=1000, h=500, stream=False):
    """An AppPane shell with just the attributes blit() touches — no X, no
    processes, no tty."""
    import apprun
    p = object.__new__(apprun.AppPane)
    p.term = FakeTerm()
    p.app_w, p.app_h = w, h
    p.stream = stream
    p.img_id = 7
    p.wid = "42"
    p.seq = 0
    p._band_seq = 0
    p._base_wh = None
    p._place_t = 0.0
    p._loop_start = time.time() - 10     # past the warmup window
    p.frames = 0
    p.debug = False
    p._dbg = {"t0": time.time(), "cap": 0, "blit": 0, "bytes": 0,
              "cfps": 0.0, "fps": 0.0, "kbps": 0.0}
    p.img_cols, p.img_rows = 100, 25
    p.off_col = p.off_row = 0
    return p


class AppPaneBlitStateTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("TMUX", None)

    def _frame(self, p, fill=0):
        return bytes([fill]) * (p.app_w * p.app_h * 3)

    def test_first_blit_is_full_then_bands(self):
        p = make_apppane(stream=True)
        f0 = self._frame(p, 0)
        p.blit(f0)                                   # no band: full
        self.assertIn("a=T", p.term.escapes())
        self.assertEqual(p._base_wh, (p.app_w, p.app_h))
        p.term.writes.clear()
        p.blit(self._frame(p, 1), band=(10, 4))     # small band: a=f edit
        esc = p.term.escapes()
        self.assertIn("a=f", esc)
        self.assertNotIn("a=T", esc)

    def test_band_above_65pct_goes_full(self):
        p = make_apppane(stream=True)
        p.blit(self._frame(p, 0))
        p.term.writes.clear()
        big = int(p.app_h * 0.65) + 1
        p.blit(self._frame(p, 2), band=(0, big))
        self.assertIn("a=T", p.term.escapes())

    def test_stale_base_size_forces_full(self):
        p = make_apppane(stream=True)
        p.blit(self._frame(p, 0))
        p.app_w, p.app_h = 800, 400                  # simulated resize
        p.term.writes.clear()
        p.blit(self._frame(p, 1), band=(5, 2))      # band vs stale base
        esc = p.term.escapes()
        self.assertIn("a=T", esc)
        self.assertNotIn("a=f", esc)
        self.assertEqual(p._base_wh, (800, 400))

    def test_periodic_full_replace_fires_while_animating(self):
        p = make_apppane(stream=True)
        p.blit(self._frame(p, 0))
        p._place_t = time.time() - 6                 # older than the 5s cadence
        p.term.writes.clear()
        p.blit(self._frame(p, 1), band=(5, 2))
        self.assertIn("a=T", p.term.escapes())       # full re-place, not a band

    def test_warmup_window_forces_full(self):
        p = make_apppane(stream=True)
        p.blit(self._frame(p, 0))
        p._loop_start = time.time()                  # inside the 4s warmup
        p._place_t = time.time()
        p.term.writes.clear()
        p.blit(self._frame(p, 1), band=(5, 2))
        self.assertIn("a=T", p.term.escapes())

    def test_local_band_files_are_unique(self):
        p = make_apppane(stream=False)
        p.blit(self._frame(p, 0))
        made = []
        for i in (1, 2):
            p.blit(self._frame(p, i), band=(3, 2))
        for w in p.term.writes:
            if "a=f" in w:
                made.append(w)
        self.assertEqual(len(made), 2)
        self.assertNotEqual(made[0], made[1])        # distinct shm paths
        # cleanup the files the fake blits created
        import glob
        for f in glob.glob("/dev/shm/tty-graphics-protocol-kilix-run-42-*.rgb"):
            os.unlink(f)


class DeskBlitStateTests(unittest.TestCase):
    """The builtin desktop's Desk.blit fb/img machine, on a Desk constructed
    in offscreen (screenshot) mode and given a fake term afterwards."""

    def setUp(self):
        os.environ.pop("TMUX", None)
        os.environ["KILIX_STREAM"] = "1"             # keep blits off /dev/shm
        import main as desk_main
        self.desk_main = desk_main
        self.desk = desk_main.Desk(term=None, size=(320, 200))
        self.desk.term = FakeTerm()
        self.desk.stream = True
        self.desk.img_id = 3

    def tearDown(self):
        os.environ.pop("KILIX_STREAM", None)

    def test_fb_full_then_band_then_img_rearms(self):
        d = self.desk
        d.blit()                                     # first fb blit: full
        self.assertIn("a=T", d.term.escapes())
        d.term.writes.clear()
        # dirty one strip of the fb -> band edit
        from PIL import ImageDraw
        ImageDraw.Draw(d.fb).rectangle([0, 50, 319, 60], fill=(255, 0, 0))
        d.blit()
        self.assertIn("a=f", d.term.escapes())
        self.assertNotIn("a=T", d.term.escapes())
        # a system-screen blit (img=) puts a foreign image on screen …
        d.term.writes.clear()
        from PIL import Image
        d.blit(img=Image.new("RGB", (320, 200), (9, 9, 9)))
        self.assertIn("a=T", d.term.escapes())
        # … so the NEXT fb blit must re-place in full even with a tiny change
        d.term.writes.clear()
        ImageDraw.Draw(d.fb).rectangle([0, 80, 319, 82], fill=(0, 255, 0))
        d.blit()
        esc = d.term.escapes()
        self.assertIn("a=T", esc)
        self.assertNotIn("a=f", esc)

    def test_unchanged_fb_sends_nothing_but_keepalive_heals(self):
        d = self.desk
        d.blit()
        d.term.writes.clear()
        d.blit()                                     # unchanged fb: no bytes
        self.assertEqual(d.term.escapes(), "")
        d.blit(force_full=True)                      # keepalive: full re-place
        self.assertIn("a=T", d.term.escapes())

    def test_last_blit_stamped_only_on_transmit(self):
        d = self.desk
        d.blit()
        t0 = d._last_blit
        time.sleep(0.02)
        d.blit()                                     # no-op: stamp unchanged
        self.assertEqual(d._last_blit, t0)
        d.blit(force_full=True)
        self.assertGreater(d._last_blit, t0)


if __name__ == "__main__":
    unittest.main()
