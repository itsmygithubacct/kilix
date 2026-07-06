"""Explorer file operations on FileWindow (copy/cut/paste/delete/properties).

Each test drives a real FileWindow over a temp dir via the harness; the
Recycle Bin is redirected to a temp dir so Delete never touches real data."""
import os
import tempfile

import harness as H
import recycle
import wm
from apps import filemgr


def _isolate_bin():
    os.environ["KILIX_RECYCLE_DIR"] = tempfile.mkdtemp(prefix="kilix95-recbin-")


def _win(desk, path):
    win = filemgr.FileWindow(desk, path)
    desk.wm.add(win)
    return win


def _select(win, label):
    for i, it in enumerate(win.grid.items):
        if it["label"] == label:
            win.grid.sel = {i}
            return it
    raise AssertionError(f"no {label!r} in view; got "
                         f"{[i['label'] for i in win.grid.items]}")


def _labels(win):
    return [it["label"] for it in win.grid.items]


def test_copy_paste_makes_duplicate():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    with open(os.path.join(root, "a.txt"), "w") as f:
        f.write("AAA")
    win = _win(d, root)
    _select(win, "a.txt")
    win._copy()
    win._paste()
    assert os.path.exists(os.path.join(root, "a.txt"))          # original kept
    assert os.path.exists(os.path.join(root, "a - Copy.txt"))   # duplicate made
    assert "a - Copy.txt" in _labels(win)                       # view refreshed


def test_cut_paste_moves():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    src, dst = os.path.join(root, "src"), os.path.join(root, "dst")
    os.mkdir(src)
    os.mkdir(dst)
    with open(os.path.join(src, "f.txt"), "w") as f:
        f.write("data")
    win = _win(d, src)
    _select(win, "f.txt")
    win._cut()
    win.navigate(dst)
    win._paste()
    assert not os.path.exists(os.path.join(src, "f.txt"))       # moved out
    assert os.path.exists(os.path.join(dst, "f.txt"))           # moved in
    assert "f.txt" in _labels(win)


def test_delete_sends_to_recycle():
    _isolate_bin()
    assert recycle.items() == []
    d = H.make_desk()
    root = tempfile.mkdtemp()
    p = os.path.join(root, "trash.txt")
    with open(p, "w") as f:
        f.write("bye")
    win = _win(d, root)
    sel = [_select(win, "trash.txt")]
    win._delete(sel)
    # confirm dialog: fire its Yes button
    dlg = d.wm.modal_top()
    import widgets as W
    for wdg in dlg.widgets:
        if isinstance(wdg, W.Button) and wdg.text == "Yes":
            wdg.cb()
            break
    assert not os.path.exists(p)                                # gone from disk
    items = recycle.items()
    assert len(items) == 1 and items[0]["name"] == "trash.txt"  # in the bin
    assert "trash.txt" not in _labels(win)


def test_drop_moves_into_folder():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    os.mkdir(os.path.join(root, "box"))
    with open(os.path.join(root, "m.txt"), "w") as f:
        f.write("x")
    win = _win(d, root)
    src = _select(win, "m.txt")
    box = next(it for it in win.grid.items if it["label"] == "box")
    win._drop([src], box)
    assert not os.path.exists(os.path.join(root, "m.txt"))
    assert os.path.exists(os.path.join(root, "box", "m.txt"))


def test_properties_reports_size():
    d = H.make_desk()
    root = tempfile.mkdtemp()
    with open(os.path.join(root, "big.bin"), "wb") as f:
        f.write(b"Z" * 2048)
    win = _win(d, root)
    it = _select(win, "big.bin")

    captured = {}
    orig = wm.msgbox
    wm.msgbox = lambda desk, title, text, **k: captured.update(
        title=title, text=text)
    try:
        win._properties(it)
    finally:
        wm.msgbox = orig
    assert "big.bin" in captured["title"]
    assert "2.0 KB" in captured["text"], captured["text"]


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
