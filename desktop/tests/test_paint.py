"""Paint: pencil strokes pixels, the bucket floods, swatches set the color."""
import harness as H
from apps import paint as P


def _nonwhite(img):
    return sum(1 for px in img.getdata() if px != (255, 255, 255))


d = H.make_desk()
win = P.Paint(d)
d.wm.add(win)
cv = win.canvas
gx, gy = win.client_origin()
vx = gx + cv.x + 2                     # global top-left of the drawable sheet
vy = gy + cv.y + 2

# ── pencil: a drag lays down foreground-colored pixels ──────────────────────
assert win.tool == "pencil"
assert _nonwhite(cv.img) == 0
H.drag(d, vx + 10, vy + 10, vx + 60, vy + 40)
assert _nonwhite(cv.img) > 0, "pencil left no marks"
assert cv.img.getpixel((10, 10)) == win.fg

# ── color select: clicking a red swatch changes the active foreground ───────
red = P.COLORS.index((255, 0, 0))
sx, sy, sw, sh = win.palette.cell_rect(red)
H.click(d, gx + sx + sw // 2, gy + sy + sh // 2)
assert win.fg == (255, 0, 0), ("swatch did not set fg", win.fg)

# right-click sets the background color
navy = P.COLORS.index((0, 0, 128))
sx, sy, sw, sh = win.palette.cell_rect(navy)
H.click(d, gx + sx + sw // 2, gy + sy + sh // 2, btn=3)
assert win.bg == (0, 0, 128), ("right-click did not set bg", win.bg)

# ── fill bucket: floods a fresh sheet with the foreground color ─────────────
cv.new_image()
assert _nonwhite(cv.img) == 0
win.set_tool("fill")
H.click(d, vx + 30, vy + 30)
w, h = cv.img.size
assert _nonwhite(cv.img) == w * h, "bucket did not flood the whole sheet"
assert cv.img.getpixel((30, 30)) == (255, 0, 0)

# ── shape tool: a rectangle is only committed on release ────────────────────
cv.new_image()
win.set_tool("rect")
H.press(d, vx + 20, vy + 20)
H.move(d, vx + 80, vy + 60, btn=1)
assert _nonwhite(cv.img) == 0, "rect committed before release"
assert cv.preview is not None
H.release(d, vx + 80, vy + 60)
assert cv.preview is None
assert _nonwhite(cv.img) > 0, "rect not committed on release"

print("ok")
