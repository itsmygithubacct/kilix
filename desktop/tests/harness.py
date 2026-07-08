"""Offscreen test harness for the kilix 95 desktop.

Plain helpers, no framework. Tests are plain-assert scripts run by run.py,
one subprocess each: build a Desk with term=None, feed it synthetic
widgets.Ev the way Desk.run would, or drive kilix_sdk.term.Term.read_input over
a pipe fd with raw terminal bytes.
"""
import atexit
import contextlib
import os
import shutil
import sys
import tempfile

_here = os.path.dirname(os.path.abspath(__file__))
_desktop = os.path.dirname(_here)
KILIX_HOME = os.environ.get("KILIX_HOME") or os.path.dirname(_desktop)
sys.path.insert(0, os.path.join(KILIX_HOME, "config"))
sys.path.insert(0, _desktop)

from kilix_sdk import term as kilix_term
import main as desk_main   # import patches host F-key tables, like the live desk
import widgets as W

_dirs = []                 # temp desktop dirs owned by this process
atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in _dirs])


@contextlib.contextmanager
def desktop_dir():
    """Fresh temp dir exported as KILIX_DESKTOP_DIR for the with-block."""
    prev = os.environ.get("KILIX_DESKTOP_DIR")
    d = tempfile.mkdtemp(prefix="kilix95-test-")
    _dirs.append(d)
    os.environ["KILIX_DESKTOP_DIR"] = d
    try:
        yield d
    finally:
        if prev is None:
            os.environ.pop("KILIX_DESKTOP_DIR", None)
        else:
            os.environ["KILIX_DESKTOP_DIR"] = prev


def make_desk(size=(1024, 768)):
    """Offscreen Desk; KILIX_DESKTOP_DIR always points at a harness temp dir
    (a fresh one unless a desktop_dir() block is active)."""
    if os.environ.get("KILIX_DESKTOP_DIR") not in _dirs:
        d = tempfile.mkdtemp(prefix="kilix95-test-")
        _dirs.append(d)
        os.environ["KILIX_DESKTOP_DIR"] = d
    return desk_main.Desk(term=None, size=size)


def find_window(desk, cls_name):
    """Topmost window whose type name is cls_name ('Notepad'…), or None."""
    for win in reversed(desk.wm.windows):
        if type(win).__name__ == cls_name:
            return win
    return None


# ── synthetic events (global pixel coords, dispatched as Desk.run does) ─────

def ev(kind, **kw):
    """Bare widgets.Ev (kind='mouse'|'key', any Ev fields as keywords)."""
    return W.Ev(kind=kind, **kw)


def press(desk, x, y, btn=1, clicks=1, **mods):
    """Button press; pass clicks=2 for a double-click (no Desk timer here)."""
    desk.dispatch_mouse(W.Ev(kind="mouse", x=x, y=y, btn=btn, press=True,
                             clicks=clicks, **mods))


def release(desk, x, y, btn=1, **mods):
    """Button release (negative-space event: no press/move/wheel)."""
    desk.dispatch_mouse(W.Ev(kind="mouse", x=x, y=y, btn=btn, press=False,
                             **mods))


def click(desk, x, y, btn=1, clicks=1, **mods):
    """Press then release — menu items, buttons etc. fire on the release."""
    press(desk, x, y, btn=btn, clicks=clicks, **mods)
    release(desk, x, y, btn=btn, **mods)


def move(desk, x, y, btn=0, **mods):
    """Motion; btn is the held-button BITMASK (0 = hover, 1 = left drag)."""
    desk.dispatch_mouse(W.Ev(kind="mouse", x=x, y=y, move=True, btn=btn,
                             **mods))


def drag(desk, x0, y0, x1, y1, btn=1, steps=3):
    """Press at (x0,y0), move in steps with the button-held mask, release."""
    press(desk, x0, y0, btn=btn)
    for i in range(1, steps + 1):
        move(desk, x0 + (x1 - x0) * i // steps,
             y0 + (y1 - y0) * i // steps, btn=1 << (btn - 1))
    release(desk, x1, y1, btn=btn)


def wheel(desk, x, y, dz=1, **mods):
    """Wheel at (x, y): dz=-1 up, +1 down."""
    desk.dispatch_mouse(W.Ev(kind="mouse", x=x, y=y, wheel=dz, **mods))


def key(desk, k, text=None, **mods):
    """Key event; text defaults to k for single printables without ctrl/alt
    ('Enter'/'Escape'/… get text='')."""
    if text is None:
        text = k if (len(k) == 1 and not mods.get("ctrl")
                     and not mods.get("alt")) else ""
    desk.dispatch_key(W.Ev(kind="key", key=k, text=text, **mods))


def type_text(desk, s):
    """Type a string, one key event per char ('\\n' becomes Enter)."""
    for ch in s:
        if ch == "\n":
            key(desk, "Enter", text="\r")
        else:
            key(desk, ch)


# ── raw byte-stream parsing (kilix_sdk.term.Term over a pipe fd) ────────────

def make_term():
    """(term, write_fd): a kilix_sdk.term.Term reading a non-blocking pipe.
    os.write(write_fd, ...) then term.read_input(); term.inbuf shows what
    stalled. Caller closes both fds (or lets the test process exit)."""
    t = object.__new__(kilix_term.Term)
    r, w = os.pipe()
    os.set_blocking(r, False)
    t.fd = r
    t.inbuf = b""
    return t, w


def term_feed(data, chunks=None):
    """Feed raw terminal bytes through kilix_sdk.term.Term.read_input and return the
    parsed event dicts. chunks = byte offsets to split the writes at, with
    read_input called after every chunk (adversarial framing;
    chunks=range(len(data)) is byte-by-byte)."""
    t, w = make_term()
    try:
        offs = sorted(set([0, len(data)] + list(chunks or [])))
        evs = []
        for a, b in zip(offs, offs[1:]):
            os.write(w, data[a:b])
            evs += t.read_input()
        return evs
    finally:
        os.close(t.fd)
        os.close(w)
