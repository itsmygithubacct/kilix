"""Taskbar furnishings: system tray + volume flyout, clock tooltip/calendar,
quick launch (Show Desktop), and the empty-space context menu (Cascade)."""
import harness as H
import apps
import taskbar as TB


def _mid(rect):
    x0, y0, x1, y1 = rect
    return (x0 + x1) // 2, (y0 + y1) // 2


# ── system tray: speaker opens the volume flyout, persists a level ─────────
d = H.make_desk()
tb = d.taskbar
speaker = [t for t in tb._tray_icons() if t[0] == "speaker"][0]
sx = (speaker[2] + speaker[3]) // 2
sy = _mid(tb.rect())[1]
assert tb._popup is None
H.click(d, sx, sy)                                  # click the speaker
fly = tb._popup
assert fly is not None and type(fly).__name__ == "_VolFlyout"
assert fly in d.wm.windows and getattr(fly, "_no_taskbar", False)
assert all(w is not fly for w, _, _ in tb._buttons()), "flyout got a task button"

# drive the slider to the top → 100, then check it persisted to shell state
H.press(d, fly.x + 36, fly.y + 22)
H.release(d, fly.x + 36, fly.y + 22)
assert fly.slider.value == 100 and d.shell.state["volume"] == 100
H.click(d, fly.x + 12, fly.y + 122)                 # Mute checkbox
assert d.shell.state["muted"] is True
H.click(d, sx, sy)                                  # speaker again → closes
assert tb._popup is None and fly not in d.wm.windows

# ── clock: tooltip is a full date; double-click opens the calendar popup ───
import time
cr = tb._clock_rect()
cx, cy = _mid(cr)
tip = tb.tooltip_at(cx, cy)
assert isinstance(tip, str) and str(time.localtime().tm_year) in tip, tip
assert tb.tooltip_at(sx, sy).startswith("Volume")   # tray tooltip too
H.press(d, cx, cy, clicks=2)                         # double-click the clock
H.release(d, cx, cy)
assert tb._popup is not None and type(tb._popup).__name__ == "_ClockPopup"
tb._close_popup()

# ── quick launch: Show Desktop minimizes all, then restores ────────────────
d = H.make_desk()
tb = d.taskbar
apps.open(d, "notepad", None)
apps.open(d, "calc", None)
wins = [w for w in d.wm.windows if not w.modal]
for w in wins:
    w.x, w.y = 60, 60                                # keep them off the taskbar
sd = [b for b in tb._ql_buttons() if b[1] == "show_desktop"][0]
sdx, sdy = (sd[4] + sd[5]) // 2, _mid(tb.rect())[1]
H.click(d, sdx, sdy)
assert all(w.minimized for w in wins), "Show Desktop did not minimize all"
H.click(d, sdx, sdy)
assert not any(w.minimized for w in wins), "Show Desktop did not restore"

# ── context menu: right-click empty space; Cascade repositions windows ─────
for i, w in enumerate(wins):
    w.x, w.y, w.maximized = 200, 200, False
ex, ey = _mid(tb.rect())                             # mid-bar = empty space
H.press(d, ex, ey, btn=3)
assert d.menus.active, "right-click did not open the context menu"
labels = {it.label for it in d.menus.stack[-1].items}
assert {"Cascade Windows", "Tile Windows Horizontally",
        "Task Manager"} <= labels, labels
cascade = [it for it in d.menus.stack[-1].items
           if it.label == "Cascade Windows"][0]
d.menus.close_all()
order = tb._arrangeable()
cascade.action()
assert order[0].x != order[1].x and order[0].y != order[1].y, \
    "Cascade did not stagger the windows"
assert not any(w.maximized for w in order)

d.render()                                           # everything still paints
print("ok")
