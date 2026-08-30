"""kilix_sdk.xapp lifecycle tests without starting Xvfb or ffmpeg."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock


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

    def start_xvnc(self, number, width, height, port, password_file,
                   desktop="kilix"):
        self.started_vnc = (
            number, width, height, port, password_file, desktop)
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
    def test_display_size_reads_and_closes_the_authenticated_root(self):
        seen = {}

        class Root:
            def get_geometry(self):
                return type("Geometry", (), {"width": 960, "height": 600})()

        class Connection:
            def __init__(self, name):
                seen["name"] = name
                seen["authority"] = os.environ.get("XAUTHORITY")
                self.closed = False

            def screen(self):
                return type("Screen", (), {"root": Root()})()

            def close(self):
                self.closed = True
                seen["closed"] = True

        previous = os.environ.get("XAUTHORITY")
        os.environ["XAUTHORITY"] = "/tmp/original-auth"
        with mock.patch.object(xapp.xdisplay, "Display", Connection):
            try:
                self.assertEqual(
                    xapp.display_size(":77", xauthority="/tmp/private-auth"),
                    (960, 600),
                )
            finally:
                if previous is None:
                    os.environ.pop("XAUTHORITY", None)
                else:
                    os.environ["XAUTHORITY"] = previous
        self.assertEqual(seen["name"], ":77")
        self.assertEqual(seen["authority"], "/tmp/private-auth")
        self.assertTrue(seen["closed"])
        self.assertEqual(os.environ.get("XAUTHORITY"), previous)

    def test_dimensions_display_port_and_capture_inputs_are_validated(self):
        supervisor = FakeSupervisor()
        session = xapp.XAppSession(
            "fixture", 320, 200, supervisor=supervisor)
        for kwargs in ({"width": 0}, {"height": -1}, {"number": -1},
                       {"number": 65536}):
            with self.subTest(xvfb=kwargs):
                with self.assertRaises(ValueError):
                    session.start_xvfb(**kwargs)
        self.assertFalse(hasattr(supervisor, "started"))
        for port in (0, 65536):
            with self.subTest(port=port):
                with self.assertRaises(ValueError):
                    session.start_xvnc(port, "/tmp/test-password")
        self.assertFalse(hasattr(supervisor, "started_vnc"))

        session.start_xvfb()

        class Capture:
            closed = False

            def close(self):
                self.closed = True

        current = Capture()
        session.capture = current
        session.capture_backend = "existing"
        for kwargs in ({"fps": 0}, {"capture_name": "../outside"},
                       {"capture_name": ""}):
            with self.subTest(capture=kwargs):
                with self.assertRaises(ValueError):
                    session.start_capture(**kwargs)
                self.assertIs(session.capture, current)
                self.assertFalse(current.closed)
                self.assertEqual(session.capture_backend, "existing")
        with self.assertRaises(ValueError):
            session.make_injector(width=0)
        with self.assertRaises(ValueError):
            session.set_geometry(0.5, 200)
        session.close()

    def test_capture_pipe_setup_failure_stops_the_spawned_process(self):
        supervisor = FakeSupervisor()
        session = xapp.XAppSession(
            "fixture", 64, 48, supervisor=supervisor)
        session.start_xvfb()
        with mock.patch.object(
                xapp.os, "set_blocking", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                session.start_capture(prefer_damage=False)
        process = supervisor.spawns["cap"][2]
        self.assertTrue(process.terminated)
        self.assertIsNone(session.capture_process)
        self.assertEqual(session.capture_backend, "stopped")
        session.close()

    def test_xauthority_scopes_are_serialized_between_threads(self):
        previous = os.environ.get("XAUTHORITY")
        os.environ["XAUTHORITY"] = "/tmp/original-auth"
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        errors = []

        def first():
            try:
                with xapp._temporary_xauthority("/tmp/first-auth"):
                    first_entered.set()
                    if not release_first.wait(2):
                        raise AssertionError("first scope was not released")
                    self.assertEqual(
                        os.environ.get("XAUTHORITY"), "/tmp/first-auth")
            except BaseException as error:
                errors.append(error)

        def second():
            try:
                if not first_entered.wait(2):
                    raise AssertionError("first scope did not start")
                with xapp._temporary_xauthority("/tmp/second-auth"):
                    second_entered.set()
                    self.assertEqual(
                        os.environ.get("XAUTHORITY"), "/tmp/second-auth")
            except BaseException as error:
                errors.append(error)

        one = threading.Thread(target=first)
        two = threading.Thread(target=second)
        try:
            one.start()
            two.start()
            self.assertTrue(first_entered.wait(2))
            self.assertFalse(second_entered.wait(0.1))
            release_first.set()
            one.join(2)
            two.join(2)
            self.assertFalse(one.is_alive())
            self.assertFalse(two.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(os.environ.get("XAUTHORITY"), "/tmp/original-auth")
        finally:
            release_first.set()
            one.join(2)
            two.join(2)
            if previous is None:
                os.environ.pop("XAUTHORITY", None)
            else:
                os.environ["XAUTHORITY"] = previous

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
