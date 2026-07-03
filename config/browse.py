#!/usr/bin/env python3
"""kilix browse — Phase 1 prototype of the browser kitten.

Renders current Chrome inside a kilix/kitty pane:
  - pixel layer: CDP screencast frames blitted via the kitty graphics
    protocol at z=-1 (below text), through /dev/shm temp files
  - glyph layer: page text drawn as real terminal cells (selectable),
    harvested from DOMSnapshot; Chrome's own text ink is made transparent
  - input: kitty keyboard protocol + SGR-pixel mouse, forwarded over CDP
  - copy: Ctrl+C also exports the DOM selection via OSC 52

Usage: browse.py [url]     (run inside kilix; `kilix browse <url>`)
Keys : Ctrl+L url bar · Alt+←/→ history · Ctrl+R reload · Ctrl+Q quit
       Shift+drag = native terminal selection of the glyph text
Design doc: ~/research/kilix/browser-kitten-implementation.md
"""
import array
import base64
import fcntl
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import unicodedata
from io import BytesIO

try:
    from PIL import Image
except ImportError:
    sys.exit("kilix browse: python3-pil is required for the pixel layer")

LOG_PATH = os.environ.get("KILIX_BROWSE_LOG")


def log(*a):
    if LOG_PATH:
        with open(LOG_PATH, "a") as f:
            f.write(f"[{time.time():.3f}] " + " ".join(str(x) for x in a) + "\n")


# ───────────────────────── CDP over --remote-debugging-pipe ─────────────────

