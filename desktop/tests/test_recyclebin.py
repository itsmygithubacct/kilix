"""Recycle Bin window: lists recycled items, restores, and empties.

Drives the real RecycleBin window over a temp bin via the harness; the bin
is redirected to a temp dir so nothing touches real recycled data."""
import os
import tempfile

import harness as H
import recycle
import widgets as W
from apps.recyclebin import RecycleBin


def _isolate_bin():
    os.environ["KILIX_RECYCLE_DIR"] = tempfile.mkdtemp(prefix="kilix95-recbin-")


def _win(desk):
    win = RecycleBin(desk)
    desk.wm.add(win)
    return win


def _yes(desk):
    dlg = desk.wm.modal_top()
    for wdg in dlg.widgets:
        if isinstance(wdg, W.Button) and wdg.text == "Yes":
            wdg.cb()
            return
    raise AssertionError("no Yes button on the confirm dialog")


def test_lists_recycled_item():
    _isolate_bin()
    work = tempfile.mkdtemp()
    p = os.path.join(work, "gone.txt")
    with open(p, "w") as f:
        f.write("bye")
    recycle.send(p)

    d = H.make_desk()
    win = _win(d)
    assert len(win.items) == 1
    assert win.items[0]["name"] == "gone.txt"
    assert win.lb.items[0][1].startswith("gone.txt")     # row shows the name
    d.dirty = True
    d.render()                                            # paints without error


def test_empty_message_when_empty():
    _isolate_bin()
    d = H.make_desk()
    win = _win(d)
    assert win.items == []
    d.dirty = True
    d.render()


def test_restore_puts_file_back():
    _isolate_bin()
    work = tempfile.mkdtemp()
    p = os.path.join(work, "back.txt")
    with open(p, "w") as f:
        f.write("data")
    recycle.send(p)
    assert not os.path.exists(p)

    d = H.make_desk()
    win = _win(d)
    win.lb.sel = 0
    win._restore_item(win._selected())
    assert os.path.exists(p)                              # returned to origin
    assert recycle.items() == []                          # gone from the bin
    assert win.items == []                                # view refreshed


def test_delete_purges_one():
    _isolate_bin()
    work = tempfile.mkdtemp()
    p = os.path.join(work, "kill.txt")
    with open(p, "w") as f:
        f.write("x")
    recycle.send(p)

    d = H.make_desk()
    win = _win(d)
    win.lb.sel = 0
    win._purge(win._selected())
    _yes(d)
    assert recycle.items() == []
    assert not os.path.exists(p)                          # not restored, purged
    assert win.items == []


def test_empty_clears_bin():
    _isolate_bin()
    work = tempfile.mkdtemp()
    for n in ("a", "b", "c"):
        q = os.path.join(work, n)
        open(q, "w").close()
        recycle.send(q)

    d = H.make_desk()
    win = _win(d)
    assert len(win.items) == 3
    win._empty()
    _yes(d)
    assert recycle.items() == []
    assert win.items == []


if __name__ == "__main__":
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
