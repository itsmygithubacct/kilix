#!/usr/bin/env python3
"""kilix desktop — a Windows 95-style desktop environment in a kilix pane.

The whole desktop is rendered as pixels (PIL framebuffer, blitted through
the kitty graphics protocol — the same t=t /dev/shm path `kilix browse`
uses, or inline t=d in streamed sessions) with pixel-precise SGR mouse
input. Start bar, overlapping windows, desktop launchers, a file manager,
Notepad, an image viewer and a Settings app that edits the kilix config
live. Programs launch into new kilix tabs over kitty remote control.

Usage:  kilix desktop                 (from inside kilix)
        main.py --screenshot out.png --scene start   (headless render, tests)
Quit :  Start ▸ Shut Down…  ·  Ctrl+Alt+Q
"""
import argparse
import base64
import os
import select
import signal
import sys
import time

_here = os.path.dirname(os.path.abspath(__file__))
KILIX_HOME = os.environ.get("KILIX_HOME") or os.path.dirname(_here)
sys.path.insert(0, os.path.join(KILIX_HOME, "config"))   # browse (Term), gfx
sys.path.insert(0, _here)

from PIL import Image

import browse                        # Term: raw mode + kitty kbd/mouse parsing
import gfx                           # inline t=d frames for streamed sessions
import icons
import shell as shell_mod
import taskbar as taskbar_mod
import theme as T
import widgets as W
import wm as wm_mod

# keys browse's parser doesn't map (it never needed F-keys): add them
browse.SPECIAL_TILDE.update({
    11: ("F1", "F1", 112), 12: ("F2", "F2", 113), 13: ("F3", "F3", 114),
    14: ("F4", "F4", 115), 15: ("F5", "F5", 116), 17: ("F6", "F6", 117),
    18: ("F7", "F7", 118), 19: ("F8", "F8", 119), 20: ("F9", "F9", 120),
    21: ("F10", "F10", 121), 23: ("F11", "F11", 122),
    24: ("F12", "F12", 123)})
browse.SPECIAL_CSI.update({
    "P": ("F1", "F1", 112), "Q": ("F2", "F2", 113),
    "S": ("F4", "F4", 115)})


class DeskTerm(browse.Term):
    """browse.Term with any-motion mouse tracking (hover, drags)."""

    def enter(self):
        import tty
        tty.setraw(self.fd)
        # alt screen, hide cursor, no autowrap, kitty kbd protocol,
        # any-motion + SGR + SGR-pixels mouse, bracketed paste
        self.write("\x1b[?1049h\x1b[2J\x1b[?25l\x1b[?7l\x1b[>13u"
                   "\x1b[?1003h\x1b[?1006h\x1b[?1016h\x1b[?2004h"
                   "\x1b]2;kilix 95\x07")

    def restore(self):
        try:
            self.write("\x1b[<u\x1b[?1003l\x1b[?1006l\x1b[?1016l\x1b[?2004l"
                       "\x1b[?7h\x1b_Ga=d,d=A\x1b\\\x1b[?25h\x1b[?1049l")
        finally:
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)


