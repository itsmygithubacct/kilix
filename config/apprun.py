#!/usr/bin/env python3
"""kilix run — prototype: an X11 app living inside a kilix/kitty pane.

The i3 idea turned inside-out: instead of a WM arranging app windows,
each app gets a private off-screen X server and its pixels are streamed
into a pane, so GUI apps tile exactly like terminal programs.

  - display : a per-instance Xvfb (found on PATH, or the user-space copy
    under $XDG_DATA_HOME/kilix/xvfb/usr/bin/Xvfb)
  - pixels  : ffmpeg x11grab -> raw RGB pipe -> kitty graphics protocol,
    letterboxed into the pane by the GPU via the c=/r= cell-scaling
  - input   : kitty keyboard protocol (with press/release reporting, so
    games can hold keys) + SGR-pixel mouse, injected with XTest — into
    the private display only, never the real one

Usage: kilix run [--size WxH] [--fps N] command [args…]
Keys : everything is forwarded to the app; Ctrl+Q quits the kitten.

Known limits (prototype): no sound routing; apps that grab the pointer
(e.g. DOSBox autolock) see relative motion, so the app cursor and the
pane cursor can drift; the X screen size is fixed at startup — pane
resizes rescale the picture instead of resizing the app.
"""
import os
import select
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browse  # reuse Term (raw mode, kitty kbd/mouse parsing), not Chrome

try:
    from Xlib import X, XK, display as xdisplay
    from Xlib.ext import xtest
except ImportError:
    sys.exit("kilix run: python3-xlib is required (apt install python3-xlib)")

LOG_PATH = os.environ.get("KILIX_RUN_LOG")


def log(*a):
    if LOG_PATH:
        with open(LOG_PATH, "a") as f:
            f.write(f"[{time.time():.3f}] " + " ".join(str(x) for x in a) + "\n")


def find_xvfb():
    p = shutil.which("Xvfb")
    if p:
        return p
    data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    p = os.path.join(data, "kilix", "xvfb", "usr", "bin", "Xvfb")
    if os.access(p, os.X_OK):
        return p
    sys.exit("kilix run: Xvfb not found — install xvfb, or unpack the .deb "
             "into ~/.local/share/kilix/xvfb (apt-get download xvfb && "
             "dpkg -x xvfb_*.deb ~/.local/share/kilix/xvfb)")


# kitty functional keycodes for modifier keys -> X keysym names
MOD_KEYSYMS = {57441: "Shift_L", 57442: "Control_L", 57443: "Alt_L",
               57444: "Super_L", 57447: "Shift_R", 57448: "Control_R",
               57449: "Alt_R", 57450: "Super_R"}

NAME_KEYSYMS = {"Enter": "Return", "Escape": "Escape",
                "Backspace": "BackSpace", "Tab": "Tab",
                "ArrowUp": "Up", "ArrowDown": "Down",
                "ArrowLeft": "Left", "ArrowRight": "Right",
                "Home": "Home", "End": "End", "PageUp": "Prior",
                "PageDown": "Next", "Insert": "Insert", "Delete": "Delete",
                **{f"F{i}": f"F{i}" for i in range(1, 13)}}

# legacy CSI ~ numbers for F1-F12 (browse's tables stop at PageDown)
FKEY_TILDE = {11: "F1", 12: "F2", 13: "F3", 14: "F4", 15: "F5", 17: "F6",
              18: "F7", 19: "F8", 20: "F9", 21: "F10", 23: "F11", 24: "F12"}
FKEY_CSI = {"P": "F1", "Q": "F2", "S": "F4"}


