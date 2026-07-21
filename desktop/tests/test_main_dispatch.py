import harness as H
import apps
import wm
import theme as T
import main as desk_main


class FakeTerm:
    def __init__(self, cols=80, rows=24, cell_w=8, cell_h=16):
        self.cols, self.rows = cols, rows
        self.cell_w, self.cell_h = cell_w, cell_h
        self.writes = []

    def refresh_size(self):
        pass

    def write(self, data):
        self.writes.append(data)


# ── F01/F08: taskbar is hit-tested before windows that overlap it ────────────
def test_taskbar_hit_order():
    d = H.make_desk((1024, 768))
    apps.open(d, "notepad", None)
    np = H.find_window(d, "Notepad")
    np.x, np.y = 0, 720                 # drag the window down over the taskbar
    assert d.taskbar.menu_open == -1
    H.press(d, 10, 754)                 # click the Start button through it
    # pre-fix: window_at() returns the notepad first, so the click lands in the
    # window (opening its own File menu) and the taskbar never sees it
    assert d.taskbar.menu_open == 1, "Start button click was stolen by the window"


# ── F11: un-maximize after a shrink keeps the restore rect reachable ─────────
def test_resize_restore_clamp():
    d = H.make_desk((1024, 768))
    apps.open(d, "notepad", None)
    np = H.find_window(d, "Notepad")
    np.x, np.y, np.w, np.h = 600, 500, 520, 380
    d.wm.toggle_maximize(np)           # saves _restore = (600, 500, 520, 380)
    d.term = FakeTerm(cols=80, rows=30)   # shrink to 640x480
    d.do_resize()
    d.wm.toggle_maximize(np)           # restore onto the smaller screen
    # pre-fix: restored verbatim to (600, 500) — fully off a 640x480 screen
    assert 0 <= np.x <= d.w - 60, (np.x, d.w)
    assert 0 <= np.y <= d.h - T.TASKBAR_H - 20, (np.y, d.h)
    assert d.wm.window_at(np.x + 10, np.y + 2) is np


# ── F13: numeric keypad PUA codes are translated, not dropped ────────────────
def test_keypad_translation():
    d = H.make_desk()
    e = d._norm_key({"key": chr(57399), "mods": 1, "text": ""})
    assert e is not None and e.key == "0" and e.text == "0"
    e = d._norm_key({"key": chr(57408), "mods": 1, "text": ""})
    assert e.key == "9" and e.text == "9"
    e = d._norm_key({"key": chr(57414), "mods": 1, "text": ""})   # KP Enter
    assert e.key == "Enter" and e.text == "\r"
    e = d._norm_key({"key": chr(57417), "mods": 1, "text": ""})   # KP Left
    assert e.key == "ArrowLeft"
    e = d._norm_key({"key": chr(57426), "mods": 1, "text": ""})   # KP Delete
    assert e.key == "Delete"
    # a genuine bare-modifier functional code is still dropped
    assert d._norm_key({"key": chr(57344), "mods": 1, "text": ""}) is None


# ── F15: double-click counter is per-button ──────────────────────────────────
def test_double_click_button_identity():
    d = H.make_desk()
    d._norm_mouse({"b": 2, "x": 100, "y": 100, "press": True})    # right press
    left = d._norm_mouse({"b": 0, "x": 100, "y": 100, "press": True})
    assert left.clicks == 1, "right-then-left counted as a double click"
    # same-button repeat still counts as a double click
    d2 = H.make_desk()
    d2._norm_mouse({"b": 0, "x": 50, "y": 50, "press": True})
    e = d2._norm_mouse({"b": 0, "x": 50, "y": 50, "press": True})
    assert e.clicks == 2


