"""Regression tests for the menu/scrollbar/listbox fixes
(F03/F12/F24/F44/F45/F50/F59/F60). Each assertion fails on the pre-fix code."""
import harness as H
import apps
import wm
import widgets as W
import theme as T


# ── F03 — IconGrid (window) scrollbar hit region tracks the widget origin ─────
d = H.make_desk()
win = wm.Window(d, "Grid", 400, 320)
d.wm.add(win)
grid = win.add(W.IconGrid(2, 49, 300, 200))
grid.set_items([{"label": "f%d" % n, "icon": "doc", "data": n}
                for n in range(30)])
win.invalidate()
win.render()                                   # places the scrollbar (surface coords)
assert grid.sb.total > grid.sb.page            # scrollbar is live
# the DOWN arrow sits at the bottom of the visible bar; click it in CLIENT coords
sbx = grid.x + (grid.w - T.SCROLL_W - 2) + T.SCROLL_W // 2
sby = grid.y + 2 + (grid.h - 4) - T.SCROLL_W // 2
grid.on_mouse(H.ev("mouse", press=True, btn=1, x=sbx, y=sby))
assert grid.sb.pos == 1, ("F03: down arrow missed scrollbar", grid.sb.pos)
assert grid.band is None, "F03: scrollbar click started a rubber band"


# ── F50 — ListBox hit test rejects the top bevel and the partial bottom strip ─
lb = W.ListBox(0, 0, 200, 100,
               items=[(None, "row%d" % n, n) for n in range(10)])
lb.window = win                                # for invalidate()
rows = lb._rows()
# a real, drawn row selects
lb.on_mouse(H.ev("mouse", press=True, btn=1, x=10, y=lb.y + 2 + 2 * lb.RH + 1))
assert lb.sel == 2, ("ListBox real row not selected", lb.sel)
# the partial strip below the last drawn row must NOT select an off-screen item
lb.sel = -1
lb.on_mouse(H.ev("mouse", press=True, btn=1, x=10, y=lb.y + 2 + rows * lb.RH + 1))
assert lb.sel == -1, ("F50: partial bottom strip selected an item", lb.sel)
# the 2px top bevel (while scrolled) must not select the hidden row above
lb.sb.pos = 3
lb.sel = -1
lb.on_mouse(H.ev("mouse", press=True, btn=1, x=10, y=lb.y + 1))
assert lb.sel == -1, ("F50: top bevel selected the row above", lb.sel)


# ── F60 — Menu.item_at bounds x: the sidebar band / frame is dead ─────────────
d = H.make_desk()
fired = []
items = [W.MenuItem("A", action=lambda: fired.append("A")),
         W.MenuItem("B", action=lambda: fired.append("B"))]
