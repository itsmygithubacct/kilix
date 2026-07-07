"""MenuItem.context — right-clicking a menu item opens its context list as a
stacked popup; left-click behaviour is unchanged."""
import harness as H
import widgets as W


def center(rect):
    _, (x0, y0, x1, y1) = rect
    return (x0 + x1) // 2, (y0 + y1) // 2


# ── right-click over an item with a context opens a second popup ──────────────
d = H.make_desk()
fired = []
ctx = [W.MenuItem("A", action=lambda: fired.append("A")),
       W.MenuItem("B", action=lambda: fired.append("B"))]
items = [W.MenuItem("has-ctx", action=lambda: fired.append("main"), context=ctx),
         W.MenuItem("plain", action=lambda: fired.append("plain"))]
m = d.menus.open(items, 100, 100)
rects = list(m.item_rects())
cx, cy = center(rects[0])
H.press(d, cx, cy, btn=3)                       # right-click the item
assert len(d.menus.stack) == 2, ("context popup did not open",
                                  len(d.menus.stack))
popup = d.menus.stack[-1]
assert [it.label for it in popup.items] == ["A", "B"], "wrong context items"
assert fired == [], ("right-click activated the main item", fired)
H.release(d, cx, cy, btn=3)                      # matching right-release: no-op
assert len(d.menus.stack) == 2 and fired == [], "right-release fired/closed"

# left-click 'B' in the context popup fires B and closes everything
bx, by = center(list(popup.item_rects())[1])
H.press(d, bx, by, btn=1)
H.release(d, bx, by, btn=1)
assert fired == ["B"], ("context left-click did not fire B", fired)
assert not d.menus.active, "context left-click did not close the popups"


# ── right-click over a context-less item is a no-op ──────────────────────────
d = H.make_desk()
fired = []
items = [W.MenuItem("has-ctx", context=[W.MenuItem("A")]),
         W.MenuItem("plain", action=lambda: fired.append("plain"))]
m = d.menus.open(items, 100, 100)
rects = list(m.item_rects())
px, py = center(rects[1])
H.press(d, px, py, btn=3)
H.release(d, px, py, btn=3)
assert len(d.menus.stack) == 1, ("right-click on a context-less item opened a "
                                 "popup", len(d.menus.stack))
assert fired == [], ("right-click on a context-less item activated it", fired)


# ── left-click still activates normally ──────────────────────────────────────
lx, ly = center(rects[1])
H.press(d, lx, ly, btn=1)
H.release(d, lx, ly, btn=1)
assert fired == ["plain"], ("left-click no longer activates", fired)
assert not d.menus.active, "left-click did not close the menu"


print("ok")