# ── F16: another button's release does not abort an in-progress drag ─────────
def test_drag_survives_other_button_release():
    d = H.make_desk((1024, 768))
    apps.open(d, "notepad", None)
    np = H.find_window(d, "Notepad")
    np.x, np.y = 100, 100
    sx, sy = np.x + 40, np.y + 10
    H.press(d, sx, sy)                     # left press on the title bar
    H.move(d, sx + 30, sy + 30, btn=1)     # drag
    assert (np.x, np.y) == (130, 130), (np.x, np.y)
    H.press(d, sx + 30, sy + 30, btn=3)    # a right press/release mid-drag
    H.release(d, sx + 30, sy + 30, btn=3)
    H.move(d, sx + 60, sy + 60, btn=1)     # keep dragging with the left button
    H.release(d, sx + 60, sy + 60, btn=1)
    # pre-fix: the right release ends the drag, so the window stops at (130,130)
    assert (np.x, np.y) == (160, 160), (np.x, np.y)


# ── F16c: a foreign button doesn't drop a per-widget (text-select) drag ──────
def test_widget_drag_survives_other_button_release():
    d = H.make_desk((1024, 768))
    apps.open(d, "notepad", None)
    np = H.find_window(d, "Notepad")
    np.x, np.y = 100, 100
    np.ta.set_text("\n".join("line %02d aaaaaaaa" % i for i in range(12)))
    ta = np.ta
    cox, coy = np.client_origin()
    tx, ty = cox + ta.x, coy + ta.y
    H.press(d, tx + 5, ty + 5)                  # start selecting near the top
    H.move(d, tx + 5, ty + 20, btn=1)
    assert np._capture is ta
    H.press(d, tx + 5, ty + 20, btn=3)          # foreign right press/release
    H.release(d, tx + 5, ty + 20, btn=3)
    # pre-fix: the right press rebinds _capture_btn and its release clears the
    # per-widget capture, so the drag below stops extending the selection
    assert np._capture is ta, "widget capture dropped by a foreign release"
    H.move(d, tx + 5, ty + ta.h + 200, btn=1)   # keep dragging off the widget
    assert ta.cr == 11, ("selection stopped tracking", ta.cr)


# ── F17: keyboard is modal too ───────────────────────────────────────────────
def test_modal_blocks_keyboard():
    d = H.make_desk((1024, 768))
    apps.open(d, "notepad", None)
    np = H.find_window(d, "Notepad")
    dlg = wm.msgbox(d, "T", "hi", buttons=("OK",))
    assert dlg.modal and d.wm.modal_top() is dlg
    d.wm.activate(np)                      # as a taskbar click would
    assert d.wm.active is np
    before = np.ta.text()
    H.key(d, "a"); H.key(d, "b")
    # pre-fix: 'ab' is typed into the notepad behind the modal
    assert np.ta.text() == before, "typing reached a window behind the modal"
    assert d.wm.active is dlg, "modal not re-activated"
    # dialog keyboard (Enter/Escape) still works when the modal is on top
    H.key(d, "Escape")
    assert d.wm.modal_top() is None, "Escape did not close the modal"


# ── F42: open menus are closed on a resize ───────────────────────────────────
def test_menus_close_on_resize():
    d = H.make_desk((1024, 768))
    d.term = FakeTerm(cols=80, rows=24)
    d.taskbar.open_start_menu()
    assert d.menus.active
    d.do_resize()
    assert not d.menus.active, "menu survived the resize with stale geometry"


# ── F46/W00: horizontal wheel is not a vertical scroll ───────────────────────
def test_horizontal_wheel():
    d = H.make_desk()
    assert d._norm_mouse({"b": 66, "x": 100, "y": 100}) is None
    assert d._norm_mouse({"b": 67, "x": 100, "y": 100}) is None
    assert d._norm_mouse({"b": 64, "x": 100, "y": 100}).wheel == -1
    assert d._norm_mouse({"b": 65, "x": 100, "y": 100}).wheel == 1


