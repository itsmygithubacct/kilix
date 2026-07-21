"""Damage and graphics-protocol compatibility helpers in config/gfx.py.

The shared presenter finds exact rectangles, uses bounded POSIX shared memory
locally, and emits in-place root-frame edits. Legacy row-band and temporary-file
builders remain covered because they are part of the versioned Kilix SDK.
"""
import base64
import re
import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import gfx


def frame(w, h, fill=0):
    return bytes([fill]) * (w * h * 3)


def poke(buf, w, y, x=0, val=255):
    b = bytearray(buf)
    b[(y * w + x) * 3] = val
    return bytes(b)


class DiffBandTests(unittest.TestCase):
    W, H = 100, 90        # spans multiple 32-row chunks + a partial tail chunk

    def test_identical_frames_return_none(self):
        a = frame(self.W, self.H)
        self.assertIsNone(gfx.diff_band(a, bytes(a), self.W, self.H))
        self.assertIsNone(gfx.diff_band(a, a, self.W, self.H))

    def test_single_row_bands(self):
        a = frame(self.W, self.H)
        for y in (0, 1, 31, 32, 33, 63, 64, self.H - 1):
            with self.subTest(row=y):
                b = poke(a, self.W, y, x=self.W - 1)
                self.assertEqual(gfx.diff_band(a, b, self.W, self.H), (y, 1))

    def test_multi_row_band_spans_first_to_last_change(self):
        a = frame(self.W, self.H)
        b = poke(poke(a, self.W, 10), self.W, 70)
        self.assertEqual(gfx.diff_band(a, b, self.W, self.H), (10, 61))

    def test_adjacent_rows(self):
        a = frame(self.W, self.H)
        b = poke(poke(a, self.W, 40), self.W, 41)
        self.assertEqual(gfx.diff_band(a, b, self.W, self.H), (40, 2))

    def test_full_change(self):
        a = frame(self.W, self.H, 0)
        b = frame(self.W, self.H, 255)
        self.assertEqual(gfx.diff_band(a, b, self.W, self.H), (0, self.H))

    def test_size_mismatch_is_fully_dirty(self):
        a = frame(self.W, self.H)
        b = frame(self.W, self.H + 1)
        self.assertEqual(gfx.diff_band(a, b, self.W, self.H), (0, self.H))

    def test_band_slice_reconstructs_frame(self):
        # the band must cover every changed byte: patching prev with the band
        # slice must reproduce cur exactly
        a = frame(self.W, self.H)
        b = poke(poke(a, self.W, 17, x=3), self.W, 55, x=90)
        y0, bh = gfx.diff_band(a, b, self.W, self.H)
        stride = self.W * 3
        patched = bytearray(a)
        patched[y0 * stride:(y0 + bh) * stride] = \
            b[y0 * stride:(y0 + bh) * stride]
        self.assertEqual(bytes(patched), b)


class ExactRectTests(unittest.TestCase):
    def test_exact_columns_and_rows(self):
        width, height = 20, 12
        before = frame(width, height)
        after = bytearray(before)
        for y in range(4, 7):
            for x in range(8, 13):
                at = (y * width + x) * 3
                after[at:at + 3] = b"\x10\x20\x30"
        rect = gfx.diff_rect(before, after, width, height)
        self.assertEqual(rect, (8, 4, 5, 3))
        self.assertEqual(
            gfx.extract_rect(after, width, height, rect),
            b"\x10\x20\x30" * 15)

    def test_disjoint_row_regions_remain_bounded(self):
        width, height = 20, 12
        before = frame(width, height)
        after = poke(poke(before, width, 1, x=2), width, 10, x=17)
        self.assertEqual(gfx.diff_rects(before, after, width, height),
                         ((2, 1, 1, 1), (17, 10, 1, 1)))


class FullFrameEscapeTests(unittest.TestCase):
    def test_direct_full_frame_is_transient_on_start_chunk_only(self):
        # In a chunked direct upload the first command is retained as kitty's
        # start command; continuation chunks carry payload/m= only.  The
        # transient hint therefore belongs on the first command, not every
        # continuation.
        import random
        rgb = random.Random(1).randbytes(200 * 100 * 3)
        esc = gfx.build_direct(rgb, 200, 100, 80, 25, 11)
        apcs = esc.split("\x1b\\")[:-1]
        self.assertGreater(len(apcs), 1)
        for i, apc in enumerate(apcs):
            head = apc.split("\x1b_G", 1)[1].split(";", 1)[0]
            if i == 0:
                self.assertIn("a=T", head)
                self.assertIn("N=1", head)
            else:
                self.assertNotIn("N=1", head)


