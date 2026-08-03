"""Shell.open_in_xpane: an app opens as a desktop window via XPane, and an
XPane/Xvfb failure shows an error dialog instead of crashing the desktop."""
import os

import harness as H
import wm
from apps import xpane


class FakePane(wm.Window):
    """A real window that captures the argv/title it was built with, so wm.add
    genuinely succeeds (a bare stub would raise in wm.add and mask the real path
    with the error-dialog fallback)."""
    made = []

    def __init__(self, desk, argv, title, icon="exe", cwd=None, fill=False,
                 app_size=None, cleanup=None):
        FakePane.made.append((argv, title, icon, cwd, fill))
        super().__init__(desk, title, 400, 300, icon=icon)


def _with_pane(cls, fn):
    orig = xpane.XPane
    xpane.XPane = cls
    try:
        fn()
    finally:
        xpane.XPane = orig


# ── a real argv opens as a window on the desktop ─────────────────────────────
def opens_window():
    FakePane.made = []
    d = H.make_desk()
    n0 = len(d.wm.windows)

    def go():
        d.shell.open_in_xpane(["xterm", "-e", "top"], "xterm", icon="terminal")
    _with_pane(FakePane, go)
    assert len(d.wm.windows) == n0 + 1, "no window added"
    assert isinstance(d.wm.windows[-1], FakePane), \
        "the added window must be the pane itself, not an error dialog"
    argv, title, icon, cwd, fill = FakePane.made[-1]
    assert argv == ["xterm", "-e", "top"], argv
    assert title == "xterm" and icon == "terminal"
    assert cwd, "cwd must default to the home directory"
    assert fill is True, "open_in_xpane must maximize the app to fill the window"


# ── a raising XPane (Xvfb unavailable) shows an error box, no crash ──────────
def failure_shows_msgbox():
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("Xvfb: not found")

    d = H.make_desk()

    def go():
        d.shell.open_in_xpane(["true"], "Broken App")
    _with_pane(Boom, go)             # pre-fix: exception escapes, desk dies
    box = H.find_window(d, "Window")
    assert box is not None and box.modal, "no error dialog"
    assert not any(type(w).__name__ == "XPane" for w in d.wm.windows)


def url_launchers_use_the_real_browser_dispatch():
    d = H.make_desk()
    seen = {}
    d.shell._first_on_path = lambda cands: "/usr/bin/chromium"
    d.shell._tab = lambda argv, title, cwd=None, **kw: seen.update(
        argv=argv, title=title, cwd=cwd, kw=kw) or True

    d.shell.open_url("https://example.invalid/launcher")

    assert os.path.basename(seen["argv"][0]) == "kilix", seen
    assert seen["argv"][1:] == [
        "run", "--fill", "/usr/bin/chromium",
        "https://example.invalid/launcher"], seen
    assert seen["kw"]["env"]["KILIX_IN_OVERLAY"] == "1"


def chromium_defaults_to_filled_run_tab():
    d = H.make_desk()
    seen = {}
    d.shell._first_on_path = lambda cands: "/usr/bin/chromium"
    d.shell._tab = lambda argv, title, cwd=None, **kw: seen.update(
        argv=argv, title=title, cwd=cwd, kw=kw) or True

    d.shell.open_browser("chromium")

    assert seen["argv"][1:] == [
        "run", "--fill", "/usr/bin/chromium",
        d.shell.BROWSER_HOME], seen
    assert seen["kw"]["env"]["KILIX_IN_OVERLAY"] == "1"


def chromium_window_mode_uses_a_private_profile():
    d = H.make_desk()
    seen = {}
    d.shell._first_on_path = lambda cands: "/usr/bin/chromium"
    d.shell.open_in_xpane = lambda argv, title, **kw: seen.update(
        argv=argv, title=title, kw=kw) or True

    d.shell.open_browser("chromium", "window")

    profile_arg = next(
        arg for arg in seen["argv"] if arg.startswith("--user-data-dir="))
    profile = profile_arg.split("=", 1)[1]
    assert os.path.isdir(profile)
    seen["kw"]["cleanup"]()
    assert not os.path.exists(profile)


def copied_gui_launcher_defaults_to_contained_run():
    d = H.make_desk()
    seen = {}
    d.shell.open_x11_tab = lambda argv, title, cwd=None, **kw: seen.update(
        argv=argv, title=title, cwd=cwd, kw=kw) or True
    d.shell.launch({
        "Type": "Application", "Name": "Chromium",
        "Exec": "/usr/bin/chromium %U",
    })
    assert seen["argv"] == ["/usr/bin/chromium"], seen


def copied_terminal_launcher_sanitizes_field_codes():
    d = H.make_desk()
    seen = {}
    d.shell._spawn_kitty_launch = lambda opts, cmd, title, cwd=None: seen.update(
        opts=opts, cmd=cmd, title=title, cwd=cwd) or True
    d.shell.launch({
        "Type": "Application", "Name": "Terminal tool",
        "Exec": "terminal-tool %U --label 'two words'", "Terminal": "true",
    })
    assert seen["cmd"] == "terminal-tool --label 'two words'", seen


opens_window()
failure_shows_msgbox()
url_launchers_use_the_real_browser_dispatch()
chromium_defaults_to_filled_run_tab()
chromium_window_mode_uses_a_private_profile()
copied_gui_launcher_defaults_to_contained_run()
copied_terminal_launcher_sanitizes_field_codes()
print("ok")