# ── W01: side buttons 8-11 produce no event ──────────────────────────────────
def test_side_buttons_ignored():
    d = H.make_desk()
    assert d._norm_mouse({"b": 128, "x": 10, "y": 10, "press": True}) is None
    assert d._norm_mouse({"b": 129, "x": 10, "y": 10, "press": True}) is None


# ── WHEEL companion: padding-edge coords are clamped into the framebuffer ─────
def test_coord_clamp():
    d = H.make_desk((1024, 768))
    e = d._norm_mouse({"b": 64, "x": -3, "y": -5})
    assert e.x == 0 and e.y == 0
    e = d._norm_mouse({"b": 64, "x": d.w + 50, "y": d.h + 50})
    assert e.x == d.w - 1 and e.y == d.h - 1


# ── F39: graphics-delete APC is tmux-wrapped in streamed tmux sessions ───────
def test_restore_tmux_wraps_delete():
    import os
    keys = ("KILIX_STREAM", "TMUX")
    saved = {k: os.environ.get(k) for k in keys}

    def run():
        t = object.__new__(desk_main.DeskTerm)
        writes = []
        t.write = writes.append
        t.fd, t.saved = 0, None            # tcsetattr will raise; write ran first
        try:
            t.restore()
        except Exception:
            pass
        return "".join(writes)

    try:
        os.environ["KILIX_STREAM"], os.environ["TMUX"] = "1", "x"
        out = run()
        assert "\x1bPtmux;" in out, "delete APC not wrapped for tmux passthrough"
        os.environ.pop("KILIX_STREAM"); os.environ.pop("TMUX")
        out = run()
        assert "\x1bPtmux;" not in out
        assert "\x1b_Ga=d,d=A\x1b\\" in out
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── W02: local t=s objects are private and bounded ───────────────────────────
def test_frame_shm_is_private():
    import base64
    import os
    import re
    import stat

    t = FakeTerm(cols=4, rows=3, cell_w=8, cell_h=8)
    d = desk_main.Desk(term=t)
    try:
        d.blit()
        match = re.search(r"t=s[^;]*;([A-Za-z0-9+/=]+)\x1b\\", t.writes[-1])
        assert match
        name = base64.b64decode(match.group(1)).decode()
        frame = "/dev/shm/" + name.lstrip("/")
        assert stat.S_IMODE(os.stat(frame).st_mode) == 0o600
        assert os.path.getsize(frame) == d.w * d.h * 3
        assert "N=1" in t.writes[-1]
        assert "t=t" not in t.writes[-1]
    finally:
        d.cleanup_shm()
    assert not os.path.exists(frame)


# ── W03: an unconsumed three-slot ring applies newest-frame backpressure ─────
def test_frame_shm_ring_is_bounded_and_newest_wins():
    import base64
    import os
    import re

    t = FakeTerm(cols=4, rows=3, cell_w=8, cell_h=8)
    d = desk_main.Desk(term=t)
    try:
        for _ in range(10):
            d.blit(force_full=True)
        names = [base64.b64decode(match.group(1)).decode()
                 for write in t.writes
                 for match in [re.search(
                     r"t=s[^;]*;([A-Za-z0-9+/=]+)\x1b\\", write)] if match]
        assert len(names) == 3, names
        assert len(set(names)) == 3
        assert d.presenter.stats.frames_dropped == 6
        # Kitty's unlink is the consumption acknowledgement. Free one slot;
        # flush must emit the newest queued request under that safely reused
        # name, never grow the ring.
        os.unlink("/dev/shm/" + names[0].lstrip("/"))
        assert d.presenter.flush().emitted
        match = re.search(r"t=s[^;]*;([A-Za-z0-9+/=]+)\x1b\\", t.writes[-1])
        assert base64.b64decode(match.group(1)).decode() == names[0]
        assert all("N=1" in write for write in t.writes)
    finally:
        d.cleanup_shm()


for _name, _fn in sorted(list(globals().items())):
    if _name.startswith("test_") and callable(_fn):
        _fn()
print("ok")