class FrameEditEscapeTests(unittest.TestCase):
    def test_direct_edit_header_keys(self):
        esc = gfx.build_frame_edit(b"\x01\x02\x03" * 40, 40, 1, 0, 12, 7)
        head = esc.split(";", 1)[0]
        for key in ("a=f", "i=7", "r=1", "x=0", "y=12",
                    "t=d", "f=24", "o=z", "N=1", "s=40", "v=1", "q=2"):
            self.assertIn(key, head)
        # no placement/cursor keys: frame edits repaint in place
        self.assertNotIn("p=", head)
        self.assertNotIn("C=", head)
        self.assertNotIn("a=T", esc)

    def test_direct_edit_payload_roundtrip(self):
        rgb = bytes(range(256)) * 3          # 768 bytes = 16x16 rgb
        esc = gfx.build_frame_edit(rgb, 16, 16, 4, 8, 3)
        payload = "".join(
            m.group(1) for m in re.finditer(r";([A-Za-z0-9+/=]*)\x1b", esc))
        self.assertEqual(zlib.decompress(base64.b64decode(payload)), rgb)

    def test_direct_edit_chunks_repeat_routing_keys(self):
        # >4096 b64 bytes forces chunking; every chunk must carry a=f AND
        # i=/r=1 — kitty computes the target frame from each chunk's own keys
        # before restoring the saved start command, so a continuation without
        # r=1 silently appends a stray animation frame instead of editing the
        # displayed root frame. Only the first chunk carries the geometry.
        # Random bytes defeat zlib so the payload really spans chunks, and the
        # reassembled multi-chunk payload must round-trip byte-exactly.
        import random
        rgb = random.Random(0).randbytes(200 * 100 * 3)
        esc = gfx.build_frame_edit(rgb, 200, 100, 0, 0, 9)
        apcs = esc.split("\x1b\\")[:-1]
        self.assertGreater(len(apcs), 1)
        for i, apc in enumerate(apcs):
            head = apc.split(";", 1)[0]
            self.assertIn("a=f", head)
            self.assertIn("i=9", head)
            self.assertIn("r=1", head)
            self.assertIn(f"m={1 if i < len(apcs) - 1 else 0}", head)
            if i == 0:
                self.assertIn("N=1", head)
            else:
                # The saved start command supplies N=1 after kitty routes this
                # chunk via the repeated a=f/i=/r=1 keys.
                self.assertNotIn("N=1", head)
                self.assertNotIn("s=", head)
                self.assertNotIn("x=", head)
        joined = "".join(
            m.group(1) for m in re.finditer(r";([A-Za-z0-9+/=]*)\x1b", esc))
        self.assertEqual(zlib.decompress(base64.b64decode(joined)), rgb)

    def test_direct_edit_tmux_wrap(self):
        esc = gfx.build_frame_edit(b"\x00" * 30, 10, 1, 0, 0, 1, in_tmux=True)
        self.assertTrue(esc.startswith("\x1bPtmux;"))
        self.assertIn("\x1b\x1b_G", esc)

    def test_file_edit_escape(self):
        path = "/dev/shm/tty-graphics-protocol-test-0.rgb"
        esc = gfx.build_frame_edit_file(path, 80, 4, 0, 33, 5)
        head = esc.split(";", 1)[0]
        for key in ("a=f", "i=5", "r=1", "x=0", "y=33",
                    "t=t", "f=24", "N=1", "s=80", "v=4", "q=2"):
            self.assertIn(key, head)
        payload = esc.split(";", 1)[1].split("\x1b", 1)[0]
        self.assertEqual(base64.b64decode(payload).decode(), path)

    def test_shared_memory_full_and_edit_headers(self):
        full = gfx.build_full_shm("/kilix-test-full", 80, 40, 10, 5, 7)
        self.assertIn("a=T", full)
        self.assertIn("t=s", full)
        self.assertNotIn("t=t", full)
        payload = full.rsplit(";", 1)[1].split("\x1b", 1)[0]
        self.assertEqual(base64.b64decode(payload).decode(),
                         "/kilix-test-full")

        edit = gfx.build_frame_edit_shm(
            "/kilix-test-edit", 6, 4, 11, 13, 7)
        for key in ("a=f", "r=1", "x=11", "y=13", "s=6", "v=4", "t=s"):
            self.assertIn(key, edit)

    def test_scroll_compose_requires_explicit_fork_hint(self):
        value = gfx.build_compose(7, (0, 5, 80, 35), (0, 0, 80, 35))
        self.assertIn("a=c", value)
        self.assertIn("C=1", value)
        self.assertIn("N=2", value)

    def test_empty_rgb_builds_nothing(self):
        self.assertEqual(gfx.build_frame_edit(b"", 1, 1, 0, 0, 1), "")


class SdkReexportTests(unittest.TestCase):
    def test_sdk_exposes_damage_helpers(self):
        from kilix_sdk import graphics
        for name in ("FramePresenter", "PosixShmRing", "diff_band",
                     "diff_rect", "build_frame_edit", "blit_frame_edit",
                     "build_frame_edit_shm", "build_frame_edit_file"):
            self.assertTrue(callable(getattr(graphics, name)), name)


if __name__ == "__main__":
    unittest.main()
