"""wm.py regression tests: modality, dialog sizing, maximize drag, caret,
cascade wrap. Each assertion fails on the pre-fix code path."""
import harness as H
import apps
import theme as T
import widgets as W
import wm


def sysmenu_items(d, win):
    win._system_menu()
    out = {it.label: it for it in d.menus.stack[-1].items if it.label != "-"}
    d.menus.close_all()
    return out


# ── F18: modal dialogs must not offer Minimize (minimizing one loses it
# forever — no taskbar button, modal_top skips minimized) ────────────────────
d = H.make_desk()
dlg = wm.msgbox(d, "T", "hi")
assert dlg.modal
assert sysmenu_items(d, dlg)["Minimize"].enabled is False
dlg.close()
apps.open(d, "notepad", None)
np = H.find_window(d, "Notepad")
assert sysmenu_items(d, np)["Minimize"].enabled is True   # normal windows keep it


# ── F19: a 3-button msgbox must be wide enough that no button is clipped by
# the frame (Yes/No/Cancel = 232px vs the old 205px client) ──────────────────
d = H.make_desk()
box = wm.msgbox(d, "Notepad", "The text has changed.\nSave the changes?",
                buttons=("Yes", "No", "Cancel"))
cw = box.client_size()[0]
btns = [b for b in box.widgets if isinstance(b, W.Button)]
assert len(btns) == 3
for b in btns:
    assert b.x >= 0 and b.x + b.w <= cw, (b.text, b.x, b.w, cw)


# ── F20: a maximized window must not drag-move (leaves a 'maximized' window
# floating off 0,0) ──────────────────────────────────────────────────────────
d = H.make_desk()
apps.open(d, "notepad", None)
np = H.find_window(d, "Notepad")
d.wm.toggle_maximize(np)
assert np.maximized and (np.x, np.y) == (0, 0)
gy = np.y + T.BORDER + 2
H.press(d, np.x + 200, gy)
H.move(d, np.x + 350, gy + 120, btn=1)
H.release(d, np.x + 350, gy + 120)
assert (np.x, np.y) == (0, 0), (np.x, np.y)
assert np.maximized


# ── F31/F40: an error dialog raised from an app constructor must surface
# above (and dismiss) the app window it belongs to ──────────────────────────
d = H.make_desk()
apps.open(d, "notepad", "/no/such/kilix-test-file.txt")
wins = d.wm.windows
assert len(wins) == 2, [w.title for w in wins]
top = wins[-1]
assert top.modal, "modal error dialog must be topmost after construction"
assert d.wm.active is top and d.wm.modal_top() is top
H.key(d, "Escape")                       # active modal takes the Escape
assert top not in d.wm.windows
np = H.find_window(d, "Notepad")
assert np is not None and d.wm.active is np


# ── F48: only the active window blinks a caret; a deactivated window must not
# leave a frozen solid caret in its focused text widget ─────────────────────
d = H.make_desk()
apps.open(d, "notepad", None)
np1 = d.wm.active
apps.open(d, "notepad", None)
np2 = d.wm.active
assert np1 is not np2
assert np1.caret_on is False and np2.caret_on is True
d.wm.activate(np1)
assert np1.caret_on is True and np2.caret_on is False


# ── F49: the default cascade must wrap/clamp so windows never spawn off the
# right edge or behind the taskbar ──────────────────────────────────────────
d = H.make_desk(size=(1024, 768))
sw, sh = d.size()
for _ in range(30):
    apps.open(d, "notepad", None)
for w in d.wm.windows:
    assert 0 <= w.x and w.x + w.w <= sw, (w.x, w.w)
    assert 0 <= w.y and w.y + w.h <= sh - T.TASKBAR_H, (w.y, w.h)
