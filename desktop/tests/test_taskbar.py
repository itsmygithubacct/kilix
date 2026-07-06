"""Taskbar: modality (F32), button overflow (F54), right-click menu (F58)."""
import harness as H
import apps
import taskbar as TB
import theme as T

# ── F32: task buttons / Start must not bypass an open modal ────────────────
d = H.make_desk()
apps.open(d, "notepad", None)
np = H.find_window(d, "Notepad")
np.x, np.y = 100, 100
d.shell.shutdown_dialog()
modal = d.wm.modal_top()
assert modal is not None and d.wm.active is modal
(win, bx0, bx1), = d.taskbar._buttons()       # modal has no button
assert win is np
x0, y0, x1, y1 = d.taskbar.rect()
bmid = ((bx0 + bx1) // 2, y0 + 10)

H.click(d, *bmid)                             # task-button click
assert d.wm.active is modal, "task button raised a window over the modal"
assert d.wm.windows[-1] is modal
H.click(d, *bmid)                             # second click must not minimize
assert not np.minimized and d.wm.active is modal
H.press(d, *bmid, btn=3)                      # right-click blocked too
H.release(d, *bmid, btn=3)
assert not d.menus.active and d.wm.active is modal

H.click(d, x0 + 12, y0 + 10)                  # Start while modal is up
assert not d.menus.active, "Start menu opened over a modal"
assert d.wm.active is modal
modal.close()
H.click(d, x0 + 12, y0 + 10)                  # ... and works again after
assert d.menus.active
d.menus.close_all()

# ── F54: many windows shrink the buttons instead of running off-screen ─────
d = H.make_desk()
for _ in range(20):
    apps.open(d, "notepad", None)
    w = d.wm.windows[-1]
    w.x, w.y = 50, 50                         # keep windows off the taskbar
btns = d.taskbar._buttons()
x0, y0, x1, y1 = d.taskbar.rect()
lim = x1 - TB.CLOCK_W - 8
assert len(btns) == 20, "some windows lost their task button"
assert all(b1 <= lim for _, _, b1 in btns), \
    "task buttons run under the clock: %r" % (btns[-1],)
active = d.wm.active
H.click(d, x1 - 40, y0 + 10)                  # click inside the clock well
assert d.wm.active is active and not any(w.minimized for w in d.wm.windows), \
    "clock click activated/minimized a hidden task button's window"
d.render()                                    # shrunken buttons still draw

# ── F58: taskbar right-click menu anchors at the button, restores minimized ─
d = H.make_desk()
apps.open(d, "notepad", None)
np = H.find_window(d, "Notepad")
np.x, np.y = 200, 150
d.wm.minimize(np)
(win, bx0, bx1), = d.taskbar._buttons()
x0, y0, x1, y1 = d.taskbar.rect()
H.press(d, (bx0 + bx1) // 2, y0 + 10, btn=3)
assert d.menus.active
m = d.menus.stack[-1]
assert m.x == bx0, "menu at (%d,%d), not at the button x=%d" % (m.x, m.y, bx0)
assert m.y + m.h <= y0, "menu not above the taskbar"
items = {it.label: it for it in m.items}
assert items["Restore"].enabled, "Restore disabled for a minimized window"
assert not items["Minimize"].enabled
items["Restore"].action()
assert not np.minimized and d.wm.active is np
d.menus.close_all()
# maximized (not minimized): Restore un-maximizes
d.wm.toggle_maximize(np)
(win, bx0, bx1), = d.taskbar._buttons()
H.press(d, (bx0 + bx1) // 2, y0 + 10, btn=3)
items = {it.label: it for it in d.menus.stack[-1].items}
assert items["Restore"].enabled and not items["Maximize"].enabled
items["Restore"].action()
assert not np.maximized
d.menus.close_all()

print("ok")
