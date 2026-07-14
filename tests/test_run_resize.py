"""Live end-to-end test of the `kilix run` display-resize machinery.

Runs the real choreography apprun.do_resize performs against a real Xvfb:
disable the CRTC (randr_prepare), RRSetScreenSize to several pane-like sizes
(randr_set_screen_size), refit a live client window, and capture exact-size
frames with ffmpeg x11grab after each resize. Skipped when Xvfb, ffmpeg, or
python-xlib are unavailable (the unpacked Kilix-private dependency copy
counts, same as apprun's find_xvfb).

The display number is chosen by Xvfb itself (-displayfd), so the test never
collides with kilix's own supervisor range (60-119) or a stale server, and
the spawned Xvfb is guaranteed to be the server the test talks to.
"""
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))


def _find_xvfb():
    p = shutil.which("Xvfb")
    if p:
        return p
    data = os.environ.get("KILIX_DATA_HOME") or os.path.join(
        os.environ.get("KILIX_STORAGE_HOME", os.path.expanduser(
            "~/.local/gpu_terminal/kilix")), "data")
    p = os.path.join(data, "deps", "usr", "bin", "Xvfb")
    return p if os.access(p, os.X_OK) else None


XVFB = _find_xvfb()
HAVE_FFMPEG = shutil.which("ffmpeg") is not None
try:
    from Xlib import X, display as xdisplay  # noqa: F401
    HAVE_XLIB = True
except ImportError:
    HAVE_XLIB = False

SIZES = [(1000, 640), (1440, 900), (820, 520)]     # down, up, arbitrary


@unittest.skipUnless(XVFB and HAVE_FFMPEG and HAVE_XLIB,
                     "needs Xvfb + ffmpeg + python-xlib")
class RunResizeE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # -displayfd: Xvfb picks a free display itself and writes the number
        # to the fd — no fixed number, no race with other X servers.
        rfd, wfd = os.pipe()
        cls.xvfb = subprocess.Popen(
            [XVFB, "-displayfd", str(wfd), "-screen", "0", "3840x2160x24",
             "-nolisten", "tcp"],
            pass_fds=(wfd,),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls._stop_xvfb)      # runs even if setup fails below
        os.close(wfd)
        num = b""
        deadline = time.time() + 15
        while not num.endswith(b"\n"):
            if cls.xvfb.poll() is not None:
                os.close(rfd)
                raise RuntimeError("Xvfb exited during startup")
            if time.time() > deadline:
                os.close(rfd)
                raise RuntimeError("Xvfb did not report a display number")
            chunk = os.read(rfd, 16)
            if not chunk:
                break
            num += chunk
        os.close(rfd)
        cls.disp_n = int(num.strip())
        cls.disp = f":{cls.disp_n}"
        cls.xd = xdisplay.Display(cls.disp)

    @classmethod
    def _stop_xvfb(cls):
        xd = getattr(cls, "xd", None)
        if xd is not None:
            try:
                xd.close()
            except Exception:
                pass
        cls.xvfb.terminate()
        try:
            cls.xvfb.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.xvfb.kill()
            cls.xvfb.wait()

    def test_resize_choreography(self):
        import apprun

        self.assertTrue(apprun.randr_prepare(self.xd),
                        "CRTC disable failed — Xvfb too old for RandR?")
        root = self.xd.screen().root

        # a live client window standing in for the app
        scr = self.xd.screen()
        win = root.create_window(0, 0, 800, 600, 0, scr.root_depth,
                                 X.InputOutput, X.CopyFromParent,
                                 background_pixel=scr.white_pixel)
        win.map()
        self.xd.sync()

        for w, h in SIZES:
            with self.subTest(size=f"{w}x{h}"):
                # the do_resize order: resize screen -> refit window -> capture
                self.assertTrue(apprun.randr_set_screen_size(self.xd, w, h))
                g = root.get_geometry()
                self.assertEqual((g.width, g.height), (w, h))
                win.configure(x=0, y=0, width=w, height=h)
                self.xd.sync()
                p = subprocess.run(
                    ["ffmpeg", "-loglevel", "error", "-f", "x11grab",
                     "-framerate", "10", "-video_size", f"{w}x{h}",
                     "-i", self.disp, "-frames:v", "2",
                     "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                    capture_output=True, timeout=30)
                self.assertEqual(len(p.stdout), 2 * w * h * 3,
                                 p.stderr.decode(errors="replace")[-300:])
                gw = win.get_geometry()
                self.assertEqual((gw.width, gw.height), (w, h))


if __name__ == "__main__":
    unittest.main()
