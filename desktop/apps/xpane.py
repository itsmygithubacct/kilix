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

from PIL import Image

import theme as T
import widgets as W
import wm

import stream                     # from config/ (main.py puts it on the path)
import xinject
from Xlib import display as xdisplay, X

_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
            d.rectangle([self.x, self.y, self.x + self.w - 1,
                         self.y + self.h - 1], fill=T.TEXT)
            d.text((self.x + 10, self.y + 10), "starting…",
                   font=T.FONT, fill=T.LIGHT)

    def on_mouse(self, ev):
        self.pane.inject_mouse(ev)
        return True

    def on_key(self, ev):
        self.pane.inject_key(ev)
        return True


class XPane(wm.Window):
    _seq2 = 0

    def __init__(self, desk, cmd, title, icon="exe", app_size=(640, 480),
                 fps=15, env=None, cwd=None):
        aw, ah = app_size
        super().__init__(desk, title, aw + 2 * T.BORDER,
                         ah + 2 * T.BORDER + T.TITLE_H, icon=icon,
                         resizable=False)
        XPane._seq2 += 1
        self.app_w, self.app_h = aw, ah
        self.frame_img = None
        self.fsize = aw * ah * 3
        self.buf = bytearray()
        self._dead = False
        self.sup = stream.StreamSupervisor(
            f"desk-xpane-{os.getpid()}-{XPane._seq2}")
        n = self.sup.pick_display()
        self.sup.start_xvfb(n, aw, ah)
        e = dict(os.environ, DISPLAY=f":{n}",
                 XAUTHORITY=self.sup.xauth, **(env or {}))
        self.app = self.sup.spawn("app", cmd, env=e, cwd=cwd,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        # python-xlib reads XAUTHORITY at connect time; each pane connects
        # once, right here, so the env swap is safe for other panes
        os.environ["XAUTHORITY"] = self.sup.xauth
        self.xd = xdisplay.Display(f":{n}")
        self.inj = xinject.Injector(self.xd, aw, ah)
        self.ff = self.sup.spawn(
            "cap", ["ffmpeg", "-loglevel", "quiet",
                    "-f", "x11grab", "-framerate", str(fps),
                    "-video_size", f"{aw}x{ah}", "-i", f":{n}",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(self.ff.stdout.fileno(), False)
        self.add(_XSurface(self, aw, ah))
        self.set_focus(self.widgets[-1])
        self._born = time.time()
        desk.add_fd(self.ff.stdout.fileno(), self._pump)
        desk.tick_hooks.append(self._tick)

    # ── frames in ───────────────────────────────────────────────────────────
    def _pump(self):
        try:
            while True:
                chunk = os.read(self.ff.stdout.fileno(), 1 << 20)
                if not chunk:
                    return
                self.buf += chunk
        except BlockingIOError:
            pass
        frame = None
        while len(self.buf) >= self.fsize:        # newest frame wins
            frame = bytes(self.buf[:self.fsize])
            del self.buf[:self.fsize]
        if frame is not None:
            self.frame_img = Image.frombytes(
                "RGB", (self.app_w, self.app_h), frame)
            self.invalidate()

    def _tick(self, now):
        if self._dead:
            return
        if self.app.poll() is not None:           # app exited: window follows
            self.close()
            return
        self._rescue_windows()

    def _rescue_windows(self):
        """Apps that remember positions from a big screen can map their
        windows outside the little private display: pull them into view.
        Runs every tick — only windows FULLY outside are touched, so it
        never fights a drag happening in view."""
        try:
            y_next = 8
            for c in self.xd.screen().root.query_tree().children:
                a = c.get_attributes()
                if a.map_state != X.IsViewable:
                    continue
                g = c.get_geometry()
                if (g.x >= self.app_w or g.y >= self.app_h
                        or g.x + g.width <= 0 or g.y + g.height <= 0):
                    c.configure(x=8, y=y_next)
                    y_next = min(self.app_h - 20, y_next + g.height + 4)
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
        try:                          # desk key events are press-only: tap
            self.inj.key(ev.key, 1)
            self.inj.key(ev.key, 3)
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
                self.on_ok()
        else:
            self.title = "Install failed"
            self.invalidate()

    def request_close(self):
        if self._tick in self.desk.tick_hooks:
            self.desk.tick_hooks.remove(self._tick)
        if self.proc.poll() is None:
            self.proc.terminate()
        self.close()