m = d.menus.open(items, 100, 100, sidebar="kilix 95")
it, (x0, y0, x1, y1) = list(m.item_rects())[0]
cy = (y0 + y1) // 2
assert m.item_at(m.x + 4, cy) == -1, "F60: click in the sidebar band hit an item"
assert m.item_at((x0 + x1) // 2, cy) == 0, "item column must still hit"
d.menus.close_all()


# ── F59 — a submenu clamped at the right edge cascades LEFT of its parent ─────
d = H.make_desk(size=(300, 400))
sub = [W.MenuItem("s%d" % i) for i in range(3)]
m = d.menus.open([W.MenuItem("parent", submenu=sub)], 200, 50)
it, (x0, y0, x1, y1) = list(m.item_rects())[0]
H.move(d, (x0 + x1) // 2, (y0 + y1) // 2)      # hover opens the submenu
assert len(d.menus.stack) == 2, "submenu did not open"
smenu = d.menus.stack[-1]
assert smenu.x + smenu.w <= m.x + 4, ("F59: submenu overlaps its parent",
                                      smenu.x, smenu.w, m.x)
d.menus.close_all()


# ── F45 — a menu taller than the screen scrolls and stays keyboard-navigable ──
d = H.make_desk(size=(400, 300))
fired = []
items = [W.MenuItem("item%d" % i, action=lambda i=i: fired.append(i))
         for i in range(30)]
m = d.menus.open(items, 10, 10)
assert m.scrollable and m.h <= 300 - T.TASKBAR_H, "F45: tall menu not capped/scrolled"
# End reaches the last item and scrolls it into the visible band
d.dispatch_key(W.Ev(kind="key", key="End"))
assert m.hot == 29, ("F45: keyboard nav can't reach the tail", m.hot)
top, bot = m._content()
it, (x0, y0, x1, y1) = list(m.item_rects())[29]
assert top <= y0 and y1 < bot, "F45: End did not reveal the last item"
d.dispatch_key(W.Ev(kind="key", key="Enter"))
assert fired == [29], ("F45: could not activate the tail item", fired)
# wheel scrolls too
m = d.menus.open(items, 10, 10)
H.wheel(d, m.x + 20, m.y + m.ARROW + 5, dz=1)
assert m.first > 0, "F45: wheel did not scroll the menu"
d.menus.close_all()


# ── F12 — the release of the click that OPENS a flip-up menu selects nothing ──
d = H.make_desk()
apps.open(d, "notepad", None)
np = H.find_window(d, "Notepad")
np.y = 640                                     # so the File menu flips upward
np.invalidate()
gx, gy = np.client_origin()
spans = list(np.menubar._spans())
fx = spans[0][2]                               # "File" label x (client)
H.press(d, gx + fx + 3, gy + np.menubar.y + 8)
assert d.menus.active and np.menubar.menu_open == 0
m = d.menus.stack[-1]
close_c = next(((cx0 + cx1) // 2, (cy0 + cy1) // 2)
               for it, (cx0, cy0, cx1, cy1) in m.item_rects()
               if it.label == "Close")
H.release(d, *close_c)                         # release of the OPENING click
assert H.find_window(d, "Notepad") is not None, "F12: opening release fired Close"
assert d.menus.active, "F12: opening release closed the menu"
# a genuine click (hover, then press+release) still fires the item
nx = next(((cx0 + cx1) // 2, (cy0 + cy1) // 2)
          for it, (cx0, cy0, cx1, cy1) in m.item_rects() if it.label == "New")
H.move(d, *nx)
H.press(d, *nx)
H.release(d, *nx)
assert not d.menus.active, "genuine menu click must still select"


# ── F24 — MenuBar hover-switch keeps the open label highlighted ───────────────
d = H.make_desk()
apps.open(d, "notepad", None)
np = H.find_window(d, "Notepad")
gx, gy = np.client_origin()
spans = list(np.menubar._spans())
fx = spans[0][2]
H.press(d, gx + fx + 3, gy + np.menubar.y + 8)     # open File
H.release(d, gx + fx + 3, gy + np.menubar.y + 8)
assert np.menubar.menu_open == 0 and d.menus.active
ei = next(i for i, s in enumerate(spans) if s[1] == "Edit")
ex = spans[ei][2]
H.move(d, gx + ex + 3, gy + np.menubar.y + 8)      # slide onto Edit
assert d.menus.active, "F24: hover-switch closed the menu"
assert np.menubar.menu_open == ei, ("F24: hover-switch cleared the highlight",
                                    np.menubar.menu_open)
d.menus.close_all()


# ── F44 — a Dropdown popup that flips up over its field selects nothing on the
#          opening release ─────────────────────────────────────────────────────
d = H.make_desk()
win = wm.Window(d, "Drop", 240, 120)
d.wm.add(win)
win.x, win.y = 300, 560
win.surface = None
dd = win.add(W.Dropdown(20, 10, 160, [chr(97 + i) for i in range(12)]))
win.invalidate()
win.render()
gx, gy = win.client_origin()
px, py = gx + dd.x + 12, gy + dd.y + 10
H.press(d, px, py)                             # opens the (flipped) popup
assert d.menus.active, "dropdown popup did not open"
before = dd.index
H.release(d, px, py)                           # release of the opening click
assert dd.index == before, ("F44: opening release changed the value", dd.index)
assert d.menus.active, "F44: opening release closed the popup"


print("ok")