class CDP:
    """NUL-framed JSON over Chrome's fds 3 (it reads) / 4 (it writes)."""

    def __init__(self, url, width, height, profile):
        a, b = os.pipe()   # we write -> chrome fd 3
        c, d = os.pipe()   # chrome fd 4 -> we read
        r_in = fcntl.fcntl(a, fcntl.F_DUPFD, 10)
        self.w_in = fcntl.fcntl(b, fcntl.F_DUPFD, 10)
        self.r_out = fcntl.fcntl(c, fcntl.F_DUPFD, 10)
        w_out = fcntl.fcntl(d, fcntl.F_DUPFD, 10)
        for fd in (a, b, c, d):
            os.close(fd)
        # pass_fds preserves exact numbers: pin child-facing ends to 3/4
        os.dup2(r_in, 3)
        os.dup2(w_out, 4)
        os.close(r_in)
        os.close(w_out)
        chrome = None
        for cand in ("google-chrome", "google-chrome-stable", "chromium",
                     "chromium-browser"):
            if any(os.access(os.path.join(p, cand), os.X_OK)
                   for p in os.environ.get("PATH", "").split(":")):
                chrome = cand
                break
        if not chrome:
            raise RuntimeError("no chrome/chromium binary on PATH")
        self.proc = subprocess.Popen(
            [chrome, "--headless=new", "--remote-debugging-pipe",
             "--no-first-run", "--no-default-browser-check",
             "--hide-scrollbars", "--mute-audio",
             "--autoplay-policy=no-user-gesture-required",
             f"--user-data-dir={profile}",
             f"--window-size={width},{height}", "about:blank"],
            pass_fds=(3, 4),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(3)
        os.close(4)
        self.buf = b""
        self.next_id = 0
        self.pending = {}   # id -> result (filled by pump)
        self.events = []    # queued events

    def fileno(self):
        return self.r_out

    def send(self, method, params=None, session=None):
        self.next_id += 1
        msg = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        os.write(self.w_in, json.dumps(msg).encode() + b"\0")
        return self.next_id

    def pump(self, block=False, timeout=None):
        """Read whatever is available; return list of event messages."""
        out = []
        while True:
            if b"\0" in self.buf:
                raw, self.buf = self.buf.split(b"\0", 1)
                m = json.loads(raw)
                if "id" in m:
                    self.pending[m["id"]] = m
                else:
                    out.append(m)
                continue
            if not block and not select.select([self.r_out], [], [], 0)[0]:
                return out
            if block and timeout is not None:
                if not select.select([self.r_out], [], [], timeout)[0]:
                    raise TimeoutError("CDP read timeout")
            chunk = os.read(self.r_out, 1 << 20)
            if not chunk:
                raise EOFError("chrome closed the CDP pipe")
            self.buf += chunk
            block = False  # got data; drain non-blocking from here

    def call(self, method, params=None, session=None, timeout=30):
        """Synchronous call: pump until the result arrives (events queue)."""
        mid = self.send(method, params, session)
        end = time.time() + timeout
        while mid not in self.pending:
            self.events.extend(self.pump(block=True, timeout=end - time.time()))
        m = self.pending.pop(mid)
        if "error" in m:
            raise RuntimeError(f"{method}: {m['error']}")
        return m.get("result", {})

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


# ───────────────────────── terminal plumbing ─────────────────────────────────

CSI_RE = re.compile(rb"\x1b\[([\x30-\x3f]*)([\x20-\x2f]*)([\x40-\x7e])")

# kitty mods bitmask (mods-1): 1 shift, 2 alt, 4 ctrl, 8 super
# CDP modifiers: 1 alt, 2 ctrl, 4 meta, 8 shift
def cdp_mods(kitty_mods):
    m = max(0, kitty_mods - 1)
    return ((1 if m & 2 else 0) | (2 if m & 4 else 0) |
            (4 if m & 8 else 0) | (8 if m & 1 else 0))


SPECIAL_CSI = {  # final letter -> (key, code, windowsVirtualKeyCode)
    "A": ("ArrowUp", "ArrowUp", 38), "B": ("ArrowDown", "ArrowDown", 40),
    "C": ("ArrowRight", "ArrowRight", 39), "D": ("ArrowLeft", "ArrowLeft", 37),
    "H": ("Home", "Home", 36), "F": ("End", "End", 35),
}
SPECIAL_TILDE = {2: ("Insert", "Insert", 45), 3: ("Delete", "Delete", 46),
                 5: ("PageUp", "PageUp", 33), 6: ("PageDown", "PageDown", 34)}
SPECIAL_U = {13: ("Enter", "Enter", 13), 9: ("Tab", "Tab", 9),
             127: ("Backspace", "Backspace", 8), 27: ("Escape", "Escape", 27)}


class Term:
    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.out = sys.stdout.fileno()
        if not os.isatty(self.fd):
            sys.exit("kilix browse: must run on a terminal (inside kilix)")
        ws = array.array("H", [0, 0, 0, 0])
        fcntl.ioctl(self.fd, termios.TIOCGWINSZ, ws)
        self.rows, self.cols, self.xpix, self.ypix = ws
        if not self.xpix:
            sys.exit("kilix browse: terminal does not report pixel size")
        self.saved = termios.tcgetattr(self.fd)
        self.inbuf = b""

    @property
    def cell_w(self):
        return self.xpix / self.cols

    @property
    def cell_h(self):
        return self.ypix / self.rows

    def refresh_size(self):
        ws = array.array("H", [0, 0, 0, 0])
        fcntl.ioctl(self.fd, termios.TIOCGWINSZ, ws)
        self.rows, self.cols, self.xpix, self.ypix = ws

    def write(self, s):
        # stdin/stdout share one pty file description; stdin's O_NONBLOCK
        # applies here too, so handle partial/blocked writes explicitly.
        if isinstance(s, str):
            s = s.encode()
        mv = memoryview(s)
        while mv:
            try:
                n = os.write(self.out, mv)
                mv = mv[n:]
            except BlockingIOError:
                select.select([], [self.out], [])

    def enter(self):
        import tty
        tty.setraw(self.fd)
        # alt screen, hide cursor, no autowrap (glyph grid must never shift),
        # kbd protocol (1|4|8: disambiguate, alternates, all-keys-as-escapes),
        # mouse: drag+SGR+SGR-pixels, bracketed paste
        self.write("\x1b[?1049h\x1b[2J\x1b[?25l\x1b[?7l\x1b[>13u"
                   "\x1b[?1002h\x1b[?1006h\x1b[?1016h\x1b[?2004h")

    def restore(self):
        try:
            self.write("\x1b[<u\x1b[?1002l\x1b[?1006l\x1b[?1016l\x1b[?2004l"
                       "\x1b[?7h\x1b_Ga=d,d=A\x1b\\\x1b[?25h\x1b[?1049l")
        finally:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def read_input(self):
        """Return list of parsed events: dicts with kind key/mouse/text."""
        try:
            self.inbuf += os.read(self.fd, 65536)
        except BlockingIOError:
            pass
        events, buf = [], self.inbuf
        while buf:
            if buf.startswith(b"\x1b["):
                m = CSI_RE.match(buf)
                if not m:
                    if len(buf) > 64:  # garbage; drop ESC and resync
                        buf = buf[1:]
                        continue
                    break  # incomplete sequence, wait for more bytes
                buf = buf[m.end():]
                ev = self._parse_csi(m.group(1).decode(), m.group(3).decode())
                if ev:
                    if ev.get("paste_begin"):
                        # capture raw text until ESC[201~
                        end = buf.find(b"\x1b[201~")
                        if end < 0:  # wait for full paste
                            buf = m.group(0) + buf  # put marker back
                            break
                        events.append({"kind": "paste",
                                       "text": buf[:end].decode("utf-8", "replace")})
                        buf = buf[end + 6:]
                    else:
                        events.append(ev)
            elif buf.startswith(b"\x1b"):
                if len(buf) == 1:
                    break
                buf = buf[1:]  # stray ESC (shouldn't happen with flag 8)
            else:
                # raw text (paste without brackets, or IME): one char run
                nxt = buf.find(b"\x1b")
                chunk = buf if nxt < 0 else buf[:nxt]
                buf = b"" if nxt < 0 else buf[nxt:]
                events.append({"kind": "paste",
                               "text": chunk.decode("utf-8", "replace")})
        self.inbuf = buf
        return events

    def _parse_csi(self, params, final):
        if final in ("M", "m") and params.startswith("<"):
            b, x, y = (int(v) for v in params[1:].split(";"))
            return {"kind": "mouse", "b": b, "x": x - 1, "y": y - 1,
                    "press": final == "M"}
        parts = params.split(";") if params else []
        if final == "~":
            if parts and parts[0] == "200":
                return {"paste_begin": True}
            num = int(parts[0].split(":")[0]) if parts else 0
            mods = int(parts[1].split(":")[0]) if len(parts) > 1 else 1
            if num in SPECIAL_TILDE:
                k, c, vk = SPECIAL_TILDE[num]
                return {"kind": "key", "key": k, "code": c, "vk": vk,
                        "mods": mods, "text": ""}
            return None
        if final in SPECIAL_CSI:
            mods = int(parts[1].split(":")[0]) if len(parts) > 1 else 1
            k, c, vk = SPECIAL_CSI[final]
            return {"kind": "key", "key": k, "code": c, "vk": vk,
                    "mods": mods, "text": ""}
        if final == "u":
            keyf = parts[0] if parts else "0"
            mods = int(parts[1].split(":")[0]) if len(parts) > 1 else 1
            nums = keyf.split(":")
            key = int(nums[0])
            shifted = int(nums[1]) if len(nums) > 1 and nums[1] else 0
            if key in SPECIAL_U:
                k, c, vk = SPECIAL_U[key]
                return {"kind": "key", "key": k, "code": c, "vk": vk,
                        "mods": mods, "text": "\r" if key == 13 else
                        ("\t" if key == 9 else "")}
            mm = max(0, mods - 1)
            ch = chr(shifted) if (mm & 1 and shifted) else chr(key)
            text = ch if not (mm & ~1) and key >= 32 else ""
            return {"kind": "key", "key": ch, "code": "", "vk": ord(ch.upper()[:1] or " "),
                    "mods": mods, "text": text}
        return None


def wcwidth(ch):
    """Terminal cell width; must agree with kitty or rows drift and the
    erase-diff misses. 0 = does not advance the cursor (skip drawing)."""
    o = ord(ch)
    if o < 32 or o == 127:
        return -1  # control char (\t, \n, …): sanitize before drawing
    if unicodedata.combining(ch) or unicodedata.category(ch) == "Cf":
        return 0   # combining marks, ZWSP & other format chars
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _rle(seg):
    """[' ',None,None,' '] -> [(True,1),(False,2),(True,1)]"""
    out, i = [], 0
    while i < len(seg):
        j = i
        while j < len(seg) and (seg[j] is None) == (seg[i] is None):
            j += 1
        out.append((seg[i] is not None, j - i))
        i = j
    return out


# ───────────────────────── the browser app ──────────────────────────────────

TRANSPARENT_CSS = (
    "*:not(input):not(textarea):not(select)"
    "{-webkit-text-fill-color:transparent !important;"
    "text-shadow:none !important}"
    "::selection{background:rgba(52,101,164,0.55)}"
)
INJECT_JS = ("(function(){var s=document.createElement('style');"
             f"s.textContent={json.dumps(TRANSPARENT_CSS)};"
             "(document.head||document.documentElement).appendChild(s);})()")

RGB_RE = re.compile(r"rgba?\((\d+),\s*(\d+),\s*(\d+)")


class Browse:
    def __init__(self, url):
        self.term = Term()
        self.url = url
        self.wid = os.environ.get("KITTY_WINDOW_ID", str(os.getpid()))
        self.seq = 0
        self.view_rows = self.term.rows - 1          # last row = status
        self.vw = int(self.view_rows * self.term.cell_h)
        self.vh_cols = self.term.cols
        self.page_w = int(self.term.cols * self.term.cell_w)
        self.page_h = int(self.view_rows * self.term.cell_h)
        profile = os.path.join(os.environ.get("XDG_STATE_HOME",
                               os.path.expanduser("~/.local/state")),
                               "kilix", "browse-profile")
        os.makedirs(profile, exist_ok=True)
        self.temp_profile = None
        # Chrome refuses to share a profile: if another browse instance
        # holds the SingletonLock, fall back to a disposable profile.
        lock = os.path.join(profile, "SingletonLock")
        if os.path.lexists(lock):
            try:
                holder_pid = int(os.readlink(lock).rsplit("-", 1)[1])
                alive = os.path.exists(f"/proc/{holder_pid}")
            except (OSError, ValueError, IndexError):
                alive = False
            if alive:
                profile = f"{profile}-{os.getpid()}"
                self.temp_profile = profile
        self.cdp = CDP(url, self.page_w, self.page_h, profile)
        self.sess = None
        self.runs = []               # cached glyph runs (doc coords)
        self.scroll_x = self.scroll_y = 0.0
        self.snap_dirty = True
        self.last_input = 0.0
        self.last_snap = 0.0
        self.frames = 0
        self.status_msg = "loading…"
        self.title = url
        self.url_edit = None         # None or [buffer string]
        self.mouse_buttons = 0
        self.last_click = (0.0, -9, -9, 0)   # t, x, y, count
        self.resized = False
        self.glyph_dirty = True
        self.prev_glyphs = None

    # ---- CDP session -------------------------------------------------------
    def start(self):
        tid = self.cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.sess = self.cdp.call("Target.attachToTarget",
                                  {"targetId": tid, "flatten": True})["sessionId"]
        s = self.sess
        self.cdp.call("Page.enable", session=s)
        self.cdp.call("Runtime.enable", session=s)
        self.cdp.call("DOMSnapshot.enable", session=s)
        self.cdp.call("Page.addScriptToEvaluateOnNewDocument",
                      {"source": INJECT_JS}, session=s)
        self.cdp.call("Emulation.setDeviceMetricsOverride",
                      {"width": self.page_w, "height": self.page_h,
                       "deviceScaleFactor": 1, "mobile": False}, session=s)
        self.cdp.send("Page.navigate", {"url": self.url}, session=s)
        self.cdp.call("Page.startScreencast",
                      {"format": "jpeg", "quality": 80,
                       "maxWidth": self.page_w, "maxHeight": self.page_h,
                       "everyNthFrame": 1}, session=s)

    # ---- pixel layer -------------------------------------------------------
    def blit(self, b64jpeg, meta):
        img = Image.open(BytesIO(base64.b64decode(b64jpeg)))
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        self.seq = (self.seq + 1) % 8
        name = f"tty-graphics-protocol-kilix-{self.wid}-{self.seq}.rgb"
        path = "/dev/shm/" + name
        with open(path, "wb") as f:
            f.write(img.tobytes())
        payload = base64.b64encode(path.encode()).decode()
        self.term.write(f"\x1b[H\x1b_Ga=T,i=1,p=1,z=-1,t=t,f=24,"
                        f"s={w},v={h},q=2,C=1;{payload}\x1b\\")
        sx, sy = meta.get("scrollOffsetX", 0), meta.get("scrollOffsetY", 0)
        if (sx, sy) != (self.scroll_x, self.scroll_y):
            self.scroll_x, self.scroll_y = sx, sy
            self.glyph_dirty = True
        self.frames += 1
        if self.frames == 1 or self.frames % 60 == 0:
            log(f"frames={self.frames} size={w}x{h} scroll={self.scroll_y}")

    # ---- glyph layer -------------------------------------------------------
    def snapshot(self):
        try:
            snap = self.cdp.call("DOMSnapshot.captureSnapshot",
                                 {"computedStyles": ["color", "font-weight",
                                  "font-style", "text-decoration-line"]},
                                 session=self.sess, timeout=5)
        except Exception as e:
            log("snapshot failed:", e)
            return
        strings = snap["strings"]
        runs = []
        for doc in snap["documents"]:
            lay = doc["layout"]
            texts = lay.get("text", [])
            styles = lay.get("styles", [])
            for i, ti in enumerate(texts):
                if ti < 0:
                    continue
                text = strings[ti]
                if not text.strip():
                    continue
                x, y, w, h = lay["bounds"][i]
                fg, bold, italic, under = (211, 215, 207), False, False, False
                if styles and i < len(styles) and styles[i]:
                    vals = [strings[si] if 0 <= si < len(strings) else ""
                            for si in styles[i]]
                    m = RGB_RE.match(vals[0]) if vals else None
                    if m:
                        fg = tuple(int(g) for g in m.groups())
                    if len(vals) > 1 and vals[1] and vals[1][0].isdigit():
                        bold = int(vals[1].split()[0]) >= 600
                    if len(vals) > 2:
                        italic = vals[2] == "italic"
                    if len(vals) > 3:
                        under = "underline" in vals[3]
                runs.append((x, y, w, h, text, fg, bold, italic, under))
        self.runs = runs
        self.last_snap = time.time()
        self.snap_dirty = False
        self.glyph_dirty = True
        log(f"snapshot: {len(runs)} runs")

    def render_glyphs(self):
        rows, cols = self.view_rows, self.term.cols
        cw, ch = self.term.cell_w, self.term.cell_h
        grid = [[None] * cols for _ in range(rows)]
        for x, y, w, h, text, fg, bold, italic, under in self.runs:
            sy = y - self.scroll_y
            sx = x - self.scroll_x
            row = int((sy + h / 2) // ch)
            if not (0 <= row < rows):
                continue
            col = int(round(sx / cw))
            attr = (fg, bold, italic, under)
            for chx in text:
                if col >= cols:
                    break
                w = wcwidth(chx)
                if w == -1:
                    chx, w = " ", 1   # control char -> plain space
                elif w == 0:
                    continue          # zero-width: never reaches the grid
                if col >= 0:
                    grid[row][col] = (chx, attr)
                    if w == 2 and col + 1 < cols:
                        grid[row][col + 1] = ("", attr)  # wide continuation
                col += w
        status = self.render_status()
        if grid == self.prev_glyphs and status == getattr(self, "prev_status", None):
            # identical content: never rewrite (a no-op repaint still clears
            # kitty's native selection)
            self.glyph_dirty = False
            return
        self.prev_status = status
        prev = self.prev_glyphs or [[None] * cols for _ in range(rows)]
        out = ["\x1b[?2026h"]
        for r in range(rows):
            out.append(f"\x1b[{r + 1};1H\x1b[0m")
            cur_attr, line = None, []
            col = 0
            while col < cols:
                cell = grid[r][col]
                if cell is None:
                    if cur_attr is not None:
                        line.append("\x1b[0m")
                        cur_attr = None
                    # blank cells that had a glyph last repaint; jump the rest
                    # (untouched cells keep showing the z=-1 image through)
                    seg = []
                    while col < cols and grid[r][col] is None:
                        seg.append(" " if prev[r][col] is not None else None)
                        col += 1
                    for erase, n in _rle(seg):
                        line.append(" " * n if erase else f"\x1b[{n}C")
                    continue
                chx, attr = cell
                if chx == "":  # orphaned wide-char continuation: blank it
                    line.append("\x1b[0m ")
                    cur_attr = None
                    col += 1
                    continue
                if col + wcwidth(chx) > cols:
                    # wide char can't fit in the last column: blank instead
                    line.append("\x1b[0m ")
                    cur_attr = None
                    col += 1
                    continue
                if attr != cur_attr:
                    fg, bold, italic, under = attr
                    sgr = f"\x1b[0;38;2;{fg[0]};{fg[1]};{fg[2]}"
                    sgr += ";1" if bold else ""
                    sgr += ";3" if italic else ""
                    sgr += ";4" if under else ""
                    line.append(sgr + "m")
                    cur_attr = attr
                line.append(chx)
                col += wcwidth(chx)
            out.append("".join(line))
        out.append(status)
        out.append("\x1b[?2026l")
        self.term.write("".join(out))
        self.prev_glyphs = grid
        self.glyph_dirty = False

    def render_status(self):
        cols = self.term.cols
        if self.url_edit is not None:
            body = f" URL: {self.url_edit}▏"
        else:
            body = f" {self.title[:40]} — {self.url}  [{self.status_msg}]"
        body = body[:cols].ljust(cols)
        return f"\x1b[{self.term.rows};1H\x1b[0;7m{body}\x1b[0m"

    # ---- input -------------------------------------------------------------
    def on_key(self, ev):
        # pure modifier presses (kitty functional keycodes 57441-57454, e.g.
        # the Shift of a shift+drag selection) must not dirty the page: the
        # resulting repaint would clear kitty's native selection
        if len(ev["key"]) == 1 and 57441 <= ord(ev["key"]) <= 57454:
            return
        self.last_input = time.time()
        self.snap_dirty = True
        mods = max(0, ev["mods"] - 1)
        ctrl, alt = bool(mods & 4), bool(mods & 2)
        key = ev["key"]
        if self.url_edit is not None:
            return self.url_edit_key(ev)
        if ctrl and key == "l":
            self.url_edit = ""
            self.glyph_dirty = True
            return
        if ctrl and key == "q":
            raise KeyboardInterrupt
        if ctrl and key == "r":
            self.cdp.send("Page.reload", {}, session=self.sess)
            self.status_msg = "reloading…"
            return
        if alt and key in ("ArrowLeft", "ArrowRight"):
            return self.history(-1 if key == "ArrowLeft" else +1)
        if ctrl and key == "c":
            self.copy_selection()   # and fall through: forward to page
        self.forward_key(ev)

    def forward_key(self, ev):
        p = {"modifiers": cdp_mods(ev["mods"]),
             "key": ev["key"], "code": ev["code"] or ev["key"],
             "windowsVirtualKeyCode": ev["vk"]}
        if ev["text"]:
            p["text"] = ev["text"]
        self.cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", **p},
                      session=self.sess)
        p.pop("text", None)
        self.cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", **p},
                      session=self.sess)

    def url_edit_key(self, ev):
        key = ev["key"]
        if key == "Enter":
            url = self.url_edit.strip()
            if url and "://" not in url:
                url = ("https://" + url) if "." in url and " " not in url \
                    else "https://duckduckgo.com/?q=" + url.replace(" ", "+")
            self.url_edit = None
            if url:
                self.url = url
                self.status_msg = "loading…"
                self.cdp.send("Page.navigate", {"url": url}, session=self.sess)
        elif key == "Escape":
            self.url_edit = None
        elif key == "Backspace":
            self.url_edit = self.url_edit[:-1]
        elif ev["text"]:
            self.url_edit += ev["text"]
        self.glyph_dirty = True

    def on_paste(self, text):
        self.last_input = time.time()
        self.snap_dirty = True
        if self.url_edit is not None:
            self.url_edit += text.replace("\n", "")
            self.glyph_dirty = True
        else:
            self.cdp.send("Input.insertText", {"text": text}, session=self.sess)

    def on_mouse(self, ev):
        self.last_input = time.time()
        b, x, y, press = ev["b"], ev["x"], ev["y"], ev["press"]
        log(f"mouse b={b} x={x} y={y} press={press}")
        if y >= self.page_h:      # status row: ignore
            return
        mods = ((8 if b & 4 else 0) | (1 if b & 8 else 0) | (2 if b & 16 else 0))
        if b & 64:  # wheel: 64 up, 65 down
            delta = -120 if (b & 3) == 0 else 120
            self.cdp.send("Input.dispatchMouseEvent",
                          {"type": "mouseWheel", "x": x, "y": y,
                           "deltaX": 0, "deltaY": delta, "modifiers": mods},
                          session=self.sess)
            self.snap_dirty = True
            return
        btn_code = b & 3
        button = ("left", "middle", "right", "none")[btn_code]
        if b & 32:  # motion
            # during a drag the moved events must carry the held button or
            # Blink's selection controller ignores the drag
            drag_btn = ("left" if self.mouse_buttons & 1 else
                        "right" if self.mouse_buttons & 2 else
                        "middle" if self.mouse_buttons & 4 else "none")
            self.cdp.send("Input.dispatchMouseEvent",
                          {"type": "mouseMoved", "x": x, "y": y,
                           "buttons": self.mouse_buttons, "modifiers": mods,
                           "button": drag_btn}, session=self.sess)
            return
        mask = {"left": 1, "right": 2, "middle": 4}.get(button, 0)
        if press:
            t, lx, ly, cc = self.last_click
            cc = cc + 1 if (time.time() - t < 0.4 and abs(x - lx) < 4
                            and abs(y - ly) < 4) else 1
            self.last_click = (time.time(), x, y, cc)
            self.mouse_buttons |= mask
            typ = "mousePressed"
        else:
            self.mouse_buttons &= ~mask
            typ = "mouseReleased"
        self.cdp.send("Input.dispatchMouseEvent",
                      {"type": typ, "x": x, "y": y, "button": button,
                       "buttons": self.mouse_buttons,
                       "clickCount": self.last_click[3], "modifiers": mods},
                      session=self.sess)
        self.snap_dirty = True

    # ---- commands ----------------------------------------------------------
    def history(self, step):
        try:
            h = self.cdp.call("Page.getNavigationHistory", session=self.sess)
            idx = h["currentIndex"] + step
            if 0 <= idx < len(h["entries"]):
                self.cdp.send("Page.navigateToHistoryEntry",
                              {"entryId": h["entries"][idx]["id"]},
                              session=self.sess)
                self.status_msg = "navigating…"
        except Exception as e:
            log("history:", e)

    def copy_selection(self):
        try:
            r = self.cdp.call("Runtime.evaluate",
                              {"expression": "window.getSelection().toString()",
                               "returnByValue": True}, session=self.sess,
                              timeout=3)
            text = r.get("result", {}).get("value") or ""
            if text:
                b64 = base64.b64encode(text.encode()).decode()
                self.term.write(f"\x1b]52;c;{b64}\x07")
                self.status_msg = f"copied {len(text)} chars"
                self.glyph_dirty = True
        except Exception as e:
            log("copy:", e)

    def refresh_title(self):
        try:
            r = self.cdp.call("Runtime.evaluate",
                              {"expression": "document.title",
                               "returnByValue": True}, session=self.sess,
                              timeout=3)
            self.title = r.get("result", {}).get("value") or self.url
            self.term.write(f"\x1b]2;{self.title[:60]}\x07")
        except Exception:
            pass

    # ---- resize ------------------------------------------------------------
    def do_resize(self):
        self.term.refresh_size()
        self.view_rows = self.term.rows - 1
        self.page_w = int(self.term.cols * self.term.cell_w)
        self.page_h = int(self.view_rows * self.term.cell_h)
        s = self.sess
        try:
            self.cdp.call("Emulation.setDeviceMetricsOverride",
                          {"width": self.page_w, "height": self.page_h,
                           "deviceScaleFactor": 1, "mobile": False}, session=s)
            self.cdp.send("Page.stopScreencast", {}, session=s)
            self.cdp.call("Page.startScreencast",
                          {"format": "jpeg", "quality": 80,
                           "maxWidth": self.page_w, "maxHeight": self.page_h,
                           "everyNthFrame": 1}, session=s)
        except Exception as e:
            log("resize:", e)
        self.prev_glyphs = None
        self.snap_dirty = self.glyph_dirty = True
        log(f"resize -> {self.page_w}x{self.page_h} "
            f"({self.term.cols}x{self.term.rows} cells)")

    # ---- event dispatch ----------------------------------------------------
    def on_cdp_event(self, m):
        meth, params = m.get("method"), m.get("params", {})
        if meth == "Page.screencastFrame":
            self.blit(params["data"], params.get("metadata", {}))
            # cap ~30fps: the ack is the throttle (CDP sends nothing until
            # acked). Only bites during animation; static pages are
            # damage-driven and idle at 0.
            dt = time.time() - getattr(self, "_last_frame_t", 0)
            if dt < 0.033:
                time.sleep(0.033 - dt)
            self._last_frame_t = time.time()
            self.cdp.send("Page.screencastFrameAck",
                          {"sessionId": params["sessionId"]}, session=self.sess)
        elif meth == "Page.loadEventFired":
            self.status_msg = "ready"
            self.snap_dirty = True
            self.cdp.send("Runtime.evaluate", {"expression": INJECT_JS},
                          session=self.sess)
            self.refresh_title()
        elif meth == "Page.frameNavigated":
            fr = params.get("frame", {})
            if not fr.get("parentId"):
                self.url = fr.get("url", self.url)
                self.glyph_dirty = True

    def run(self):
        signal.signal(signal.SIGWINCH, lambda *a: setattr(self, "resized", True))
        os.set_blocking(self.term.fd, False)
        err = None
        self.term.enter()
        try:
            self.start()
            # make the very first document transparent too (navigate raced
            # addScript for about:blank only; real doc gets it via injection)
            while True:
                r, _, _ = select.select([self.term.fd, self.cdp], [], [], 0.2)
                if self.cdp in r or self.cdp.buf:
                    for m in self.cdp.pump():
                        self.on_cdp_event(m)
                for m in self.cdp.events:
                    self.on_cdp_event(m)
                self.cdp.events.clear()
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
                now = time.time()
                if (self.snap_dirty and now - self.last_snap > 0.5
                        and now - self.last_input > 0.25):
                    self.snapshot()
                self.render_glyphs()
        except KeyboardInterrupt:
            pass
        except (EOFError, BrokenPipeError):
            err = "chrome exited unexpectedly"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        finally:
            # restore the terminal FIRST: anything printed while still in
            # the alt screen vanishes when the alt screen is left
            self.term.restore()
            self.cdp.close()
            for i in range(8):
                try:
                    os.unlink(f"/dev/shm/tty-graphics-protocol-kilix-"
                              f"{self.wid}-{i}.rgb")
                except OSError:
                    pass
            if self.temp_profile:
                import shutil
                shutil.rmtree(self.temp_profile, ignore_errors=True)
        if err:
            print(f"kilix browse: {err}", file=sys.stderr)
            sys.exit(1)


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    if "://" not in url:
        url = "https://" + url
    Browse(url).run()


if __name__ == "__main__":
    main()
