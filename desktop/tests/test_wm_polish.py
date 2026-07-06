"""WM/loop polish: Alt+Tab switcher, title-bar tooltips, idle screensaver.

Everything drives the public API offscreen (term=None) or through synthetic
widgets.Ev the way Desk.run would, so it stays headless and fast.
"""
import os
import time

import harness as H
import apps
import screensaver
import theme as T
import widgets as W


def _alt_down(d):
    d.dispatch_key(W.Ev(kind="key", key="Alt", press=True, alt=True))


def _alt_up(d):
    d.dispatch_key(W.Ev(kind="key", key="Alt", press=False))


def _tab(d, shift=False):
    d.dispatch_key(W.Ev(kind="key", key="Tab", alt=True, shift=shift))


class FakeTerm:
    """A non-None term for the screensaver's headless guard (never written)."""
    fd = -1


# ── _norm_key: releases dropped, Alt press/release surfaced ──────────────────
def test_norm_key_event_types():
    d = H.make_desk()
    # a normal key release (evt=3) is swallowed so it can't double-type
    assert d._norm_key({"key": "a", "mods": 1, "text": "a", "evt": 3}) is None
    e = d._norm_key({"key": "a", "mods": 1, "text": "a", "evt": 1})
    assert e is not None and e.key == "a" and e.text == "a"
    # a bare key with no evt (browse dicts) is a press by default
    assert d._norm_key({"key": "b", "mods": 1, "text": "b"}).key == "b"
    # the Alt key surfaces both edges
    down = d._norm_key({"key": chr(57443), "mods": 3, "evt": 1})
    up = d._norm_key({"key": chr(57443), "mods": 1, "evt": 3})
    assert down.key == "Alt" and down.press is True
    assert up.key == "Alt" and up.press is False
    # other bare modifiers stay filtered
    assert d._norm_key({"key": chr(57442), "mods": 1, "evt": 1}) is None


# ── Alt+Tab cycles forward and commits the selection on Alt release ──────────
def test_alt_tab_forward_and_commit():
    d = H.make_desk((1024, 768))
    for _ in range(3):
        apps.open(d, "notepad", None)
    a, b, c = d.wm.windows          # launch order; c is active/topmost
    assert d.wm.active is c
    _alt_down(d)
    _tab(d)                         # one step → the previous window (b)
    assert d.switcher is not None and d.switcher["sel"] == 1
    _alt_up(d)
    assert d.switcher is None and d.wm.active is b, d.wm.active.title
    # two steps commits to the one before that
    _alt_down(d)
    _tab(d)
    _tab(d)
    _alt_up(d)
    assert d.wm.active is a


# ── Shift+Tab reverses ───────────────────────────────────────────────────────
def test_alt_shift_tab_reverses():
    d = H.make_desk((1024, 768))
    for _ in range(3):
        apps.open(d, "notepad", None)
    wins = d.wm.switch_list()       # [active, ...] top-first
    _alt_down(d)
    _tab(d, shift=True)             # backward from the active one → last entry
    sel = d.switcher["sel"]
    _alt_up(d)
    assert d.wm.active is wins[sel] and sel == len(wins) - 1


# ── robust with 0 and 1 windows ──────────────────────────────────────────────
def test_alt_tab_edge_counts():
    d = H.make_desk((1024, 768))
    _alt_down(d)
    _tab(d)                         # no windows: no overlay, no crash
    assert d.switcher is None
    _alt_up(d)
    apps.open(d, "notepad", None)
    only = d.wm.active
    _alt_down(d)
    _tab(d)
    assert d.switcher is not None and d.switcher["sel"] == 0
    _alt_up(d)
    assert d.wm.active is only


# ── Alt+Esc cycles with no overlay ───────────────────────────────────────────
def test_alt_esc_cycles():
    d = H.make_desk((1024, 768))
    for _ in range(2):
        apps.open(d, "notepad", None)
    top = d.wm.active
    d.dispatch_key(W.Ev(kind="key", key="Escape", alt=True))
    assert d.switcher is None and d.wm.active is not top


# ── the overlay renders without error ────────────────────────────────────────
def test_switcher_renders():
    d = H.make_desk((640, 480))
    for _ in range(2):
        apps.open(d, "notepad", None)
    _alt_down(d)
    _tab(d)
    d.render()                      # composes the overlay onto the framebuffer
    assert d.switcher is not None
    _alt_up(d)


# ── tooltip_at returns the title over the title bar, nothing in the client ───
def test_window_tooltip_at():
    d = H.make_desk((1024, 768))
    apps.open(d, "notepad", None)
    np = d.wm.active
    gx = np.x + T.BORDER + 30
    gy = np.y + T.BORDER + 4
    assert np.tooltip_at(gx, gy) == np.title
    cx, cy = np.client_origin()
    assert np.tooltip_at(cx + 5, cy + 30) is None
    # the desk resolves the same tip through the getattr contract
    assert d._tooltip_query(gx, gy) == np.title


# ── screensaver: every saver yields a correctly-sized RGB frame ──────────────
def test_screensaver_frames():
    for cls in screensaver.SAVERS:
        s = cls((320, 240))
        for _ in range(3):
            f = s.step(0.1)
            assert f.size == (320, 240) and f.mode == "RGB"
    f = screensaver.pick((200, 150)).step(0.05)
    assert f.size == (200, 150)


# ── idle → engage, input → exit flips the saving flag (env override) ─────────
def test_screensaver_idle_engage_exit():
    os.environ["KILIX_SAVER_IDLE"] = "0.02"
    try:
        d = H.make_desk((640, 480))
        d.term = FakeTerm()
        assert d.saver_idle == 0.02
        d._last_input = time.time() - 1
        assert d.maybe_start_saver() is True
        assert d.saving and d.saver is not None
        # stepping the live saver produces a full-frame image
        assert d.saver.step(0.1).size == d.size()
        d._wake_saver()
        assert not d.saving and d.saver is None
        # never engages headless (term is None)
        d.term = None
        d._last_input = time.time() - 1
        assert d.maybe_start_saver() is False and not d.saving
    finally:
        os.environ.pop("KILIX_SAVER_IDLE", None)


for _name, _fn in sorted(list(globals().items())):
    if _name.startswith("test_") and callable(_fn):
        _fn()
print("ok")
