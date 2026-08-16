import os

import harness as H
import apps
import wm
from PIL import Image


# ── PrtSc arrives as kitty functional code 57361 and is named, not dropped ───
def test_prtsc_functional_code_is_named():
    d = H.make_desk()
    e = d._norm_key({"key": chr(57361), "mods": 1, "text": ""})
    assert e is not None and e.key == "PrintScreen" and e.text == ""
    # release must stay dropped like any other key release
    assert d._norm_key({"key": chr(57361), "mods": 1, "text": "",
                        "evt": 3}) is None


# ── the raw CSI-u byte stream parses to that functional code ─────────────────
def test_prtsc_csi_u_parses():
    evs = H.term_feed(b"\x1b[57361u")
    assert evs and evs[0]["kind"] == "key" and evs[0]["key"] == chr(57361)
    assert evs[0]["text"] == ""


# ── PrtSc saves a timestamped PNG of the whole screen to the desktop dir ─────
def test_prtsc_saves_screen_png_to_desktop_folder():
    with H.desktop_dir() as folder:
        d = H.make_desk((640, 480))
        H.key(d, "PrintScreen")
        shots = [f for f in os.listdir(folder) if f.endswith(".png")]
        assert len(shots) == 1 and shots[0].startswith("Screenshot "), shots
        with Image.open(os.path.join(folder, shots[0])) as img:
            assert img.size == (640, 480)
        # the shot appears as a desktop icon right away
        assert any(it["label"] == shots[0] for it in d.shell.grid.items)
        assert d.dirty


# ── Alt+PrtSc captures just the active window ────────────────────────────────
def test_alt_prtsc_captures_the_active_window():
    with H.desktop_dir() as folder:
        d = H.make_desk((1024, 768))
        apps.open(d, "notepad", None)
        np = H.find_window(d, "Notepad")
        H.key(d, "PrintScreen", alt=True)
        shots = [f for f in os.listdir(folder) if f.endswith(".png")]
        assert len(shots) == 1, shots
        with Image.open(os.path.join(folder, shots[0])) as img:
            assert img.size == (np.w, np.h)


# ── with no active window, Alt+PrtSc falls back to the whole screen ──────────
def test_alt_prtsc_without_window_captures_the_screen():
    with H.desktop_dir() as folder:
        d = H.make_desk((640, 480))
        H.key(d, "PrintScreen", alt=True)
        shots = [f for f in os.listdir(folder) if f.endswith(".png")]
        assert len(shots) == 1, shots
        with Image.open(os.path.join(folder, shots[0])) as img:
            assert img.size == (640, 480)


# ── same-second shots never overwrite each other ─────────────────────────────
def test_same_second_shots_get_distinct_names():
    with H.desktop_dir() as folder:
        d = H.make_desk((640, 480))
        first = d.screenshot()
        second = d.screenshot()
        assert first != second
        assert os.path.exists(first) and os.path.exists(second)


# ── PrtSc still works (and captures) with an open menu or a modal up ─────────
def test_prtsc_fires_over_menus_and_modals():
    with H.desktop_dir() as folder:
        d = H.make_desk((1024, 768))
        d.taskbar.open_start_menu()
        assert d.menus.active
        H.key(d, "PrintScreen")
        assert d.menus.active, "PrtSc must not disturb the open menu"
        wm.msgbox(d, "T", "hi", buttons=("OK",))
        H.key(d, "PrintScreen")
        shots = [f for f in os.listdir(folder) if f.endswith(".png")]
        assert len(shots) == 2, shots


# ── a failed save reports a message box instead of crashing the desk ─────────
def test_failed_save_reports_a_dialog():
    with H.desktop_dir():
        d = H.make_desk((640, 480))
        d.shell.dir = os.path.join(d.shell.dir, "gone")   # unwritable target
        assert d.screenshot() is None
        dlg = d.wm.modal_top()
        assert dlg is not None and dlg.title == "Screenshot"


for _name, _fn in sorted(list(globals().items())):
    if _name.startswith("test_") and callable(_fn):
        _fn()
print("ok")
