"""Find: All Files — the bounded tree walk finds matches by name and content.

Drives a real FindFiles window over a temp tree via the harness; the scan
runs from the desk tick hook, so tests pump ticks until it settles."""
import os
import tempfile

import harness as H
from apps.findfiles import FindFiles


def _tree():
    root = tempfile.mkdtemp(prefix="kilix95-find-")
    os.makedirs(os.path.join(root, "sub", "deep"))
    open(os.path.join(root, "report.txt"), "w").write("hello world")
    open(os.path.join(root, "notes.md"), "w").write("nothing here")
    open(os.path.join(root, "sub", "report2.txt"), "w").write("needle inside")
    open(os.path.join(root, "sub", "deep", "other.log"), "w").write("x")
    return root


def _drain(desk, win, limit=200):
    for _ in range(limit):
        if not win._scanning:
            return
        win._tick(0.0)
    raise AssertionError("scan never finished")


def _labels(win):
    return [os.path.basename(p) for _ic, _t, p in win.results.items]


def test_find_by_name_pattern():
    d = H.make_desk()
    root = _tree()
    win = FindFiles(d, root)
    d.wm.add(win)
    win.f_name.set("*.txt")
    win._find_now()
    _drain(d, win)
    names = _labels(win)
    assert "report.txt" in names, names
    assert "report2.txt" in names                       # found in a subfolder
    assert "notes.md" not in names                      # pattern excludes it


def test_find_by_substring():
    d = H.make_desk()
    root = _tree()
    win = FindFiles(d, root)
    d.wm.add(win)
    win.f_name.set("report")                            # bare substring
    win._find_now()
    _drain(d, win)
    names = _labels(win)
    assert "report.txt" in names and "report2.txt" in names, names
    assert "other.log" not in names


def test_find_containing_text():
    d = H.make_desk()
    root = _tree()
    win = FindFiles(d, root)
    d.wm.add(win)
    win.f_look.set(root)
    win.f_text.set("needle")
    win._find_now()
    _drain(d, win)
    names = _labels(win)
    assert names == ["report2.txt"], names             # only the file with it


def test_new_search_clears_and_unhooks():
    d = H.make_desk()
    root = _tree()
    win = FindFiles(d, root)
    d.wm.add(win)
    win.f_name.set("report")
    win._find_now()
    _drain(d, win)
    assert win.results.items
    win._new_search()
    assert win.results.items == [] and win.f_name.text == ""
    win.close()
    assert win._tick not in d.tick_hooks               # teardown unhooked it


def test_renders_clean():
    d = H.make_desk()
    win = FindFiles(d, _tree())
    d.wm.add(win)
    win.f_name.set("*")
    win._find_now()
    _drain(d, win)
    d.dirty = True
    d.render()


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("ok")
