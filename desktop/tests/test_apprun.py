"""config/apprun.py process cleanup regressions, without starting X/ffmpeg."""
import os
from pathlib import Path
import sys
import tempfile
import time

import harness  # noqa: F401  (sets config/ on sys.path)
import app_profiles
import apprun


with tempfile.TemporaryDirectory() as td:
    old_session = os.environ.get("KILIX_SESSION_HOME")
    try:
        os.environ["KILIX_SESSION_HOME"] = td
        command, profile = apprun.prepare_app_command(
            ["chromium", "https://example.invalid"])
        assert command[0] == "chromium"
        assert command[1] == f"--user-data-dir={profile}"
        assert command[-1] == "https://example.invalid"
        assert os.path.isdir(profile)
        apprun.cleanup_app_profile(profile)
        assert not os.path.exists(profile)

        parent = Path(td) / "app-profiles"
        stale = parent / "chromium-99999999-abandoned"
        stale.mkdir()
        old = time.time() - apprun.APP_PROFILE_STALE_SECONDS - 1
        os.utime(stale, (old, old))
        start = app_profiles._process_start(os.getpid())
        live_name = (f"chromium-{os.getpid()}-{start}-live"
                     if start is not None else f"chromium-{os.getpid()}-live")
        live = parent / live_name
        live.mkdir()
        os.utime(live, (old, old))
        reused = parent / f"chromium-{os.getpid()}-1-reused"
        reused.mkdir()
        os.utime(reused, (old, old))
        oversized = parent / f"chromium-{'9' * 100}-oversized"
        oversized.mkdir()
        os.utime(oversized, (old, old))
        apprun.cleanup_stale_app_profiles(parent)
        assert not stale.exists()
        assert live.exists()
        assert not reused.exists()
        assert not oversized.exists()

        explicit = ["chromium", "--user-data-dir=/tmp/explicit"]
        assert apprun.prepare_app_command(explicit) == (explicit, None)
        firefox, firefox_profile = apprun.prepare_app_command(
            ["firefox-esr", "--no-remote", "https://example.invalid"])
        assert firefox[:3] == ["firefox-esr", "--profile", firefox_profile]
        assert firefox.count("--no-remote") == 1
        assert firefox[-1] == "https://example.invalid"
        apprun.cleanup_app_profile(firefox_profile)
        explicit_firefox = ["firefox", "--profile", "/tmp/explicit"]
        assert apprun.prepare_app_command(explicit_firefox) == (
            explicit_firefox, None)
        profile_manager = ["firefox", "--ProfileManager"]
        assert apprun.prepare_app_command(profile_manager) == (
            profile_manager, None)
        cli = ["python3", "tool.py"]
        assert apprun.prepare_app_command(cli) == (cli, None)

        linked_session = Path(td) / "linked-session"
        linked_session.symlink_to(Path(td) / "real-session",
                                  target_is_directory=True)
        (Path(td) / "real-session").mkdir()
        os.environ["KILIX_SESSION_HOME"] = str(linked_session)
        try:
            apprun.prepare_app_command(["chromium"])
            raise AssertionError("symlinked session directory was accepted")
        except RuntimeError as error:
            assert "unsafe GUI session directory" in str(error)
    finally:
        if old_session is None:
            os.environ.pop("KILIX_SESSION_HOME", None)
        else:
            os.environ["KILIX_SESSION_HOME"] = old_session


class FakeStdout:
    def __init__(self):
        self.rfd, self.wfd = os.pipe()
        self.closed = False

    def fileno(self):
        return self.rfd

    def close(self):
        if self.closed:
            return
        self.closed = True
        for fd in (self.rfd, self.wfd):
            try:
                os.close(fd)
            except OSError:
                pass


class FakeProc:
    def __init__(self):
        self.stdout = FakeStdout()
        self.stdin = None
        self.stderr = None
        self._rc = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._rc

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self._rc = 0
        return self._rc

    def kill(self):
        self.killed = True
        self._rc = -9


old = FakeProc()
apprun._stop_proc(old)
assert old.terminated
assert old.stdout.closed

pane = object.__new__(apprun.AppPane)
pane.ff = FakeProc()
old_capture = pane.ff
pane.ffbuf = bytearray(b"partial")
pane.app_w = pane.app_h = 4
pane.disp = ":99"
pane.capture = None

new_capture = FakeProc()
old_damage = os.environ.get("KILIX_XDAMAGE_CAPTURE")


class FakeXApp:
    def __init__(self, old, new):
        self.capture = None
        self.capture_process = old
        self.new = new
        self.geometry = None

    def set_geometry(self, width, height):
        self.geometry = (width, height)

    def stop_capture(self):
        apprun._stop_proc(self.capture_process)
        self.capture = self.capture_process = None

    def start_capture(self, **_kwargs):
        self.capture_process = self.new
        return apprun.xapp_sdk.CaptureStart("ffmpeg@2")


pane.xapp = FakeXApp(old_capture, new_capture)
try:
    os.environ["KILIX_XDAMAGE_CAPTURE"] = "0"
    pane._spawn_capture(2)
    assert old_capture.terminated
    assert old_capture.stdout.closed
    assert pane.ff is new_capture
    assert pane.ffbuf == bytearray()
    assert pane.xapp.geometry == (4, 4)
