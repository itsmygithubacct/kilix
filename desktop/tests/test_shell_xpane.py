"""Shell.open_in_xpane: an app opens as a desktop window via XPane, and an
XPane/Xvfb failure shows an error dialog instead of crashing the desktop."""
import harness as H
import wm
from apps import xpane


class FakePane(wm.Window):
    """A real window that captures the argv/title it was built with, so wm.add
    genuinely succeeds (a bare stub would raise in wm.add and mask the real path
    with the error-dialog fallback)."""
    made = []

    def __init__(self, desk, argv, title, icon="exe", cwd=None, fill=False,
                 app_size=None):
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


opens_window()
failure_shows_msgbox()
print("ok")
