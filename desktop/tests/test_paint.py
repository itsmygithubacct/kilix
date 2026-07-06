"""Paint: pencil strokes pixels, the bucket floods, swatches set the color."""
import os
import tempfile

from PIL import Image

import harness as H
import widgets as W
import filedialog
from apps import paint as P


def _nonwhite(img):
    return sum(1 for px in img.getdata() if px != (255, 255, 255))


def _drive_picker(desk, path):
    """Type path into the topmost FileDialog's name field and confirm it."""
    dlg = desk.wm.windows[-1]
    assert isinstance(dlg, filedialog.FileDialog), type(dlg)
    dlg._nav(os.path.dirname(path))
    dlg.name.set(os.path.basename(path))
    dlg._confirm()


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

# ── dirty flag: an edit stars the title, a save clears it ────────────────────
assert win.modified and win.title.startswith("*"), win.title

tmp = tempfile.mkdtemp(prefix="paint-test-")

# ── Save As: writes a file that reopens with matching size/pixels ───────────
spath = os.path.join(tmp, "art.png")
win._save_as()
_drive_picker(d, spath)
assert os.path.exists(spath), "Save As wrote nothing"
assert win.path == spath and not win.modified, "save did not clear dirty"
assert win.title == "art.png - Paint", win.title
reop = Image.open(spath)
assert reop.size == cv.img.size, (reop.size, cv.img.size)
assert list(reop.convert("RGB").getdata()) == list(cv.img.getdata()), \
    "reopened pixels differ"

# ── Save (no path) reuses the remembered path ───────────────────────────────
win.canvas.clear()
assert win.modified
win._save()
assert not win.modified and win.path == spath

# ── Open: the canvas adopts a temp image of a different size ────────────────
opath = os.path.join(tmp, "in.png")
src = Image.new("RGB", (37, 29), (0, 128, 0))
src.putpixel((5, 5), (255, 0, 0))
src.save(opath)
win._open()                            # not modified now → opens straight away
_drive_picker(d, opath)
assert cv.img.size == (37, 29), cv.img.size
assert cv.img.getpixel((5, 5)) == (255, 0, 0)
assert win.path == opath and not win.modified

# ── missing file in the picker: rejected in-dialog, canvas untouched ────────
before = list(cv.img.getdata())
win._open()
dlg = d.wm.windows[-1]
assert isinstance(dlg, filedialog.FileDialog)
dlg.name.set("does-not-exist.png")
dlg._confirm()                         # warn box, picker stays open
assert dlg in d.wm.windows and d.wm.modal_top() is not dlg
d.wm.modal_top().close()               # dismiss the warning
dlg._cancel()                          # close the picker
d.render()

# ── corrupt image: the picker accepts it, Paint reports the load error ──────
bad = os.path.join(tmp, "broken.png")
with open(bad, "wb") as f:
    f.write(b"not an image")
win._open()
_drive_picker(d, bad)
assert list(cv.img.getdata()) == before, "canvas changed on failed open"
assert any(w.title == "Paint" for w in d.wm.windows), "no error box shown"
d.render()                             # error box composes without hanging

print("ok")
