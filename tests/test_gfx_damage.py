"""Damage (tiled/partial) update helpers in config/gfx.py.

diff_band must find the exact changed row band between frames (it gates every
partial blit), and the a=f frame-edit builders must emit protocol-correct
escapes: r=1 root-frame edits, chunked t=d with a=f repeated on every chunk,
and the t=t file variant used by the fast local path.
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


class FrameEditEscapeTests(unittest.TestCase):
    def test_direct_edit_header_keys(self):
        esc = gfx.build_frame_edit(b"\x01\x02\x03" * 40, 40, 1, 0, 12, 7)
        head = esc.split(";", 1)[0]
        for key in ("a=f", "i=7", "r=1", "x=0", "y=12",
                    "t=d", "f=24", "o=z", "s=40", "v=1", "q=2"):
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
            if i > 0:
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
                    "t=t", "f=24", "s=80", "v=4", "q=2"):
            self.assertIn(key, head)
        payload = esc.split(";", 1)[1].split("\x1b", 1)[0]
        self.assertEqual(base64.b64decode(payload).decode(), path)

    def test_empty_rgb_builds_nothing(self):
        self.assertEqual(gfx.build_frame_edit(b"", 1, 1, 0, 0, 1), "")


class SdkReexportTests(unittest.TestCase):
    def test_sdk_exposes_damage_helpers(self):
        from kilix_sdk import graphics
        for name in ("diff_band", "build_frame_edit", "blit_frame_edit",
                     "build_frame_edit_file"):
            self.assertTrue(callable(getattr(graphics, name)), name)


if __name__ == "__main__":
    unittest.main()
