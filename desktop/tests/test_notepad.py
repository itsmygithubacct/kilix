"""Notepad Save As: a failed write must not latch the bad path (F29)."""
import os
import tempfile

import harness as H
import widgets as W
import apps


def _field(dlg):
    return next(w for w in dlg.widgets if isinstance(w, W.TextField))


# ── F29: Save As to a directory fails; path/title/dirty must stay truthful ──
d = H.make_desk()
apps.open(d, "notepad", None)
np = H.find_window(d, "Notepad")
H.type_text(d, "hello")
assert np.modified and np.path is None

baddir = tempfile.mkdtemp()          # open(dir, "w") -> IsADirectoryError
np._save_as()
dlg = d.wm.modal_top()
assert dlg is not None and dlg.title == "Save As", dlg
_field(dlg).set(baddir)
H.key(d, "Enter")

# the write failed: nothing latched, title still says Untitled, still dirty
assert np.path is None, ("bad path latched", np.path)
assert np.modified
assert np.title == "*Untitled - Notepad", np.title

# dismiss the error box the failed write raised, back to the editor
err = d.wm.modal_top()
assert err is not None and err.title == "Notepad", err
err.close()
d.wm.activate(np)

# Ctrl+S must re-prompt Save As, not silently retry the bad path
H.key(d, "s", ctrl=True)
top = d.wm.modal_top()
assert top is not None and top.title == "Save As", ("no re-prompt", top)
top.close()

# ── happy path unchanged: a good Save As commits the path and retitles ──
good = os.path.join(baddir, "note.txt")
np._save_as()
dlg = d.wm.modal_top()
_field(dlg).set(good)
H.key(d, "Enter")

assert np.path == good, np.path
assert not np.modified
assert np.title == "note.txt - Notepad", np.title
with open(good, encoding="utf-8") as f:
    assert f.read() == "hello"

print("ok")
