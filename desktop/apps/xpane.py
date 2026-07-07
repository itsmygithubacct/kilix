"""kilix desktop — XPane: an X11 application inside a kilix 95 window.

The apprun recipe, embedded: the app runs on a private Xvfb sized to the
window's client area, an ffmpeg rawvideo capture feeds frames into the
window surface through the Desk's fd hooks, and mouse/keys are injected
with XTest into the private display only. Processes are owned by a
StreamSupervisor, so closing the window (or the desktop) tears everything
down. Also here: InstallerWindow, a small log-tailing window that runs
`games.py <target> --setup-only` and fires a callback on success.
"""
import os
import subprocess
import tempfile
import time

from PIL import Image, ImageChops

import theme as T
import widgets as W
import wm

import stream                     # from config/ (main.py puts it on the path)
import xinject
from Xlib import display as xdisplay, X

_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The private X server's root is painted this chroma and keyed out when the
# frame is composited, so only the app's own (rectangular, opaque) windows —
# the skin — show on the desktop. Classic skins never use pure magenta.
CHROMA = (255, 0, 255)


class _XSurface(W.Widget):
    """The client-area widget: shows the captured frames, forwards input."""
    focusable = True

    def __init__(self, pane, w, h):
        super().__init__(0, 0, w, h)
        self.pane = pane

    def draw(self, d, img):
        if self.pane.frame_img is not None:
            img.paste(self.pane.frame_img, (self.x, self.y))
        else:
            img.paste(CHROMA, (self.x, self.y,
                               self.x + self.w, self.y + self.h))

    def on_mouse(self, ev):
        self.pane.inject_mouse(ev)
        return True

    def on_key(self, ev):
        self.pane.inject_key(ev)
        return True


