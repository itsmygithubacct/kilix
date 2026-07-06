import os
import tempfile

import harness as H
import recycle
from apps import mycomp

# isolate the bin: recycle stores beside KILIX_DESKTOP_DIR (a shared /tmp
# sibling under the harness), so pin an own bin for this process.
os.environ["KILIX_RECYCLE_DIR"] = tempfile.mkdtemp(prefix="kilix95-mycomp-bin-")


def _labels(win):
    return [it["label"] for it in win.grid.items]


def _entry(win, label):
    return next(it for it in win.grid.items if it["label"] == label)


def _open():
    d = H.make_desk()
    win = mycomp.MyComputer(d)
    d.wm.add(win)
    return d, win


# ── the top-level namespace is present ───────────────────────────────────────
def test_entries_exist():
    d, win = _open()
    labels = _labels(win)
    for want in ("Local Disk (/)", "Home", "Desktop", "Control Panel",
                 "Recycle Bin"):
        assert want in labels, f"missing {want!r}: {labels}"
    drive = _entry(win, "Local Disk (/)")
    assert drive["data"] == ("filemgr", "/")
    assert _entry(win, "Home")["data"][0] == "filemgr"
    assert _entry(win, "Desktop")["data"][1] == d.shell.dir


# ── activating the drive opens a File Manager there ──────────────────────────
def test_drive_opens_filemgr():
    d, win = _open()
    win._activate(_entry(win, "Local Disk (/)"))
    fw = H.find_window(d, "FileWindow")
    assert fw is not None, "no File Manager window opened"
    assert os.path.abspath(fw.path) == "/"


# ── the Recycle Bin entry's icon reflects fullness ───────────────────────────
def test_recycle_bin_icon_reflects_fullness():
    recycle.empty()
    d, win = _open()
    assert _entry(win, "Recycle Bin")["icon"] == "recyclebin_empty"

    p = os.path.join(d.shell.dir, "trashme.txt")
    with open(p, "w") as f:
        f.write("junk")
    recycle.send(p)
    win._refresh_bin()
    assert _entry(win, "Recycle Bin")["icon"] == "recyclebin_full"

    recycle.empty()
    win._refresh_bin()
    assert _entry(win, "Recycle Bin")["icon"] == "recyclebin_empty"


# ── the cheap emptiness probe agrees with items() but reads no sidecars ───────
def test_has_items_cheap_probe():
    recycle.empty()
    assert recycle.has_items() is False
    p = os.path.join(tempfile.mkdtemp(), "probe.txt")
    with open(p, "w") as f:
        f.write("x")
    recycle.send(p)
    assert recycle.has_items() is True
    assert bool(recycle.items()) is True
    recycle.empty()
    assert recycle.has_items() is False


# ── Control Panel opens Settings ─────────────────────────────────────────────
def test_control_panel_opens_settings():
    d, win = _open()
    win._activate(_entry(win, "Control Panel"))
    assert H.find_window(d, "SettingsWin") is not None


# ── Recycle Bin opens the bin, singleton, and can restore ────────────────────
def test_recycle_bin():
    d, win = _open()
    p = os.path.join(d.shell.dir, "gone.txt")
    with open(p, "w") as f:
        f.write("bye")
    recycle.send(p)
    assert not os.path.exists(p)

    win._activate(_entry(win, "Recycle Bin"))
    binw = H.find_window(d, "RecycleBin")
    assert binw is not None
    from apps import recyclebin
    assert isinstance(binw, recyclebin.RecycleBin)

    # re-activating focuses the same bin, not a second one
    win._activate(_entry(win, "Recycle Bin"))
    assert sum(type(w).__name__ == "RecycleBin" for w in d.wm.windows) == 1


test_entries_exist()
test_recycle_bin_icon_reflects_fullness()
test_drive_opens_filemgr()
test_control_panel_opens_settings()
test_recycle_bin()
test_has_items_cheap_probe()
print("ok")
