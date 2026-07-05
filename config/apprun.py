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
import gfx      # direct (t=d) graphics transmission for streamed sessions
import xinject  # XTest keyboard/mouse injection (shared with kilix desktop)
import stream   # Xvnc/Xvfb + VNC/HLS/bridge supervisor for serve modes

try:
    from Xlib import X, display as xdisplay
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
    def __init__(self, cmd, app_w, app_h, fps, serve=False, lan=False, hls=False):
        self.cmd = cmd
        self.app_w, self.app_h = app_w, app_h
        self.fps = fps
        # --serve: also expose the app to remote devices via Xvnc (view+control).
        # --hls/--lan add the browser broadcast/bridge tiers (see stream.py).
        self.serve, self.lan, self.hls = serve, lan, hls
        self.session = os.environ.get("KILIX_SESSION") or f"run-{os.getpid()}"
        self.sup = stream.StreamSupervisor(self.session)
        self.rfb_port = None
        self.full_pw = self.view_pw = None
        self.http_port = self.token = self.tls_fp = None
        self.term = RunTerm()
        self.wid = os.environ.get("KITTY_WINDOW_ID", str(os.getpid()))
        self.seq = 0
        # In a streamed/served session (KILIX_STREAM=1) inline the pixels (t=d)
        # so a remote kitty can render them; otherwise use the fast local t=t
        # /dev/shm path. img_id is stable per producer so two apps reaching one
        # client terminal never collide on the graphics id.
        self.stream = os.environ.get("KILIX_STREAM") == "1"
        self.img_id = 1 + ((int(self.wid) if self.wid.isdigit()
                            else os.getpid()) % 4000)
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
        self.start_display()
        self.start_app_and_capture()

    def start_display(self):
        if self.serve:
            self._start_xvnc()
        else:
            self._start_xvfb()

    def _start_xvfb(self):
        # Xvfb (with -auth, via the supervisor) so the private display is not
        # reachable by other local users on its /tmp/.X11-unix socket.
        n = self.sup.pick_display()
        self.sup.start_xvfb(n, self.app_w, self.app_h)
        self.disp = f":{n}"
        self.xvfb = None                 # owned by the supervisor
        os.environ["XAUTHORITY"] = self.sup.xauth
        log("Xvfb on", self.disp)

    def _start_xvnc(self):
        # Serve mode: run the app on Xvnc (a virtual X server that is ALSO a VNC
        # server), so the same display the local pane captures is exposed for
        # remote view+control. Xvnc lacks -displayfd, so reserve the number.
        n = self.sup.pick_display()
        # rfb port tied to the flock-reserved display (5900+n) so two kilix
        # serves never race for it; ephemeral fallback only if it's taken.
        self.rfb_port = 5900 + n if stream.port_free(5900 + n) else stream.free_port()
        # VncAuth caps passwords at 8 chars; two roles -> full + view-only.
        self.full_pw = self.sup.mint_token()[:8]
        self.view_pw = self.sup.mint_token()[:8]
        pwfile = self.sup.make_vncpw(self.full_pw, self.view_pw)
        self.sup.start_xvnc(n, self.app_w, self.app_h, self.rfb_port, pwfile,
                            desktop=f"kilix-run {os.path.basename(self.cmd[0])}")
        self.disp = f":{n}"
        self.xvfb = None                 # Xvnc is owned by the supervisor
        os.environ["XAUTHORITY"] = self.sup.xauth
        log("Xvnc on", self.disp, "rfb", self.rfb_port)
        if self.lan or self.hls:
            self._start_web_tier(n)

    def _start_web_tier(self, n):
        """Browser tier: a WS<->RFB bridge (noVNC) and/or an HLS broadcast,
        loopback by default, or LAN over TLS+token with --lan."""
        self.http_port = stream.free_port()
        self.token = self.sup.mint_token()
        hlsdir = None
        if self.hls:
            hlsdir = os.path.join(self.sup.runtime_dir, "hls")
            self.sup.start_hls(n, self.app_w, self.app_h, hlsdir, fps=self.fps)
        tls = self.sup.tls_cert() if self.lan else None
        self.tls_fp = tls[2] if tls else None
        self.sup.start_bridge(rfb_port=self.rfb_port, http_port=self.http_port,
                              token=self.token, hlsdir=hlsdir, tls=tls,
                              lan=self.lan,
                              what=f"run {os.path.basename(self.cmd[0])}")
        log("bridge on http", self.http_port, "lan" if self.lan else "loopback")

    def start_app_and_capture(self):
        env = dict(os.environ, DISPLAY=self.disp)
        self.app = subprocess.Popen(self.cmd, env=env,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        self.xd = xdisplay.Display(self.disp)
        self.inj = xinject.Injector(self.xd, self.app_w, self.app_h)
        self.focus_app_window()

        self.ff = subprocess.Popen(
            ["ffmpeg", "-loglevel", "quiet",
             "-f", "x11grab", "-framerate", str(self.fps),
             "-video_size", f"{self.app_w}x{self.app_h}", "-i", self.disp,
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        os.set_blocking(self.ff.stdout.fileno(), False)
        self.status = f"{os.path.basename(self.cmd[0])} on {self.disp}"

    def announce(self):
        """Print connect instructions to the LOCAL terminal (before the app
        takes the alt screen) and to a 0600 file. Passwords appear here only —
        never in the pane/status, and the VNC stream is the app's display, not
        this pane, so viewers never see them."""
        lan_host = stream.lan_ip() if self.lan else None
        self.sup.print_connect(what=f"app '{os.path.basename(self.cmd[0])}'",
                               rfb_port=self.rfb_port,
                               full_pw=self.full_pw, view_pw=self.view_pw,
                               http_port=self.http_port, token=self.token,
                               lan_host=lan_host, tls_fp=self.tls_fp)
        try:
            _cf = os.open(os.path.join(self.sup.runtime_dir, "connect.txt"),
                          os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(_cf, "w") as f:
                f.write(f"kilix run --serve {' '.join(self.cmd)}\n"
                        f"VNC        127.0.0.1:{self.rfb_port}\n"
                        f"control pw {self.full_pw}\n"
                        f"view-only  {self.view_pw}\n")
                if self.http_port:
                    scheme = "https" if self.lan else "http"
                    f.write(f"browser    {scheme}://127.0.0.1:{self.http_port}/?t={self.token}\n")
        except OSError:
            pass

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
        if self.stream:
            # streamed session: inline the pixels (t=d) — the /dev/shm path of
            # the local branch below is meaningless to a kitty on another box.
            gfx.blit_direct(self.term, rgb, self.app_w, self.app_h,
                            self.img_cols, self.img_rows, self.img_id,
                            self.off_row + 1, self.off_col + 1,
                            in_tmux=bool(os.environ.get("TMUX")))
        else:
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
        serve = f" · VNC :{self.rfb_port}" if self.serve else ""
        body = (f" kilix run — {' '.join(self.cmd)[:36]} · {self.disp} "
                f"{self.app_w}x{self.app_h} · {self.frames}f{serve} · "
                f"Ctrl+Q quit")
        body = body[:self.term.cols].ljust(self.term.cols)
        s = f"\x1b[{self.term.rows};1H\x1b[0;7m{body}\x1b[0m"
        if s != self.prev_status:
            self.term.write(s)
            self.prev_status = s

    # ---- input (injected via xinject.Injector into the private display) ----
    def on_key(self, ev):
        mods = max(0, ev["mods"] - 1)
        etype = ev.get("event", 1)
        if (mods & 4) and ev["key"] == "q" and etype == 1:
            raise KeyboardInterrupt
        if etype == 2:
            return                       # Xvfb autorepeats held keys itself
        self.inj.key(ev["key"], etype)

    def on_paste(self, text):
        self.inj.paste(text)

    def on_mouse(self, ev):
        self.inj.mouse(ev, self.box)

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
        for _s in (signal.SIGTERM, signal.SIGHUP):   # -> finally + sup.cleanup()
            signal.signal(_s, lambda *a: sys.exit(0))
        os.set_blocking(self.term.fd, False)
        err = None
        if self.serve:
            # Bring Xvnc up first so connect details print to the LOCAL terminal
            # before the app takes the alt screen (and never into the captured
            # stream — passwords must not reach remote viewers).
            try:
                self.start_display()
                self.announce()
            except Exception as e:
                if self.sup is not None:
                    self.sup.cleanup()
                print(f"kilix run: {e}", file=sys.stderr)
                sys.exit(1)
        self.term.enter()
        try:
            if self.serve:
                self.start_app_and_capture()   # display already up
            else:
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
            if getattr(self, "inj", None) is not None:
                self.inj.release_all()   # no keys/buttons left stuck down
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
            if self.sup is not None:
                self.sup.cleanup()
        if err:
            print(f"kilix run: {err}", file=sys.stderr)
            sys.exit(1)


def main():
    args = sys.argv[1:]
    app_w, app_h, fps = 640, 400, 20
    serve = lan = hls = False
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
        elif args[0] == "--serve":
            serve = True
            args = args[1:]
        elif args[0] == "--lan":            # implies serve + LAN bridge (TLS+token)
            serve = lan = True
            args = args[1:]
        elif args[0] == "--hls":            # broadcast tier (many view-only viewers)
            hls = True
            args = args[1:]
        else:
            sys.exit(f"kilix run: unknown option {args[0]}")
    if not args:
        sys.exit("usage: kilix run [--size WxH] [--fps N] "
                 "[--serve|--lan] [--hls] command [args…]")
    AppPane(args, app_w, app_h, fps, serve=serve, lan=lan, hls=hls).run()


if __name__ == "__main__":
    main()
