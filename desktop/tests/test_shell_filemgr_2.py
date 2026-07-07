"""Regression tests for F51 (Back into a deleted dir desyncs history) and
F57 (desktop grid / File Manager cross-refresh after create/delete/rename)."""
import os
import shutil
import tempfile

import harness as H
from apps import filemgr


def _labels(grid):
    return [it["label"] for it in grid.items]


def _click_dialog(desk, label):
    """Press the named button in the current modal dialog (fires its cb)."""
    import widgets as W
    dlg = desk.wm.modal_top()
    for wdg in dlg.widgets:
        if isinstance(wdg, W.Button) and wdg.text == label:
            wdg.cb()
            return
    raise AssertionError(f"no {label!r} button in dialog")


# ── F51: Back into a deleted directory must not desync history ──────────────
def test_back_into_deleted_dir():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    try:
        a = os.path.join(root, "A")
        b = os.path.join(root, "B")
        os.mkdir(a)
        os.mkdir(b)
        win = filemgr.FileWindow(d, a)
        d.wm.add(win)
        win.navigate(b)
        assert win.hist_i == 1, win.hist_i
        assert os.path.basename(win.path) == "B"

        shutil.rmtree(a)                       # the Back target vanishes
        win._go(-1)                            # navigate(A) fails inside

        # pre-fix: hist_i latched at 0 while path is still B, so Back was dead
        # and Forward wrongly disabled. Post-fix: history stays consistent.
        assert win.path == os.path.abspath(b), win.path
        assert win.hist_i == 1, ("hist_i desynced", win.hist_i)
        assert win.b_back.enabled is True
        assert win.b_fwd.enabled is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── F57: filemgr delete on the desktop folder refreshes the desktop grid ────
def test_filemgr_delete_refreshes_desktop_grid():
    with H.desktop_dir() as dd:
        open(os.path.join(dd, "note.txt"), "w").close()
        d = H.make_desk()
        assert "note.txt" in _labels(d.shell.grid)

        win = filemgr.FileWindow(d, dd)
        d.wm.add(win)
        assert "note.txt" in _labels(win.grid)

        sel = [it for it in win.grid.items if it["label"] == "note.txt"]
        win._delete(sel)
        _click_dialog(d, "Yes")

        assert not os.path.exists(os.path.join(dd, "note.txt"))
        assert "note.txt" not in _labels(win.grid)
        # pre-fix the desktop grid kept the stale icon (filemgr only called
        # its own refresh()); now dir_changed refreshes the shell grid too.
        assert "note.txt" not in _labels(d.shell.grid), _labels(d.shell.grid)


# ── F57: filemgr create on the desktop folder shows up on the desktop grid ──
def test_filemgr_create_refreshes_desktop_grid():
    with H.desktop_dir() as dd:
        d = H.make_desk()
        win = filemgr.FileWindow(d, dd)
        d.wm.add(win)
        assert "fresh.txt" not in _labels(d.shell.grid)

        win._new_file()
        _click_dialog(d, "OK")                 # inputbox default is New File.txt
        assert os.path.exists(os.path.join(dd, "New File.txt"))
        assert "New File.txt" in _labels(win.grid)
        assert "New File.txt" in _labels(d.shell.grid), _labels(d.shell.grid)


def test_filemgr_new_file_rejects_path_name():
    d = H.make_desk()
    parent = tempfile.mkdtemp()
    root = os.path.join(parent, "root")
    os.mkdir(root)
    outside = os.path.join(parent, "escape.txt")
    try:
        win = filemgr.FileWindow(d, root)
        d.wm.add(win)
        win._new_file()
        dlg = d.wm.modal_top()
        import widgets as W
        fld = next(w for w in dlg.widgets if isinstance(w, W.TextField))
        fld.set("../escape.txt")
        _click_dialog(d, "OK")

        assert not os.path.exists(outside)
        assert "escape.txt" not in _labels(win.grid)
    finally:
        shutil.rmtree(parent, ignore_errors=True)


# ── F57: a shell-side creation refreshes an open File Manager on that dir ───
def test_shell_create_refreshes_open_filemgr():
    with H.desktop_dir() as dd:
        d = H.make_desk()
        win = filemgr.FileWindow(d, dd)
        d.wm.add(win)
        assert "made.txt" not in _labels(win.grid)

        # drive the shell's New Text File flow, retyping the field
        d.shell._new_file()
        dlg = d.wm.modal_top()
        import widgets as W
        fld = next(w for w in dlg.widgets if isinstance(w, W.TextField))
        fld.set("made.txt")
        _click_dialog(d, "OK")

        assert os.path.exists(os.path.join(dd, "made.txt"))
        assert "made.txt" in _labels(d.shell.grid)
        # pre-fix the open File Manager never learned about the new file.
        assert "made.txt" in _labels(win.grid), _labels(win.grid)


def test_shell_new_file_rejects_path_name():
    with H.desktop_dir() as dd:
        d = H.make_desk()
        name = os.path.basename(dd) + "-escape.txt"
        outside = os.path.join(os.path.dirname(dd), name)

        d.shell._new_file()
        dlg = d.wm.modal_top()
        import widgets as W
        fld = next(w for w in dlg.widgets if isinstance(w, W.TextField))
        fld.set("../" + name)
        _click_dialog(d, "OK")

        assert not os.path.exists(outside)
        assert name not in _labels(d.shell.grid)


# ── F57: a File Manager NOT on the desktop dir still refreshes itself ───────
def test_filemgr_non_desktop_dir_self_refresh():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    try:
        win = filemgr.FileWindow(d, root)
        d.wm.add(win)
        assert "x.txt" not in _labels(win.grid)
        win._new_file()
        dlg = d.wm.modal_top()
        import widgets as W
        fld = next(w for w in dlg.widgets if isinstance(w, W.TextField))
        fld.set("x.txt")
        _click_dialog(d, "OK")
        assert os.path.exists(os.path.join(root, "x.txt"))
        assert "x.txt" in _labels(win.grid), _labels(win.grid)
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