class RunTerm(browse.Term):
    """browse.Term + key press/release reporting (kbd protocol flag 2)."""

    def enter(self):
        import tty
        tty.setraw(self.fd)
        # same as browse, but >15u: disambiguate + report event types +
        # alternates + all-keys-as-escapes — releases matter for games.
        # ?1003h = ANY-motion tracking (not just drag): the app needs free
        # pointer motion for hover/mouse-look, e.g. xeyes or an FPS.
        self.write("\x1b[?1049h\x1b[2J\x1b[?25l\x1b[?7l\x1b[>15u"
                   "\x1b[?1003h\x1b[?1006h\x1b[?1016h\x1b[?2004h")

    def restore(self):
        # browse.Term.restore disables ?1002l but not ?1003l (all-motion),
        # which we enable above — turn it off or the shell gets flooded.
        self.write("\x1b[?1003l")
        super().restore()

    def _parse_csi(self, params, final):
        ev = super()._parse_csi(params, final)
        parts = params.split(";") if params else []
        if ev is None and final == "~" and parts:
            num = int(parts[0].split(":")[0] or 0)
            if num in FKEY_TILDE:
                mods = int(parts[1].split(":")[0]) if len(parts) > 1 else 1
                ev = {"kind": "key", "key": FKEY_TILDE[num], "code": "",
                      "vk": 0, "mods": mods, "text": ""}
        if ev is None and final in FKEY_CSI:
            mods = int(parts[1].split(":")[0]) if len(parts) > 1 else 1
            ev = {"kind": "key", "key": FKEY_CSI[final], "code": "",
                  "vk": 0, "mods": mods, "text": ""}
        if ev and ev.get("kind") == "key":
            etype = 1  # 1 press, 2 repeat, 3 release
            if len(parts) > 1 and ":" in parts[1]:
                try:
                    etype = int(parts[1].split(":")[1] or 1)
                except ValueError:
                    pass
            ev["event"] = etype
        return ev


