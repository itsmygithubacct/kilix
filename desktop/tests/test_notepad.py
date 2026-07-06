"""Notepad Open/Save As now drive the graphical file picker (filedialog)."""
import os
import tempfile

import harness as H
import widgets as W
import apps


def _field(dlg):
    return next(w for w in dlg.widgets if isinstance(w, W.TextField))


def _button(win, label):
    return next(w for w in win.widgets
               if isinstance(w, W.Button) and w.text == label)


tmp = tempfile.mkdtemp()

# ── Save As drives the picker: type a path, Enter commits + retitles ──
d = H.make_desk()
apps.open(d, "notepad", None)
np = H.find_window(d, "Notepad")
H.type_text(d, "hello")
assert np.modified and np.path is None

np._save_as()
dlg = d.wm.modal_top()
assert dlg is not None and dlg.title == "Save As", dlg
good = os.path.join(tmp, "note.txt")
_field(dlg).set(good)
H.key(d, "Enter")

assert np.path == good, np.path
assert not np.modified
assert np.title == "note.txt - Notepad", np.title
with open(good, encoding="utf-8") as f:
    assert f.read() == "hello"

# ── Save As over an existing file: the dialog owns the overwrite prompt ──
np.ta.set_text("hello world")
np._save_as()
dlg = d.wm.modal_top()
_field(dlg).set(good)                 # same path -> replace? prompt
H.key(d, "Enter")
ask = d.wm.modal_top()
assert ask is not dlg and ask.title == "Save As", ask
_button(ask, "Yes").cb()              # confirm replace
assert np.path == good and not np.modified
with open(good, encoding="utf-8") as f:
    assert f.read() == "hello world"

# ── a missing file typed into Open warns and does not load or latch ──
np2_path = np.path
np._open()
dlg = d.wm.modal_top()
assert dlg.title == "Open", dlg
_field(dlg).set(os.path.join(tmp, "nope.txt"))
H.key(d, "Enter")
warn = d.wm.modal_top()
assert warn is not dlg and warn.title == "Open", warn   # not-found box
warn.close()
dlg.close()
assert np.path == np2_path

# ── Open drives the picker: type a real path, Enter loads it ──
other = os.path.join(tmp, "other.md")
with open(other, "w", encoding="utf-8") as f:
    f.write("from disk")
d.wm.activate(np)
np._open()
dlg = d.wm.modal_top()
assert dlg is not None and dlg.title == "Open", dlg
_field(dlg).set(other)
H.key(d, "Enter")

assert np.path == other, np.path
assert np.ta.text() == "from disk", np.ta.text()
assert not np.modified
assert np.title == "other.md - Notepad", np.title

# ── Ctrl+S with no path re-routes through Save As (the picker) ──
apps.open(d, "notepad", None)
np2 = H.find_window(d, "Notepad")
H.type_text(d, "x")
H.key(d, "s", ctrl=True)
top = d.wm.modal_top()
assert top is not None and top.title == "Save As", top
top.close()

print("ok")
