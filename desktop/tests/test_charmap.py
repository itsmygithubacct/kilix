"""Character Map: clicking a cell feeds the copy field; Copy sets clipboard."""
import harness as H
from apps.charmap import CharMap, COLS, CW, CH


def _cell_center(win, cp):
    i = cp - win.grid.base
    col, row = i % COLS, i // COLS
    gx, gy = win.client_origin()
    x = gx + win.grid.x + 2 + col * CW + CW // 2
    y = gy + win.grid.y + 2 + row * CH + CH // 2
    return x, y


d = H.make_desk()
win = CharMap(d)
d.wm.add(win)

# clicking the 'A' cell drops 'A' into the copy field
H.click(d, *_cell_center(win, ord("A")))
assert win.copy.text == "A", win.copy.text

# a second cell appends
H.click(d, *_cell_center(win, ord("B")))
assert win.copy.text == "AB", win.copy.text

# arrow-key navigation + Enter also picks
d.wm.activate(win)
win.set_focus(win.grid)
win.grid.sel = 0
H.key(d, "ArrowRight")
assert win.grid.sel == 1
H.key(d, "Enter")
assert win.copy.text == "AB" + chr(win.grid.base + 1), win.copy.text

# Copy puts the field on the desktop clipboard
gx, gy = win.client_origin()
cw, ch = win.client_size()
H.click(d, gx + win.b_copy.x + 20, gy + win.b_copy.y + 12)
assert d.clipboard == win.copy.text, (d.clipboard, win.copy.text)

# the window renders without error (status line uses unicodedata.name)
d.dirty = True
d.render()

print("ok")