class XPane(wm.Window):
    """An X11 app shown directly on the desktop with no kilix window chrome:
    the app runs on a private Xvfb whose root is chroma-keyed away, so its own
    skin (title bar, buttons, dragging) is all the UI — Winamp-on-Win95 style.
    Clicks on the keyed-out (transparent) gaps fall through to the desktop."""
    _seq2 = 0

    def __init__(self, desk, cmd, title, icon="exe", app_size=None,
                 fps=15, env=None, cwd=None, fill=False):
        sw, sh = desk.size()
        aw, ah = app_size or (sw, sh - T.TASKBAR_H)
        super().__init__(desk, title, aw, ah, x=0, y=0, icon=icon,
                         chromeless=True)
        XPane._seq2 += 1
        self.app_w, self.app_h = aw, ah
        self.frame_img = None
        self._last_frame = None
        # fully transparent until the first real frame, so the magenta chroma
        # fill never flashes onto the desktop during startup
        self.compose_mask = Image.new("L", (aw, ah), 0)
        self.fsize = aw * ah * 3
        self.buf = bytearray()
        self._dead = False
        self.sup = stream.StreamSupervisor(
            f"desk-xpane-{os.getpid()}-{XPane._seq2}")
        # any failure past here leaks Xvfb/app/display-lock unless we clean up
        try:
            n = self.sup.pick_display()
            # the desktop paints its own pointer, so hide Xvfb's software cursor
            self.sup.start_xvfb(n, aw, ah, nocursor=True)
            e = dict(os.environ, DISPLAY=f":{n}",
                     XAUTHORITY=self.sup.xauth, **(env or {}))
            # python-xlib reads XAUTHORITY at connect time; each pane connects
            # once, right here, so the env swap is safe for other panes
            os.environ["XAUTHORITY"] = self.sup.xauth
            self.xd = xdisplay.Display(f":{n}")
            self._paint_root_chroma()
            self.app = self.sup.spawn("app", cmd, env=e, cwd=cwd,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
            self.inj = xinject.Injector(self.xd, aw, ah)
            self.ff = self.sup.spawn(
                "cap", ["ffmpeg", "-loglevel", "quiet",
                        "-f", "x11grab", "-draw_mouse", "0",  # desktop draws
                        "-framerate", str(fps),               # only pointer
                        "-video_size", f"{aw}x{ah}", "-i", f":{n}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            os.set_blocking(self.ff.stdout.fileno(), False)
        except Exception:
            self.sup.cleanup()
            raise
        self.add(_XSurface(self, aw, ah))
        self.set_focus(self.widgets[-1])
        self._born = time.time()
        # fill: a general app opened "in a window" should maximize; with no WM
        # on the Xvfb we resize its main window ourselves until it settles
        self.fill = fill
        self._fill_deadline = self._born + 15 if fill else 0.0
        self._fill_at = 0.0
        desk.add_fd(self.ff.stdout.fileno(), self._pump)
        desk.tick_hooks.append(self._tick)

    def hit_test(self, gx, gy):
        # only the opaque skin pixels belong to us; clicks on the keyed-out
        # gaps fall through to the desktop (icons) or windows behind
        if not self.hit(gx, gy):
            return False
        if self.compose_mask is None:
            return False
        try:
            return self.compose_mask.getpixel((gx - self.x, gy - self.y)) != 0
        except Exception:
            return False

    def _paint_root_chroma(self):
        try:
            scr = self.xd.screen()
            pix = scr.default_colormap.alloc_color(
                CHROMA[0] * 257, CHROMA[1] * 257, CHROMA[2] * 257).pixel
            scr.root.change_attributes(background_pixel=pix)
            scr.root.clear_area(x=0, y=0, width=self.app_w, height=self.app_h)
            self.xd.sync()
        except Exception:
            pass

    # ── frames in ───────────────────────────────────────────────────────────
    def _pump(self):
        try:
            while True:
                chunk = os.read(self.ff.stdout.fileno(), 1 << 20)
                if not chunk:                     # ffmpeg gone: a pipe at EOF
                    self.desk.remove_fd(          # is permanently readable, so
                        self.ff.stdout.fileno())  # leaving the hook spins select
                    return
                self.buf += chunk
        except BlockingIOError:
            pass
        frame = None
        while len(self.buf) >= self.fsize:        # newest frame wins
            frame = bytes(self.buf[:self.fsize])
            del self.buf[:self.fsize]
        if frame is not None and frame != self._last_frame:
            self._last_frame = frame
            self.frame_img = Image.frombytes(
                "RGB", (self.app_w, self.app_h), frame)
            # color-key: opaque everywhere the pixel differs from the chroma.
            # difference→L is zero only for an exact chroma match (all luma
            # coefficients are positive), so this is an exact key.
            diff = ImageChops.difference(
                self.frame_img, Image.new("RGB", self.frame_img.size, CHROMA))
            self.compose_mask = diff.convert("L").point(
                lambda v: 0 if v == 0 else 255)
            self.invalidate()

    def _tick(self, now):
        if self._dead:
            return
        if self.app.poll() is not None or self.ff.poll() is not None:
            self.close()                          # app or capture gone: close
            return
        if self.fill and now < self._fill_deadline and now >= self._fill_at:
            self._fill_at = now + 0.5             # re-fill twice a second while
            self._fill_app_window()               # the app maps its main window
        self._keep_on_screen()

    def _fill_app_window(self):
        """No WM on the Xvfb: size the app's largest window to fill the pane, so
        a general app opened 'in a window' maximizes instead of floating small."""
        try:
            best = None
            for c in self.xd.screen().root.query_tree().children:
                if c.get_attributes().map_state != X.IsViewable:
                    continue
                g = c.get_geometry()
                if g.width <= 8 or g.height <= 8:  # skip tiny helper windows
                    continue
                area = g.width * g.height
                if best is None or area > best[1]:
                    best = (c, area, g)
            if best:
                c, _, g = best
                if (g.x, g.y, g.width, g.height) != (0, 0, self.app_w,
                                                     self.app_h):
                    c.configure(x=0, y=0, width=self.app_w, height=self.app_h)
                    self.xd.set_input_focus(c, X.RevertToPointerRoot,
                                            X.CurrentTime)
                    self.xd.sync()
        except Exception:
            pass

    def _keep_on_screen(self):
        """Keep every one of the app's windows fully within the visible
        region. kilix-amp is a multi-window docking app built for a real
        screen — its playlist/EQ windows (mapped only when toggled on, after
        startup) can dock or restore to positions off our region. Pull any
        out-of-bounds window just inside; windows already in view are left
        alone, so this never fights a drag happening on-screen."""
        # the visible region can shrink under us on a terminal resize; clamp
        # against the on-screen intersection, not the (stale) capture size, or
        # windows parked past the new edge become mouse-unreachable
        vw = min(self.app_w, self.desk.w)
        vh = min(self.app_h, self.desk.h - T.TASKBAR_H)
        try:
            for c in self.xd.screen().root.query_tree().children:
                if c.get_attributes().map_state != X.IsViewable:
                    continue
                g = c.get_geometry()
                if g.width <= 8 or g.height <= 8:
                    continue
                nx = min(max(0, g.x), max(0, vw - g.width))
                ny = min(max(0, g.y), max(0, vh - g.height))
                if (nx, ny) != (g.x, g.y):
                    c.configure(x=nx, y=ny)
            self.xd.sync()
        except Exception:
            pass

    # ── input out ───────────────────────────────────────────────────────────
    def inject_mouse(self, ev):
        try:
            if ev.wheel:
                self.inj.move_click(ev.x, ev.y,
                                    button=4 if ev.wheel < 0 else 5)
            elif ev.move:
                self.inj.move_click(ev.x, ev.y)
            else:
                self.inj.move_click(ev.x, ev.y, button=ev.btn,
                                    press=ev.press)
        except Exception:
            pass

    def inject_key(self, ev):
        # desk key events are press-only; tap the key, but hold any modifiers
        # around it so shortcuts reach the app (kilix-amp toggles EQ/playlist/
        # editor with Alt+G/E/D; games use Ctrl/Shift combos)
        try:
            mods = []
            if getattr(ev, "ctrl", False):
                mods.append("Control_L")
            if getattr(ev, "alt", False):
                mods.append("Alt_L")
            if getattr(ev, "shift", False):
                mods.append("Shift_L")
            for m in mods:
                self.inj.key_named(m, 1)
            self.inj.key(ev.key, 1)
            self.inj.key(ev.key, 3)
            for m in reversed(mods):
                self.inj.key_named(m, 3)
        except Exception:
            pass

    # ── teardown ────────────────────────────────────────────────────────────
    def _teardown(self):
        if self._dead:
            return
        self._dead = True
        self.desk.remove_fd(self.ff.stdout.fileno())
        if self._tick in self.desk.tick_hooks:
            self.desk.tick_hooks.remove(self._tick)
        self.sup.cleanup()

    def close(self):
        self._teardown()
        super().close()

    def request_close(self):
        self.close()


class InstallerWindow(wm.Window):
    """Runs `games.py <target> --setup-only` and tails its log; fires
    on_ok() when the install succeeds."""

    def __init__(self, desk, target, label, on_ok=None):
        super().__init__(desk, f"Installing {label}…", 480, 300,
                         icon="exe", resizable=False)
        self.on_ok = on_ok
        cw, ch = self.client_size()
        self.ta = self.add(W.TextArea(6, 6, cw - 12, ch - 12, ""))
        self.log = tempfile.NamedTemporaryFile(
            mode="w+", prefix=f"kilix-install-{target}-", suffix=".log")
        self.proc = subprocess.Popen(
            ["python3", os.path.join(_here, "games.py"), target,
             "--setup-only"],
            stdout=self.log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL)
        self._done = False
        self._log_len = 0
        desk.tick_hooks.append(self._tick)

    def _tick(self, now):
        if self._done:
            return
        try:
            with open(self.log.name) as f:
                text = f.read()
        except OSError:
            text = ""
        if len(text) != self._log_len:
            self._log_len = len(text)
            lines = text.splitlines()[-14:]
            self.ta.set_text("\n".join(lines))
            self.invalidate()
        rc = self.proc.poll()
        if rc is None:
            return
        self._done = True
        if self._tick in self.desk.tick_hooks:
            self.desk.tick_hooks.remove(self._tick)
        if rc == 0:
            self.close()
            if self.on_ok:
                # on_ok spawns the app (e.g. XPane → Xvfb/ffmpeg); a failure
                # here runs inside a tick hook, which Desk.run does not guard,
                # so an uncaught raise would take the whole desktop down
                try:
                    self.on_ok()
                except Exception as ex:
                    wm.msgbox(self.desk, "kilix",
                              f"Could not start:\n{ex}", icon="error")
        else:
            self.title = "Install failed"
            self.invalidate()

    def request_close(self):
        if self._tick in self.desk.tick_hooks:
            self.desk.tick_hooks.remove(self._tick)
        if self.proc.poll() is None:
            self.proc.terminate()
        self.close()