finally:
    if old_damage is None:
        os.environ.pop("KILIX_XDAMAGE_CAPTURE", None)
    else:
        os.environ["KILIX_XDAMAGE_CAPTURE"] = old_damage
    new_capture.stdout.close()


class FakeAttrs:
    map_state = apprun.X.IsViewable


class FakeGeom:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = x, y, w, h


class FakeChild:
    def __init__(self, wid, geom):
        self.id = wid
        self._geom = geom
        self.configured = None

    def get_attributes(self):
        return FakeAttrs()

    def get_geometry(self):
        return self._geom

    def configure(self, x=None, y=None, width=None, height=None):
        self.configured = (x, y, width, height)
        self._geom = FakeGeom(x, y, width, height)


class FakeRoot:
    def __init__(self, children):
        self._children = children

    def query_tree(self):
        return type("Tree", (), {"children": self._children})()


class FakeXD:
    def __init__(self, children):
        self.root = FakeRoot(children)
        self.focused = None
        self.synced = False

    def screen(self):
        return type("Screen", (), {"root": self.root})()

    def set_input_focus(self, win, *_args):
        self.focused = win

    def sync(self):
        self.synced = True


manager = FakeChild(1, FakeGeom(0, 0, 800, 600))
vm = FakeChild(2, FakeGeom(16, 16, 320, 240))
pane = object.__new__(apprun.AppPane)
pane.app_w, pane.app_h = 800, 600
pane.xd = FakeXD([manager, vm])       # root children: bottom -> top
pane._auto_fit = True
pane._fit_suspended = False
pane._fit_window_id = 1
pane._last_window_fit = 0.0
fake_inputs = []
orig_fake_input = apprun.xtest.fake_input
try:
    apprun.xtest.fake_input = lambda *a, **kw: fake_inputs.append((a, kw))
    pane.maintain_app_window(1.0)
    assert vm.configured == (0, 0, 800, 600), vm.configured
    assert pane.xd.focused is vm
    assert pane._fit_window_id == 2
    assert fake_inputs, "fit must park the pointer inside the newly active window"
finally:
    apprun.xtest.fake_input = orig_fake_input

# A desktop session has its own WM. The host runner must never resize or even
# clamp that WM's taskbar, menus, or managed client windows.
pane = object.__new__(apprun.AppPane)
pane.manage_windows = False
pane.xd = FakeXD([manager, vm])
pane._fit_suspended = False
pane._last_window_fit = 0.0
pane.fit_app_window = lambda *a, **kw: (_ for _ in ()).throw(
    AssertionError("desktop session was fitted"))
pane.clamp_app_windows = lambda: (_ for _ in ()).throw(
    AssertionError("desktop session was clamped"))
pane.focus_app_window()
pane.maintain_app_window(1.0)


class FakeInjector:
    def __init__(self):
        self.keys = []

    def key(self, key, etype):
        self.keys.append((key, etype))


pane = object.__new__(apprun.AppPane)
pane._auto_fit = True
pane._fit_suspended = False
pane.prev_status = "old"
pane.inj = FakeInjector()
fit_calls = []
pane.fit_app_window = lambda force=False: fit_calls.append(force) or True

pane.on_key({"key": "F10", "mods": 1, "event": 1})
assert pane._fit_suspended is True
assert pane.prev_status is None
assert pane.inj.keys == [("F10", 1)]
assert fit_calls == []

pane.on_key({"key": "F10", "mods": 1, "event": 3})
assert pane._fit_suspended is True
assert pane.inj.keys[-1] == ("F10", 3)

pane.on_key({"key": "F10", "mods": 1, "event": 1})
assert pane._fit_suspended is False
assert fit_calls == [True]

pane._auto_fit = False
pane.on_key({"key": "F10", "mods": 1, "event": 1})
assert pane._fit_suspended is False
assert fit_calls == [True]
assert pane.inj.keys[-1] == ("F10", 1)

orig_argv = sys.argv
orig_app_pane = apprun.AppPane
seen = {}


class CapturingAppPane:
    def __init__(self, args, app_w, app_h, fps, **kw):
        seen.update(args=args, app_w=app_w, app_h=app_h, fps=fps, kw=kw)

    def run(self):
        seen["ran"] = True


try:
    apprun.AppPane = CapturingAppPane
    sys.argv = ["apprun.py", "steam"]
    apprun.main()
finally:
    apprun.AppPane = orig_app_pane
    sys.argv = orig_argv

assert seen["args"] == ["steam"]
assert seen["kw"]["auto_fit"] is True
assert seen["kw"]["manage_windows"] is True
assert seen["ran"] is True

seen.clear()
try:
    apprun.AppPane = CapturingAppPane
    sys.argv = ["apprun.py", "--desktop-session", "/bin/desktop-session"]
    apprun.main()
finally:
    apprun.AppPane = orig_app_pane
    sys.argv = orig_argv

assert seen["args"] == ["/bin/desktop-session"]
assert seen["kw"]["auto_fit"] is False
assert seen["kw"]["manage_windows"] is False
assert seen["ran"] is True

print("test_apprun OK")