class AppPane:
    def __init__(self, cmd, app_w, app_h, fps):
        self.cmd = cmd
        self.app_w, self.app_h = app_w, app_h
        self.fps = fps
        self.term = RunTerm()
        self.wid = os.environ.get("KITTY_WINDOW_ID", str(os.getpid()))
        self.seq = 0
        self.frames = 0
        self.resized = False
        self.status = "starting…"
        self.prev_status = None
        self.last_frame = None
        self.ffbuf = bytearray()
        self.xvfb = self.app = self.ff = None
        self.xd = None
        self.compute_layout()

    # ---- geometry ----------------------------------------------------------
    def compute_layout(self):
        t = self.term
        view_rows = t.rows - 1                     # last row = status
        pane_w, pane_h = t.cols * t.cell_w, view_rows * t.cell_h
        scale = min(pane_w / self.app_w, pane_h / self.app_h)
        self.img_cols = max(1, round(self.app_w * scale / t.cell_w))
        self.img_rows = max(1, min(view_rows,
                                   round(self.app_h * scale / t.cell_h)))
        self.off_col = (t.cols - self.img_cols) // 2
        self.off_row = (view_rows - self.img_rows) // 2
        # the pixel box the image actually occupies, for mouse mapping
        self.box = (self.off_col * t.cell_w, self.off_row * t.cell_h,
                    self.img_cols * t.cell_w, self.img_rows * t.cell_h)

    # ---- processes ---------------------------------------------------------
    def start(self):
        r, w = os.pipe()
        self.xvfb = subprocess.Popen(
            [find_xvfb(), "-displayfd", str(w),
             "-screen", "0", f"{self.app_w}x{self.app_h}x24",
             "-nolisten", "tcp"],
            pass_fds=(w,), stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        os.close(w)
        num = b""
        deadline = time.time() + 10
        while not num.endswith(b"\n") and time.time() < deadline:
            if select.select([r], [], [], deadline - time.time())[0]:
                chunk = os.read(r, 16)
                if not chunk:
                    break
                num += chunk
        os.close(r)
        if not num.strip():
            raise RuntimeError("Xvfb did not start")
        self.disp = f":{int(num)}"
        log("Xvfb on", self.disp)

        env = dict(os.environ, DISPLAY=self.disp)
        self.app = subprocess.Popen(self.cmd, env=env,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        self.xd = xdisplay.Display(self.disp)
        self.focus_app_window()

        self.ff = subprocess.Popen(
            ["ffmpeg", "-loglevel", "quiet",
             "-f", "x11grab", "-framerate", str(self.fps),
             "-video_size", f"{self.app_w}x{self.app_h}", "-i", self.disp,
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(self.ff.stdout.fileno(), False)
        self.status = f"{os.path.basename(self.cmd[0])} on {self.disp}"

    def focus_app_window(self):
        """No WM on the Xvfb: size the app's window to fill the virtual
        screen (small apps like xclock default to a tiny window in the
        top-left corner otherwise), focus it, and park the pointer inside
        (PointerRoot focus follows the pointer)."""
        root = self.xd.screen().root
        deadline = time.time() + 15
        while time.time() < deadline:
            for c in root.query_tree().children:
                try:
                    if c.get_attributes().map_state == X.IsViewable:
                        # fill the screen so the whole --size is the app, not
                        # black padding; apps free to ignore the hint keep
                        # their size (then they letterbox as before)
                        c.configure(x=0, y=0, width=self.app_w, height=self.app_h)
                        self.xd.set_input_focus(c, X.RevertToPointerRoot,
                                                X.CurrentTime)
                        xtest.fake_input(self.xd, X.MotionNotify,
                                         x=self.app_w // 2, y=self.app_h // 2)
                        self.xd.sync()
                        log("focused app window", hex(c.id))
                        return
                except Exception:
                    pass
            if self.app.poll() is not None:
                raise RuntimeError(f"app exited (rc={self.app.returncode})")
            time.sleep(0.25)
        log("no app window appeared; capturing root anyway")

    # ---- pixel layer -------------------------------------------------------
    def pump_frames(self):
        fd = self.ff.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 1 << 20)
            except BlockingIOError:
                break
            if not chunk:
                raise EOFError("ffmpeg capture stream closed")
            self.ffbuf += chunk
        fsize = self.app_w * self.app_h * 3
        frame = None
        while len(self.ffbuf) >= fsize:      # keep only the newest frame
            frame = bytes(self.ffbuf[:fsize])
            del self.ffbuf[:fsize]
        if frame is not None and frame != self.last_frame:
            self.last_frame = frame
            self.blit(frame)

    def blit(self, rgb):
        import base64
        self.seq = (self.seq + 1) % 8
        name = f"tty-graphics-protocol-kilix-run-{self.wid}-{self.seq}.rgb"
        path = "/dev/shm/" + name
        with open(path, "wb") as f:
            f.write(rgb)
        payload = base64.b64encode(path.encode()).decode()
        self.term.write(
            f"\x1b[{self.off_row + 1};{self.off_col + 1}H"
            f"\x1b_Ga=T,i=1,p=1,z=-1,t=t,f=24,"
            f"s={self.app_w},v={self.app_h},"
            f"c={self.img_cols},r={self.img_rows},q=2,C=1;{payload}\x1b\\")
        self._blit_t = time.time()
        self.frames += 1
        if self.frames == 1 or self.frames % 300 == 0:
            log(f"frames={self.frames}")

    def render_status(self):
        body = (f" kilix run — {' '.join(self.cmd)[:40]} · {self.disp} "
                f"{self.app_w}x{self.app_h} · {self.frames} frames · "
                f"Ctrl+Q quit")
        body = body[:self.term.cols].ljust(self.term.cols)
        s = f"\x1b[{self.term.rows};1H\x1b[0;7m{body}\x1b[0m"
        if s != self.prev_status:
            self.term.write(s)
            self.prev_status = s

    # ---- input -------------------------------------------------------------
    def keysym_for(self, key):
        if len(key) == 1:
            o = ord(key)
            if o in MOD_KEYSYMS:
                return XK.string_to_keysym(MOD_KEYSYMS[o])
            if 57344 <= o <= 63743:      # other functional keys: unmapped
                return 0
            if o < 256:                  # latin-1 keysyms == codepoints
                return o
            return 0
        name = NAME_KEYSYMS.get(key)
        return XK.string_to_keysym(name) if name else 0

    def on_key(self, ev):
        mods = max(0, ev["mods"] - 1)
        etype = ev.get("event", 1)
        if (mods & 4) and ev["key"] == "q" and etype == 1:
            raise KeyboardInterrupt
        if etype == 2:
            return                       # Xvfb autorepeats held keys itself
        keysym = self.keysym_for(ev["key"])
        if not keysym:
            return
        keycode = self.xd.keysym_to_keycode(keysym)
        if not keycode:
            return
        xtest.fake_input(self.xd, X.KeyPress if etype == 1 else X.KeyRelease,
                         keycode)
        self.xd.flush()

    def on_paste(self, text):
        for ch in text:
            keysym = self.keysym_for(ch if ch != "\n" else "Enter")
            keycode = self.xd.keysym_to_keycode(keysym) if keysym else 0
            if keycode:
                xtest.fake_input(self.xd, X.KeyPress, keycode)
                xtest.fake_input(self.xd, X.KeyRelease, keycode)
        self.xd.flush()

    def on_mouse(self, ev):
        bx, by, bw, bh = self.box
        ax = min(self.app_w - 1, max(0, round((ev["x"] - bx) * self.app_w / bw)))
        ay = min(self.app_h - 1, max(0, round((ev["y"] - by) * self.app_h / bh)))
        b = ev["b"]
        if b & 64:                       # wheel -> X buttons 4/5
            btn = 4 if (b & 3) == 0 else 5
            xtest.fake_input(self.xd, X.MotionNotify, x=ax, y=ay)
            xtest.fake_input(self.xd, X.ButtonPress, btn)
            xtest.fake_input(self.xd, X.ButtonRelease, btn)
        elif b & 32:                     # motion (with or without drag)
            xtest.fake_input(self.xd, X.MotionNotify, x=ax, y=ay)
        else:
            btn = (b & 3) + 1            # 0/1/2 -> left/middle/right
            xtest.fake_input(self.xd, X.MotionNotify, x=ax, y=ay)
            xtest.fake_input(self.xd, X.ButtonPress if ev["press"]
                             else X.ButtonRelease, btn)
        self.xd.flush()

    # ---- lifecycle ---------------------------------------------------------
    def do_resize(self):
        self.term.refresh_size()
        self.compute_layout()
        self.prev_status = None
        # placement (i=1,p=1) is replaced on next blit; clear stale cells
        self.term.write("\x1b[2J")
        self.last_frame = None           # force a re-blit at the new size
        if self.ffbuf:
            del self.ffbuf[:]

    def run(self):
        signal.signal(signal.SIGWINCH, lambda *a: setattr(self, "resized", True))
        os.set_blocking(self.term.fd, False)
        err = None
        self.term.enter()
        try:
            self.start()
            self._loop_start = time.time()
            while True:
                r, _, _ = select.select(
                    [self.term.fd, self.ff.stdout], [], [], 0.25)
                if self.ff.stdout in r:
                    self.pump_frames()
                if self.term.fd in r:
                    for ev in self.term.read_input():
                        if ev["kind"] == "key":
                            self.on_key(ev)
                        elif ev["kind"] == "mouse":
                            self.on_mouse(ev)
                        elif ev["kind"] == "paste":
                            self.on_paste(ev["text"])
                if self.resized:
                    self.resized = False
                    self.do_resize()
                # A first placement can be dropped right after startup (seen
                # as a pane that stays black while the app clearly has output;
                # q=2 means we never hear the error). Placement is otherwise
                # reliable, so self-heal by re-placing the current frame: fast
                # during a warmup window (recover in <1s), then a cheap idle
                # interval so an app sitting on a static screen isn't rewritten
                # every tick. Animation blits on its own and resets the timer.
                if self.last_frame is not None:
                    idle = time.time() - getattr(self, "_blit_t", 0)
                    warming = time.time() - self._loop_start < 4
                    if idle > (0.4 if warming else 3):
                        self.blit(self.last_frame)
                if self.app.poll() is not None:
                    err = (None if self.app.returncode == 0 else
                           f"app exited with rc={self.app.returncode}")
                    break
                self.render_status()
        except KeyboardInterrupt:
            pass
        except (EOFError, BrokenPipeError) as e:
            err = str(e)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        finally:
            self.term.restore()
            for p in (self.app, self.ff, self.xvfb):
                if p is not None:
                    try:
                        p.terminate()
                        p.wait(timeout=3)
                    except Exception:
                        try:
                            p.kill()
                        except Exception:
                            pass
            for i in range(8):
                try:
                    os.unlink(f"/dev/shm/tty-graphics-protocol-kilix-run-"
                              f"{self.wid}-{i}.rgb")
                except OSError:
                    pass
        if err:
            print(f"kilix run: {err}", file=sys.stderr)
            sys.exit(1)


def main():
    args = sys.argv[1:]
    app_w, app_h, fps = 640, 400, 20
    while args and args[0].startswith("--"):
        if args[0] == "--size" and len(args) > 1:
            app_w, app_h = (int(v) for v in args[1].lower().split("x"))
            args = args[2:]
        elif args[0].startswith("--size="):
            app_w, app_h = (int(v) for v in args[0][7:].lower().split("x"))
            args = args[1:]
        elif args[0] == "--fps" and len(args) > 1:
            fps = int(args[1])
            args = args[2:]
        elif args[0].startswith("--fps="):
            fps = int(args[0][6:])
            args = args[1:]
        else:
            sys.exit(f"kilix run: unknown option {args[0]}")
    if not args:
        sys.exit("usage: kilix run [--size WxH] [--fps N] command [args…]")
    AppPane(args, app_w, app_h, fps).run()


if __name__ == "__main__":
    main()
