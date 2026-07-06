"""Regression tests for TextField/TextArea fixes (F02/F04/F05/F09/F10/F21/
F22/F23/F28/F41). Each assertion fails on the pre-fix code path."""
import time

import harness as H
import apps
import wm
import widgets as W
import theme as T


def _client_crop(win, wdg):
    """Render win and return the wdg's rectangle from the window surface."""
    win.invalidate()
    win.render()
    ox, oy = T.BORDER, T.BORDER + T.TITLE_H
    return win.surface.crop((ox + wdg.x, oy + wdg.y,
                             ox + wdg.x + wdg.w, oy + wdg.y + wdg.h)).convert("RGB")


def _count(img, color):
    return sum(1 for p in img.getdata() if p == color)


# ── F02 / F09 — buttonless hover must not move caret or selection ────────────
d = H.make_desk()
apps.open(d, "notepad", None)
np = H.find_window(d, "Notepad")
ta = np.ta
ta.set_text("line one\nline two\nline three")
ta.cr, ta.cc = 0, 0

# hover (btn=0) over row 3 through the widget directly
ta.on_mouse(H.ev("mouse", move=True, btn=0, x=ta.x + 40, y=ta.y + 3 + 2 * ta.LH))
assert (ta.cr, ta.cc) == (0, 0), ("F09: hover moved TextArea caret", ta.cr, ta.cc)

# a genuine left-drag (btn bit set) still moves the caret
ta.on_mouse(H.ev("mouse", move=True, btn=1, x=ta.x + 40, y=ta.y + 3 + 2 * ta.LH))
assert ta.cr == 2, ("drag motion must still move caret", ta.cr, ta.cc)

# TextField: pre-selected field, a hover must not disturb cur/anchor
tf = W.TextField(50, 40, 120, "abcdef")
tf.anchor, tf.cur = 0, 6
tf.on_mouse(H.ev("mouse", move=True, btn=0, x=52, y=48))
assert (tf.anchor, tf.cur) == (0, 6), ("F02: hover rewrote TextField sel",
                                       tf.anchor, tf.cur)

# integration: hover routed through the desk leaves the caret put
ta.set_text("aaa\nbbb\nccc")
ta.cr, ta.cc = 0, 0
gx, gy = np.client_origin()
H.move(d, gx + ta.x + 20, gy + ta.y + 3 + 2 * ta.LH, btn=0)
assert (ta.cr, ta.cc) == (0, 0), ("F09 integration hover moved caret", ta.cr, ta.cc)


# ── F04 — Tab in a TextField cycles focus instead of typing \t ───────────────
box = wm.inputbox(d, "Save As", "Path:", "~/x.txt", cb=lambda t: None)
fld = next(w for w in box.widgets if isinstance(w, W.TextField))
assert (fld.anchor, fld.cur) == (0, len("~/x.txt"))     # inputbox pre-selects all
handled = fld.on_key(H.ev("key", key="Tab", text="\t"))
assert handled is False, "F04: TextField consumed Tab"
assert fld.text == "~/x.txt", ("F04: Tab wiped the field", repr(fld.text))
box.close()


# ── F05 — a single very long line must render fast (no O(n^2) truncation) ─────
ta.set_text("x" * 30000)
ta.cr, ta.cc = 0, 30000
ta._reveal()
np.caret_on = True
np.invalidate()
t0 = time.time()
np.render()
dt = time.time() - t0
assert dt < 1.0, ("F05: long line took %.2fs to render" % dt)


# ── F10 — horizontal scroll reveals editing past the right edge ──────────────
ta.set_text("x" * 500)
ta.cr, ta.cc = 0, 500
ta._reveal()
before = _client_crop(np, ta)
np.on_key(H.ev("key", key="Z", text="Z"))          # type at end of a long line
after = _client_crop(np, ta)
assert before.tobytes() != after.tobytes(), "F10: typing at EOL changed no pixels"
# caret must sit inside the visible box, not pinned off-screen
assert ta.hx > 0, "F10: no horizontal scroll offset established"

# End on a long line scrolls it into view
ta.set_text("y" * 400)
ta.cr, ta.cc = 0, 0
ta._reveal()
assert ta.hx == 0
np.on_key(H.ev("key", key="End"))
assert ta.hx > 0, "F10: End did not scroll a long line into view"


# ── F41 — selection past the fold is actually highlighted ────────────────────
ta.set_text("x" * 500)
ta.cr, ta.cc = 0, 80
np.on_key(H.ev("key", key="End", shift=True))       # select cols 80..500
img = _client_crop(np, ta)
assert _count(img, T.SEL_BG) > 100, "F41: off-screen selection drew no highlight"


# ── F22 / F28 — a no-op Backspace/Delete must not mark the doc modified ──────
d2 = H.make_desk()
apps.open(d2, "notepad", None)
np2 = H.find_window(d2, "Notepad")
np2.ta.set_text("hello")
np2.modified = False
np2.ta.cr, np2.ta.cc = 0, 0                          # start of doc
np2.on_key(H.ev("key", key="Backspace"))
assert np2.modified is False, "F28: no-op Backspace marked modified"
np2.ta.cr, np2.ta.cc = 0, len("hello")              # end of only line
np2.on_key(H.ev("key", key="Delete"))
assert np2.modified is False, "F22: no-op Delete marked modified"
# a real deletion still marks modified and fires on_change
np2.on_key(H.ev("key", key="Backspace"))
assert np2.modified is True and np2.ta.text() == "hell", "real Backspace lost"


# ── F23 — ArrowUp/Down follow the goal column, reset by a mouse click ────────
ta = np.ta
ta.set_text("a" * 24 + "\n" + "bb" + "\n" + "c" * 24)
ta.cr, ta.cc = 0, 0
ta.goal_col = 0
np.on_key(H.ev("key", key="End"))                   # goal_col -> 24
assert ta.goal_col == 24
# click at column 2 on row 0 must reset the goal column
px = ta.x + 4 + T.text_w(ta.font, "aa")
ta.on_mouse(H.ev("mouse", press=True, btn=1, x=px, y=ta.y + 3))
assert (ta.cr, ta.cc) == (0, 2), ("mouse col", ta.cr, ta.cc)
np.on_key(H.ev("key", key="ArrowDown"))
np.on_key(H.ev("key", key="ArrowDown"))
assert (ta.cr, ta.cc) == (2, 2), ("F23: stale goal_col snapped caret",
                                  ta.cr, ta.cc)


# ── F21 — TextField text must not bleed left of its box when scrolled ────────
win = wm.Window(d, "T", 300, 120)
d.wm.add(win)
fld2 = win.add(W.TextField(50, 40, 120))
fld2.set("A" * 40)
win.set_focus(fld2)
fld2.on_key(H.ev("key", key="End"))
assert fld2.scroll > 0
win.invalidate()
win.render()
ox, oy = T.BORDER, T.BORDER + T.TITLE_H
row = oy + 40 + 8
bled = [x for x in range(ox + 8, ox + 48)
        if win.surface.getpixel((x, row)) == T.TEXT]
assert not bled, ("F21: scrolled TextField bled text left of the box", bled)

print("ok")
