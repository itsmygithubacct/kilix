"""apps/xpane.py regression tests. No Xvfb/ffmpeg: XPane instances are built
with object.__new__ and driven with fake procs/pipes and a fake Xlib tree.
Each assertion fails on the pre-fix code path."""
import os

import harness as H          # noqa: sets up sys.path for the imports below
import theme as T
from apps import xpane
from Xlib import X


class FakeStdout:
    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd


class FakeProc:
    """Stands in for a Popen: poll() returns rc, .stdout wraps a real pipe fd."""
    def __init__(self, rc=None, fd=None):
        self._rc = rc
        self.stdout = FakeStdout(fd) if fd is not None else None

    def poll(self):
        return self._rc


def bare_pane(desk, ff):
    """A minimally-populated XPane for _pump: no chrome, no X connection."""
    p = object.__new__(xpane.XPane)
    p.desk = desk
    p.dirty = False
    p.ff = ff
    p.buf = bytearray()
    p.app_w = p.app_h = 4
    p.fsize = 4 * 4 * 3
    p.frame_img = None
    p.compose_mask = None
    p._last_frame = None
    return p


# ── F37/F25: a capture (ffmpeg) EOF must unregister the fd, or Desk.run's
# select() spins at 100% CPU on the permanently-readable dead pipe ────────────
d = H.make_desk()
rfd, wfd = os.pipe()
os.set_blocking(rfd, False)
pane = bare_pane(d, FakeProc(fd=rfd))
d.add_fd(rfd, pane._pump)
os.close(wfd)                         # EOF on the read end
assert rfd in d.fd_hooks
pane._pump()
assert rfd not in d.fd_hooks, "EOF must remove the fd hook (else select spins)"
os.close(rfd)


# ── F25: with the fd gone, _tick must also close the pane when ffmpeg dies
# while the app lives (nothing else tears a capture-dead pane down) ───────────
pane = object.__new__(xpane.XPane)
pane._dead = False
pane.app = FakeProc(rc=None)          # app still running
pane.ff = FakeProc(rc=0)              # capture exited
closed = []
pane.close = lambda: closed.append(1)
pane._keep_on_screen = lambda: closed.append("kept")   # must NOT be reached
pane._tick(0.0)
assert closed == [1], "capture death must close the pane, not keep it alive"

# both alive: heal position, do not close
pane = object.__new__(xpane.XPane)
pane._dead = False
pane.app = FakeProc(rc=None)
pane.ff = FakeProc(rc=None)
log = []
pane.close = lambda: log.append("closed")
pane._keep_on_screen = lambda: log.append("kept")
pane._tick(0.0)
assert log == ["kept"], "a healthy pane must not close"


# ── F38: byte-identical frames must not invalidate — otherwise an idle media
# player forces a full-desktop recomposite/retransmit at capture fps ─────────
d = H.make_desk()
rfd, wfd = os.pipe()
os.set_blocking(rfd, False)
pane = bare_pane(d, FakeProc(fd=rfd))

fa = bytes([10]) * pane.fsize
d.dirty = False
os.write(wfd, fa)
pane._pump()
assert d.dirty is True, "first frame must invalidate"
assert pane._last_frame == fa
assert pane.frame_img is not None

d.dirty = False
os.write(wfd, fa)                     # identical frame
pane._pump()
assert d.dirty is False, "identical frame must not invalidate"

d.dirty = False
fb = bytes([20]) * pane.fsize
os.write(wfd, fb)                     # a genuinely new frame
pane._pump()
assert d.dirty is True, "a changed frame must invalidate"
os.close(rfd)
os.close(wfd)


# ── F30: an exception mid-__init__ (after the supervisor exists) must call
# sup.cleanup() and re-raise — else Xvfb/app/display-lock leak per retry ─────
class FakeSup:
    instances = []

    def __init__(self, session):
        self.session = session
        self.xauth = "/tmp/kilix-test-xauth"
        self.cleaned = False
        FakeSup.instances.append(self)

    def pick_display(self):
        raise RuntimeError("kilix: no free display")

    def cleanup(self, *_):
        self.cleaned = True


d = H.make_desk()
orig_sup = xpane.stream.StreamSupervisor
xpane.stream.StreamSupervisor = FakeSup
try:
    raised = False
    try:
        xpane.XPane(d, ["true"], "T")
    except RuntimeError:
        raised = True
    assert raised, "constructor must propagate the failure"
    assert len(FakeSup.instances) == 1
    assert FakeSup.instances[0].cleaned is True, "sup.cleanup must run on failure"
    assert not any(isinstance(w, xpane.XPane) for w in d.wm.windows)
finally:
    xpane.stream.StreamSupervisor = orig_sup


# ── F43: _keep_on_screen must clamp against the CURRENT visible region, not
# the stale capture size, or windows parked past a shrunk edge stay
# mouse-unreachable ──────────────────────────────────────────────────────────
class FakeGeom:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = x, y, w, h


class FakeChild:
    def __init__(self, x, y, w, h):
        self._g = FakeGeom(x, y, w, h)
        self.configured = None

    def get_attributes(self):
        return type("A", (), {"map_state": X.IsViewable})()

    def get_geometry(self):
        return self._g

    def configure(self, x=None, y=None):
        self.configured = (x, y)


class FakeXD:
    def __init__(self, children):
        self._children = children

    def screen(self):
        root = type("R", (), {
            "query_tree": lambda s: type("T", (), {"children": self._children})()
        })()
        return type("S", (), {"root": root})()

    def sync(self):
        pass


d = H.make_desk(size=(640, 480))      # shrunk from a 1024-wide creation size
pane = object.__new__(xpane.XPane)
pane.app_w, pane.app_h = 1024, 740    # stale capture geometry
pane.desk = d
child = FakeChild(890, 300, 120, 100)  # visible in 1024, off the 640 screen
pane.xd = FakeXD([child])
pane._keep_on_screen()
# vw = min(1024, 640) = 640 -> nx = min(890, 640-120) = 520; y already fits
assert child.configured == (520, 300), child.configured

print("test_xpane OK")
