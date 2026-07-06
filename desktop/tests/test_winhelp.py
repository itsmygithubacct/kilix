"""Help viewer: selecting a topic swaps the content pane; Back returns."""
import harness as H
from apps.winhelp import Help, BOOK


def _row_y(win, i):
    gx, gy = win.client_origin()
    return gx, gy + win.topics.y + 2 + i * win.topics.RH + 3


d = H.make_desk()
win = Help(d)
d.wm.add(win)

# opens on the first topic
assert win.topic == BOOK[0][0], win.topic
first_text = win.body.plain()
assert "Welcome to kilix 95" in first_text

# select "Keyboard shortcuts" in the list -> content pane changes
kb = next(i for i, (k, *_ ) in enumerate(BOOK) if k == "keys")
gx, ry = _row_y(win, kb)
H.click(d, gx + 20, ry)
assert win.topic == "keys", win.topic
kb_text = win.body.plain()
assert kb_text != first_text
assert "Alt+F4" in kb_text and "Ctrl+Alt+Q" in kb_text
assert win.b_back.enabled and not win.b_fwd.enabled

# Back returns to the first topic; Forward re-enabled
gx, gy = win.client_origin()
H.click(d, gx + win.b_back.x + 4, gy + win.b_back.y + 4)
assert win.topic == BOOK[0][0], win.topic
assert win.body.plain() == first_text
assert win.b_fwd.enabled

# Forward walks the visited trail again
H.click(d, gx + win.b_fwd.x + 4, gy + win.b_fwd.y + 4)
assert win.topic == "keys", win.topic

# the pane scrolls without error
H.wheel(d, gx + win.body.x + 20, gy + win.body.y + 20, dz=1)
d.dirty = True
d.render()

print("ok")
