"""kilix_sdk.xapp lifecycle tests without starting Xvfb or ffmpeg."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from kilix_sdk import xapp  # noqa: E402


class FakeStream:
    def __init__(self, fd=None):
        self.fd = fd
        self.closed = False

    def fileno(self):
        return self.fd

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass


class FakeProcess:
    def __init__(self, fd=None):
        self.stdout = FakeStream(fd) if fd is not None else None
        self.stdin = self.stderr = None
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = -9


class FakeSupervisor:
    def __init__(self):
        self.xauth = "/tmp/kilix-xapp-test-auth"
        self.spawns = {}
        self.cleaned = 0
        self.write_fds = []

    def pick_display(self):
        return 77

    def start_xvfb(self, number, width, height, nocursor=False):
        self.started = (number, width, height, nocursor)
        return FakeProcess()

    def spawn(self, name, argv, **kwargs):
        if name.startswith("cap"):
            read_fd, write_fd = os.pipe()
            self.write_fds.append(write_fd)
            process = FakeProcess(read_fd)
        else:
            process = FakeProcess()
        self.spawns[name] = (argv, kwargs, process)
        return process

    def cleanup(self):
        self.cleaned += 1
        for fd in self.write_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.write_fds.clear()


class FakeDisplay:
    def __init__(self, name, seen):
        self.name = name
        self.seen = seen
        self.closed = False

    def close(self):
        self.closed = True


class FakeInjector:
    def __init__(self, _display, app_w, app_h):
        self.app_w, self.app_h = app_w, app_h
        self.released = 0

    def release_all(self):
        self.released += 1


class XAppSessionTests(unittest.TestCase):
    def test_auth_is_scoped_and_private_environment_cannot_be_overridden(self):
        supervisor = FakeSupervisor()
        seen = {}
        previous = os.environ.get("XAUTHORITY")
        os.environ["XAUTHORITY"] = "/tmp/host-auth"
        original_display = xapp.xdisplay.Display
        try:
            def connect(name):
                seen["authority"] = os.environ.get("XAUTHORITY")
                return FakeDisplay(name, seen)

            xapp.xdisplay.Display = connect
            session = xapp.XAppSession(
                "fixture", 320, 200, supervisor=supervisor)
            self.assertEqual(session.start_xvfb(nocursor=True), 77)
            self.assertEqual(supervisor.started, (77, 320, 200, True))
            session.connect()
            self.assertEqual(seen["authority"], supervisor.xauth)
            self.assertEqual(os.environ["XAUTHORITY"], "/tmp/host-auth")
            env = session.environment({
                "DISPLAY": ":1", "XAUTHORITY": "/tmp/wrong", "APP_FLAG": "yes"})
            self.assertEqual(env["DISPLAY"], ":77")
            self.assertEqual(env["XAUTHORITY"], supervisor.xauth)
            self.assertEqual(env["APP_FLAG"], "yes")
            session.close()
        finally:
            xapp.xdisplay.Display = original_display
            if previous is None:
                os.environ.pop("XAUTHORITY", None)
            else:
                os.environ["XAUTHORITY"] = previous

    def test_launch_capture_fallback_and_cleanup_share_one_owner(self):
        supervisor = FakeSupervisor()
        original_display = xapp.xdisplay.Display
        original_damage = xapp.xcapture.XDamageCapture
        original_injector = xapp.xinject.Injector
        display = FakeDisplay(":77", {})
        try:
            xapp.xdisplay.Display = lambda _name: display
            xapp.xcapture.XDamageCapture = lambda *_a, **_kw: (_ for _ in ()).throw(
                xapp.xcapture.CaptureUnavailable("fixture"))
            xapp.xinject.Injector = FakeInjector
            session = xapp.XAppSession(
                "fixture", 64, 48, fps=12, supervisor=supervisor)
            session.start_xvfb()
            session.connect()
            app = session.launch_app(["fixture-app"], env={"APP_FLAG": "1"})
            injector = session.make_injector()
            started = session.start_capture(draw_cursor=False)

            self.assertIs(session.app, app)
            self.assertEqual(started.backend, "ffmpeg@12")
            self.assertIsNotNone(started.damage_error)
            cap_argv, cap_kwargs, capture_process = supervisor.spawns["cap"]
            self.assertIn("64x48", cap_argv)
            self.assertEqual(cap_kwargs["env"]["DISPLAY"], ":77")
            self.assertEqual(cap_kwargs["env"]["XAUTHORITY"], supervisor.xauth)

            session.set_geometry(80, 60)
            self.assertEqual((injector.app_w, injector.app_h), (80, 60))
            session.close()
            session.close()
            self.assertTrue(capture_process.terminated)
            self.assertEqual(injector.released, 1)
            self.assertTrue(display.closed)
            self.assertEqual(supervisor.cleaned, 1)
        finally:
            xapp.xdisplay.Display = original_display
            xapp.xcapture.XDamageCapture = original_damage
            xapp.xinject.Injector = original_injector

    def test_damage_capture_uses_private_xauthority_without_leak(self):
        supervisor = FakeSupervisor()
        seen = {}
        original_damage = xapp.xcapture.XDamageCapture
        previous = os.environ.get("XAUTHORITY")
        os.environ["XAUTHORITY"] = "/tmp/host-auth"

        class FakeDamageCapture:
            def __init__(self, display, width, height, draw_cursor=True):
                seen["init"] = (
                    display, width, height, draw_cursor,
                    os.environ.get("XAUTHORITY"))
                self.closed = False

            def snapshot(self):
                seen["snapshot"] = os.environ.get("XAUTHORITY")
                return b"initial-frame"

            def close(self):
                self.closed = True

        try:
            xapp.xcapture.XDamageCapture = FakeDamageCapture
            session = xapp.XAppSession(
                "fixture", 64, 48, supervisor=supervisor)
            session.start_xvfb()
            started = session.start_capture(draw_cursor=False)

            self.assertEqual(started.backend, "xdamage+mit-shm")
            self.assertEqual(started.initial_frame, b"initial-frame")
            self.assertEqual(
                seen["init"], (":77", 64, 48, False, supervisor.xauth))
            self.assertEqual(seen["snapshot"], supervisor.xauth)
            self.assertEqual(os.environ["XAUTHORITY"], "/tmp/host-auth")
            session.close()
        finally:
            xapp.xcapture.XDamageCapture = original_damage
            if previous is None:
                os.environ.pop("XAUTHORITY", None)
            else:
                os.environ["XAUTHORITY"] = previous

    def test_broadcast_encoder_receives_private_xauthority_without_leak(self):
        with tempfile.TemporaryDirectory() as runtime:
            supervisor = object.__new__(xapp.stream.StreamSupervisor)
            supervisor.runtime_dir = runtime
            supervisor.xauth = "/tmp/private-broadcast-auth"
            seen = {}

            def spawn(name, argv, **kwargs):
                seen.update(name=name, argv=argv, kwargs=kwargs)
                return FakeProcess()

            supervisor.spawn = spawn
            previous = os.environ.get("XAUTHORITY")
            os.environ["XAUTHORITY"] = "/tmp/host-broadcast-auth"
            try:
                supervisor._spawn_enc("fixture", ["ffmpeg"], piped=False)
                self.assertEqual(
                    seen["kwargs"]["env"]["XAUTHORITY"],
                    "/tmp/private-broadcast-auth")
                self.assertEqual(
                    os.environ["XAUTHORITY"], "/tmp/host-broadcast-auth")
            finally:
                handle = seen.get("kwargs", {}).get("stdout")
                if handle is not None:
                    handle.close()
                if previous is None:
                    os.environ.pop("XAUTHORITY", None)
                else:
                    os.environ["XAUTHORITY"] = previous


if __name__ == "__main__":
    unittest.main()