class Desk:
    def __init__(self, term=None, size=None, draw_cursor=False):
        self.term = term
        if term:
            self.w = int(term.cols * term.cell_w)
            self.h = int(term.rows * term.cell_h)
        else:
            self.w, self.h = size or (1024, 768)
        self.fb = Image.new("RGB", (self.w, self.h), T.DESKTOP)
        self.dirty = True
        self.running = True
        self.clipboard = ""
        self.draw_cursor = draw_cursor
        self.mouse_pos = (self.w // 2, self.h // 2)
        self.menus = W.MenuHost(self)
        self.wm = wm_mod.WM(self)
        self.shell = shell_mod.Shell(self)
        self.taskbar = taskbar_mod.Taskbar(self)
        self.fd_hooks = {}            # fd -> callback (XPane video feeds etc.)
        self.tick_hooks = []          # callables(now), each loop pass
        self.mouse_owner = None
        self._buttons = 0
        self._last_click = (0.0, -99, -99, 0)
        # graphics transport (mirrors browse.py)
        self.stream = os.environ.get("KILIX_STREAM") == "1"
        wid = os.environ.get("KITTY_WINDOW_ID", str(os.getpid()))
        self.wid = wid
        self.img_id = 1 + ((int(wid) if wid.isdigit() else os.getpid())
                           % 4000)
        self.seq = 0

    def size(self):
        return self.w, self.h

    def add_fd(self, fd, cb):
        """Watch fd in the main select loop; cb() when readable."""
        self.fd_hooks[fd] = cb

    def remove_fd(self, fd):
        self.fd_hooks.pop(fd, None)

    def quit(self):
        self.running = False

    def set_clipboard(self, text):
        self.clipboard = text
        if self.term:
            b64 = base64.b64encode(text.encode()).decode()
            self.term.write(f"\x1b]52;c;{b64}\x07")

    # ── rendering ───────────────────────────────────────────────────────────
    def render(self):
        if not self.dirty:
            return
        fb = self.fb
        d = W.drawer(fb)
        self.shell.draw(fb, d)
        for win in self.wm.windows:
            if win.minimized:
                continue
            surf = win.render()
            fb.paste(surf, (win.x, win.y))
        self.taskbar.draw(fb, d)
        self.menus.draw(fb, d)
        if self.draw_cursor:
            self._paint_cursor(d)
        self.dirty = False
        self.blit()

    def _paint_cursor(self, d):
        x, y = self.mouse_pos
        pts = [(x, y), (x, y + 14), (x + 4, y + 10), (x + 7, y + 16),
               (x + 9, y + 15), (x + 6, y + 9), (x + 11, y + 9)]
        d.polygon(pts, fill=T.LIGHT, outline=T.TEXT)

    def blit(self):
        if not self.term:
            return
        self._last_blit = time.time()
        rgb = self.fb.tobytes()
        if self.stream:
            gfx.blit_direct(self.term, rgb, self.w, self.h,
                            self.term.cols, self.term.rows, self.img_id,
                            in_tmux=bool(os.environ.get("TMUX")))
            return
        self.seq = (self.seq + 1) % 8
        path = f"/dev/shm/tty-graphics-protocol-kilix95-{self.wid}-{self.seq}.rgb"
        with open(path, "wb") as f:
            f.write(rgb)
        payload = base64.b64encode(path.encode()).decode()
        self.term.write(
            f"\x1b[H\x1b_Ga=T,i={self.img_id},p=1,z=-1,t=t,f=24,"
            f"s={self.w},v={self.h},c={self.term.cols},r={self.term.rows},"
            f"q=2,C=1;{payload}\x1b\\")

    def cleanup_shm(self):
        for i in range(8):
            try:
                os.unlink(f"/dev/shm/tty-graphics-protocol-kilix95-"
                          f"{self.wid}-{i}.rgb")
            except OSError:
                pass

    # ── input normalization ─────────────────────────────────────────────────
    def _norm_key(self, raw):
        mods = max(0, raw.get("mods", 1) - 1)
        key = raw["key"]
        if len(key) == 1 and 57344 <= ord(key) <= 63743:
            return None               # kitty functional keycodes (bare mods)
        return W.Ev(kind="key", key=key, text=raw.get("text", ""),
                    shift=bool(mods & 1), alt=bool(mods & 2),
                    ctrl=bool(mods & 4))

    def _norm_mouse(self, raw):
        b = raw["b"]
        if b & 256:                   # SGR-pixel leave indicator
            return None
        x, y = raw["x"], raw["y"]
        self.mouse_pos = (x, y)
        mods = dict(shift=bool(b & 4), alt=bool(b & 8), ctrl=bool(b & 16))
        if b & 64:                    # wheel
            return W.Ev(kind="mouse", x=x, y=y,
                        wheel=(-1 if (b & 3) == 0 else 1), **mods)
        if b & 32:                    # motion
            return W.Ev(kind="mouse", x=x, y=y, move=True,
                        btn=self._buttons, **mods)
        btn = (b & 3) + 1
        if raw["press"]:
            t, lx, ly, cc = self._last_click
            cc = cc + 1 if (time.time() - t < 0.4 and abs(x - lx) < 5
                            and abs(y - ly) < 5) else 1
            self._last_click = (time.time(), x, y, cc)
            self._buttons |= 1 << (btn - 1)
            return W.Ev(kind="mouse", x=x, y=y, btn=btn, press=True,
                        clicks=cc, **mods)
        self._buttons &= ~(1 << (btn - 1))
        return W.Ev(kind="mouse", x=x, y=y, btn=btn, press=False, **mods)

    # ── dispatch ────────────────────────────────────────────────────────────
    def dispatch_mouse(self, ev):
        self._dispatch_mouse(ev)
        if not ev.press and not ev.move and not ev.wheel:
            # a release ALWAYS ends capture, even when a menu ate the event —
            # otherwise the owner set by the menu-opening press leaks and
            # swallows the next click
            self.mouse_owner = None

    def _dispatch_mouse(self, ev):
        if self.draw_cursor and (ev.move or ev.press):
            self.dirty = True
        if self.menus.active:
            if self.menus.on_mouse(ev):
                return
        if self.mouse_owner is not None:
            self.mouse_owner(ev)
            return
        win = self.wm.window_at(ev.x, ev.y)
        modal = self.wm.modal_top()
        if win is not None:
            if modal and win is not modal:
                if ev.press:
                    self.wm.activate(modal)
                return
            if ev.press:
                if self.wm.active is not win:
                    self.wm.activate(win)
                self.mouse_owner = self._route_window(win)
            win.on_mouse(ev)
            if self.wm.drag:
                self.mouse_owner = self._route_drag
            return
        if self.taskbar.hit(ev.x, ev.y):
            self.taskbar.on_mouse(ev)
            if ev.press:
                self.mouse_owner = self.taskbar.on_mouse
            return
        if modal:
            if ev.press:
                self.wm.activate(modal)
            return
        self.shell.on_mouse(ev)
        if ev.press:
            self.mouse_owner = self.shell.on_mouse

    def _route_window(self, win):
        def route(ev):
            if self.wm.drag:
                self._route_drag(ev)
            else:
                win.on_mouse(ev)
        return route

    def _route_drag(self, ev):
        if ev.move:
            self.wm.drag_motion(ev)
        elif not ev.press:
            self.wm.end_drag()

    def dispatch_key(self, ev):
        if self.menus.active:
            self.menus.on_key(ev)
            return
        if ev.ctrl and ev.alt and ev.key == "q":
            self.quit()
            return
        if ev.ctrl and ev.key == "Escape":
            self.taskbar.open_start_menu()
            return
        if ev.alt and ev.key == "F4":
            if self.wm.active:
                self.wm.active.request_close()
            return
        if self.wm.active and self.wm.active.on_key(ev):
            return
        if not self.wm.active or self.wm.modal_top() is None:
            self.shell.on_key(ev)

    def dispatch_paste(self, text):
        if self.menus.active:
            return
        win = self.wm.active
        if win and isinstance(win.focus, (W.TextField, W.TextArea)):
            win.focus.insert(text)
            if isinstance(win.focus, W.TextArea):
                win.focus._reveal()
            win.invalidate()

    # ── resize ──────────────────────────────────────────────────────────────
    def do_resize(self):
        self.term.refresh_size()
        self.w = int(self.term.cols * self.term.cell_w)
        self.h = int(self.term.rows * self.term.cell_h)
        self.fb = Image.new("RGB", (self.w, self.h), T.DESKTOP)
        self.shell.on_resize()
        for win in self.wm.windows:
            if win.maximized:
                win.x = win.y = 0
                win.w, win.h = self.w, self.h - T.TASKBAR_H
                win.surface = None
                win.on_resize()
            else:
                win.x = max(0, min(win.x, self.w - 60))
                win.y = max(0, min(win.y, self.h - T.TASKBAR_H - 20))
            win.dirty = True
        self.dirty = True

    # ── main loop ───────────────────────────────────────────────────────────
    def run(self):
        term = self.term
        resized = [False]
        signal.signal(signal.SIGWINCH, lambda *a: resized.__setitem__(0, True))
        for s in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(s, lambda *a: sys.exit(0))
        os.set_blocking(term.fd, False)
        term.enter()
        last_blink = time.time()
        self._last_blit = 0.0
        start = time.time()
        try:
            self.render()
            while self.running:
                rlist = [term.fd] + list(self.fd_hooks)
                r, _, _ = select.select(rlist, [], [], 0.25)
                for fd in r:
                    if fd == term.fd:
                        continue
                    cb = self.fd_hooks.get(fd)
                    if cb:
                        cb()
                if term.fd in r:
                    for raw in term.read_input():
                        if raw["kind"] == "key":
                            ev = self._norm_key(raw)
                            if ev:
                                self.dispatch_key(ev)
                        elif raw["kind"] == "mouse":
                            ev = self._norm_mouse(raw)
                            if ev:
                                self.dispatch_mouse(ev)
                        elif raw["kind"] == "paste":
                            self.dispatch_paste(raw["text"])
                if resized[0]:
                    resized[0] = False
                    self.do_resize()
                now = time.time()
                self.taskbar.tick(now)
                for hook in list(self.tick_hooks):
                    hook(now)
                if now - last_blink >= 0.53:
                    last_blink = now
                    self.wm.blink()
                # keepalive re-blits: kitty drops graphics sent while the
                # window is still settling (tab bar / pane title bar appear
                # right after startup and clear placements), and rendering is
                # otherwise damage-driven — so repeat the frame aggressively
                # for the first seconds and slowly forever after
                age = now - self._last_blit
                if age >= 0.5 and now - start < 5 or age >= 10:
                    self.blit()
                self.render()
        except KeyboardInterrupt:
            pass
        finally:
            term.restore()
            self.cleanup_shm()


# ── screenshot mode (offscreen render, used by the self-test) ───────────────

def _scene(desk, name):
    import apps
    if name in ("filemgr", "all"):
        apps.open(desk, "filemgr", KILIX_HOME)
    if name in ("notepad", "all"):
        apps.open(desk, "notepad", None)
        np = desk.wm.windows[-1]
        np.ta.set_text("kilix 95 — notepad self-test\n\nThe quick brown fox "
                       "jumps over the lazy dog.\n0123456789\n")
        np.x, np.y = 90, 60
    if name in ("settings", "all"):
        apps.open(desk, "settings", None)
    if name == "dialog":
        desk.shell.shutdown_dialog()
    if name == "launcher":
        desk.shell.create_launcher_dialog()
    if name == "menu":
        desk.shell._context(None, W.Ev(kind="mouse", x=300, y=200))
    if name == "start":
        desk.taskbar.open_start_menu()
        # walk into Programs so the submenu shows too
        m = desk.menus.stack[0]
        m.hot = 0
        for it, (x0, y0, x1, y1) in m.item_rects():
            if it.label == "Programs":
                desk.menus.open(it.submenu, x1 - 2, y0 - 2)
                break


def main():
    ap = argparse.ArgumentParser(prog="kilix desktop")
    ap.add_argument("--cursor", dest="cursor", action="store_true",
                    default=True, help=argparse.SUPPRESS)   # legacy (now default)
    ap.add_argument("--no-cursor", dest="cursor", action="store_false",
                    help="don't draw the desktop's own mouse pointer")
    ap.add_argument("--dir", help="desktop folder override")
    ap.add_argument("--screenshot", metavar="PNG",
                    help="render offscreen to PNG and exit (no terminal)")
    ap.add_argument("--scene", default="desktop",
                    choices=["desktop", "start", "filemgr", "notepad",
                             "settings", "dialog", "launcher", "menu", "all"])
    ap.add_argument("--size", default="1024x768",
                    help="screenshot size WxH")
    a = ap.parse_args()
    if a.dir:
        os.environ["KILIX_DESKTOP_DIR"] = a.dir
    if a.screenshot:
        w, h = (int(v) for v in a.size.lower().split("x"))
        desk = Desk(term=None, size=(w, h))
        _scene(desk, a.scene)
        desk.render()
        desk.fb.save(a.screenshot)
        print(f"wrote {a.screenshot} ({w}x{h}, scene={a.scene})")
        return
    desk = Desk(term=DeskTerm(), draw_cursor=a.cursor)
    desk.run()


if __name__ == "__main__":
    main()
