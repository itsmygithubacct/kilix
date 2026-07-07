"""filedialog Open/Save behaviour, driven offscreen over temp trees."""
import os
import tempfile

import harness as H
import widgets as W
import filedialog


def _labels(dlg):
    return [it[1] for it in dlg.list.items]


def _find(dlg, label):
    for it in dlg.list.items:
        if it[1] == label:
            return it
    raise AssertionError(f"no {label!r} in view; got {_labels(dlg)}")


def _click_button(win, text):
    for wdg in win.widgets:
        if isinstance(wdg, W.Button) and wdg.text == text:
            wdg.cb()
            return
    raise AssertionError(f"no {text!r} button")


def test_open_navigates_and_returns_file():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    sub = os.path.join(root, "sub")
    os.mkdir(sub)
    target = os.path.join(sub, "hello.txt")
    with open(target, "w") as f:
        f.write("hi")

    got = {}
    dlg = filedialog.open_file(d, "Open", lambda p: got.setdefault("p", p),
                               start=root)
    assert "sub" in _labels(dlg)                    # subdir listed
    dlg._activate(_find(dlg, "sub"))                # double-click the folder
    assert dlg.cwd == sub and "hello.txt" in _labels(dlg)
    dlg._select(_find(dlg, "hello.txt"))            # single click → name field
    assert dlg.name.text == "hello.txt"
    dlg._confirm()
    assert got["p"] == target                       # absolute chosen path
    assert dlg not in d.wm.windows                   # dialog closed


def test_open_double_click_file_confirms():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    p = os.path.join(root, "doc.txt")
    with open(p, "w") as f:
        f.write("x")
    got = {}
    dlg = filedialog.open_file(d, "Open", lambda r: got.setdefault("p", r),
                               start=root)
    dlg._activate(_find(dlg, "doc.txt"))            # double-click a file confirms
    assert got["p"] == p


def test_filter_hides_non_matching():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    for n in ("a.txt", "b.dat"):
        open(os.path.join(root, n), "w").close()
    dlg = filedialog.open_file(d, "Open", lambda p: None, start=root,
                               filters=[("Text", "*.txt"), ("All", "*.*")])
    assert "a.txt" in _labels(dlg) and "b.dat" not in _labels(dlg)
    dlg.ftype.index = 1                              # switch to All Files
    dlg._fill()
    assert "a.txt" in _labels(dlg) and "b.dat" in _labels(dlg)


def test_hidden_dotfiles_hidden():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    open(os.path.join(root, ".secret"), "w").close()
    open(os.path.join(root, "shown.txt"), "w").close()
    dlg = filedialog.open_file(d, "Open", lambda p: None, start=root)
    assert ".secret" not in _labels(dlg) and "shown.txt" in _labels(dlg)


def test_open_missing_file_stays_open():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    got = {}
    dlg = filedialog.open_file(d, "Open", lambda p: got.setdefault("p", p),
                               start=root)
    dlg.name.set("nope.txt")
    dlg._confirm()
    assert "p" not in got and dlg in d.wm.windows    # rejected, no callback
    assert d.wm.modal_top() is not dlg               # warning msgbox on top


def test_save_returns_new_path():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    got = {}
    dlg = filedialog.save_file(d, "Save As", lambda p: got.setdefault("p", p),
                               start=root)
    dlg.name.set("brand-new.txt")
    dlg._confirm()
    assert got["p"] == os.path.join(root, "brand-new.txt")   # nonexistent ok
    assert not os.path.exists(got["p"])


def test_save_prompts_on_overwrite():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    p = os.path.join(root, "old.txt")
    with open(p, "w") as f:
        f.write("old")
    got = {}
    dlg = filedialog.save_file(d, "Save As", lambda r: got.setdefault("p", r),
                               start=root)
    dlg.name.set("old.txt")
    dlg._confirm()
    assert "p" not in got                            # waits on the prompt
    box = d.wm.modal_top()
    assert box is not dlg
    _click_button(box, "Yes")                        # confirm the overwrite
    assert got["p"] == p


def test_cancel_yields_none():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    got = {}
    dlg = filedialog.open_file(d, "Open",
                               lambda p: got.__setitem__("p", p), start=root)
    _click_button(dlg, "Cancel")
    assert got["p"] is None and dlg not in d.wm.windows


def test_bad_start_falls_back_home():
    d = H.make_desk()
    dlg = filedialog.open_file(d, "Open", lambda p: None,
                               start="/no/such/dir/anywhere")
    assert os.path.isdir(dlg.cwd)                    # never crashes, valid cwd


def test_inaccessible_nav_stays_put():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    dlg = filedialog.open_file(d, "Open", lambda p: None, start=root)
    assert dlg.cwd == root
    dlg._nav(os.path.join(root, "ghost"))            # vanished/unreadable target
    assert dlg.cwd == root                           # stays put, no jump to home
    assert d.wm.modal_top() is not dlg               # not-accessible warning shown


# ── folder-picker mode (pick_folder) ─────────────────────────────────────────
def test_pick_folder_lists_only_dirs():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    os.mkdir(os.path.join(root, "sub"))
    open(os.path.join(root, "file.txt"), "w").close()
    dlg = filedialog.pick_folder(d, "Look In", lambda p: None, start=root)
    assert "sub" in _labels(dlg)                     # folders shown
    assert "file.txt" not in _labels(dlg)            # files hidden


def test_pick_folder_returns_current():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    got = {}
    dlg = filedialog.pick_folder(d, "Look In",
                                 lambda p: got.setdefault("p", p), start=root)
    _click_button(dlg, "Select")                     # nothing picked → this folder
    assert got["p"] == os.path.abspath(root)
    assert dlg not in d.wm.windows


def test_pick_folder_returns_selected_subdir():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    sub = os.path.join(root, "target")
    os.mkdir(sub)
    got = {}
    dlg = filedialog.pick_folder(d, "Look In",
                                 lambda p: got.setdefault("p", p), start=root)
    dlg._select(_find(dlg, "target"))                # single-click a subfolder
    assert dlg.name.text == "target"
    _click_button(dlg, "Select")
    assert got["p"] == sub


def test_pick_folder_double_click_navigates_not_confirms():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    sub = os.path.join(root, "deep")
    os.mkdir(sub)
    got = {}
    dlg = filedialog.pick_folder(d, "Look In",
                                 lambda p: got.setdefault("p", p), start=root)
    dlg._activate(_find(dlg, "deep"))                # double-click enters it
    assert dlg.cwd == sub and "p" not in got         # navigated, not chosen yet
    _click_button(dlg, "Select")
    assert got["p"] == sub                           # now the entered folder


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
