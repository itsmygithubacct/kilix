"""Calculator: arithmetic, divide-by-zero, and the keyboard path."""
import harness as H
import widgets as W
from apps.calc import Calc


def _btn(win, label):
    return next(w for w in win.widgets
               if isinstance(w, W.Button) and w.text == label)


def _click(desk, win, label):
    b = _btn(win, label)
    gx, gy = win.client_origin()
    H.click(desk, gx + b.x + 2, gy + b.y + 2)


# ── click path: 7 * 6 = 42 ──────────────────────────────────────────────────
d = H.make_desk()
win = Calc(d)
d.wm.add(win)
for lab in ("7", "*", "6", "="):
    _click(d, win, lab)
assert win.entry == "42", win.entry

# ── click path: 1 / 0 = error string ────────────────────────────────────────
_click(d, win, "C")
for lab in ("1", "/", "0", "="):
    _click(d, win, lab)
assert win.error == "Cannot divide by zero", (win.error, win.entry)

# CE clears the error and lets arithmetic resume
_click(d, win, "CE")
assert win.error is None

# ── keyboard path: 12 + 8 Enter -> 20 ───────────────────────────────────────
_click(d, win, "C")
d.wm.activate(win)
for ch in "12+8":
    H.key(d, ch)
H.key(d, "Enter")
assert win.entry == "20", win.entry

# Escape is Clear
H.key(d, "Escape")
assert win.entry == "0" and win.op is None

# ── functions: sqrt, 1/x, memory ────────────────────────────────────────────
for lab in ("9", "√"):
    _click(d, win, lab)
assert win.entry == "3", win.entry
_click(d, win, "MS")
assert win.mem == 3.0
_click(d, win, "C")
_click(d, win, "MR")
assert win.entry == "3"
for lab in ("0", "1/x"):        # 1/0 via reciprocal -> error
    _click(d, win, lab)
assert win.error == "Cannot divide by zero", win.error

# render once to exercise draw_client (big display + memory box)
d.dirty = True
d.render()

print("ok")
