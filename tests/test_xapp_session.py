"""kilix_sdk.xapp lifecycle tests without starting Xvfb or ffmpeg."""

from __future__ import annotations

import os
import shlex
import subprocess
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
    def test_local_pane_publication_is_independent_of_clipboard_and_closes_first(self):
        supervisor=FakeSupervisor()
        session=xapp.XAppSession('capture-publication',640,480,supervisor=supervisor)
        publication=mock.Mock()
        with mock.patch.object(xapp.capture_registry,'publish',return_value=publication) as publish:
            session.start_xvfb()
            session.launch_app(['/bin/app'],clipboard=False,capture_label='Visible application')
            publish.assert_called_once_with(session,'Visible application')
        publication.close.side_effect=lambda:self.assertEqual(supervisor.cleaned,0)
        session.close();session.close()
        publication.close.assert_called_once()
        self.assertEqual(supervisor.cleaned,1)

    def test_network_or_explicitly_excluded_apps_do_not_publish_capture_sources(self):
        for local,enabled in ((False,True),(True,False)):
            session=xapp.XAppSession('excluded-publication',640,480,supervisor=FakeSupervisor())
            if local:session.start_xvfb()
            else:session.start_xvnc(5901,'/private/password')
            with mock.patch.object(xapp.capture_registry,'publish') as publish:
                session.launch_app(['/bin/app'],clipboard=False,capture_source=enabled)
                publish.assert_not_called()
            session.close()
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

    def test_private_app_cannot_escape_to_host_display_or_remote_control(self):
        supervisor = FakeSupervisor()
        session = xapp.XAppSession("private", 640, 480, supervisor=supervisor)
        session.start_xvfb()
        inherited = {"WAYLAND_DISPLAY": "wayland-0", "KITTY_WINDOW_ID": "3",
                     "KITTY_LISTEN_ON": "unix:@host", "KITTY_PID": "123",
                     "KILIX_IN_OVERLAY": "1", "KILIX_RC_PASSWORD_FILE": "/host",
                     "DBUS_SESSION_BUS_ADDRESS": "unix:path=/host/bus"}
        with mock.patch.dict(os.environ, inherited), mock.patch.object(
                xapp.shutil, "which", return_value="/usr/bin/dbus-run-session"):
            session.launch_app(["fixture-app", "two words"], isolate_bus=True)
        argv, kwargs, _ = supervisor.spawns["app"]
        self.assertEqual(argv, ["/usr/bin/dbus-run-session", "--", xapp.sys.executable,
                               str(Path(xapp.__file__).with_name("portal_bridge.py")),
                               "--wrap", "--", "fixture-app", "two words"])
        env = kwargs["env"]
        for key in inherited:
            self.assertNotIn(key, env)
        self.assertEqual(env["DISPLAY"], ":77")
        self.assertEqual(env["KILIX_RUN_ALIASES"], "0")
        self.assertEqual(env["KILIX_PRIVATE_XAPP"], "1")
        self.assertEqual(env["GDK_BACKEND"], "x11")
        self.assertEqual(env["KILIX_PORTAL_HOST_BUS"], "unix:path=/host/bus")
        self.assertEqual(env["GTK_USE_PORTAL"], "1")

    def test_private_portal_uses_physical_bus_and_headless_apps_need_no_bridge(self):
        supervisor = FakeSupervisor()
        session = xapp.XAppSession("portals", 640, 480, supervisor=supervisor)
        session.start_xvfb()
        with mock.patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/outer/private",
                                         "PLEB_DESKTOP_BUS_ADDRESS": "unix:path=/physical"}, clear=True), \
                mock.patch.object(xapp.shutil, "which", return_value="/usr/bin/dbus-run-session"):
            session.launch_app(["fixture-app"], isolate_bus=True)
        self.assertEqual(supervisor.spawns["app"][1]["env"]["KILIX_PORTAL_HOST_BUS"],
                         "unix:path=/physical")
        session.close()
        supervisor = FakeSupervisor()
        session = xapp.XAppSession("headless", 640, 480, supervisor=supervisor)
        session.start_xvfb()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
                xapp.shutil, "which", return_value="/usr/bin/dbus-run-session"):
            session.launch_app(["fixture-app"], isolate_bus=True)
        self.assertEqual(supervisor.spawns["app"][0],
                         ["/usr/bin/dbus-run-session", "--", "fixture-app"])
        session.close()

    def test_explicit_private_gtk_chooser_policy_is_preserved(self):
        supervisor = FakeSupervisor()
        session = xapp.XAppSession("chooser-policy", 640, 480, supervisor=supervisor)
        session.start_xvfb()
        with mock.patch.dict(os.environ, {"PLEB_DESKTOP_BUS_ADDRESS": "unix:path=/physical"}, clear=True), \
                mock.patch.object(xapp.shutil, "which", return_value="/usr/bin/dbus-run-session"):
            session.launch_app(["fixture-app"], env={"GTK_USE_PORTAL": "0"}, isolate_bus=True)
        self.assertEqual(supervisor.spawns["app"][1]["env"]["GTK_USE_PORTAL"], "0")
        session.close()

    def test_private_wm_is_supervised_and_ready_before_app_launch(self):
        supervisor = FakeSupervisor()
        session = xapp.XAppSession("wm", 640, 480, supervisor=supervisor)
        session.start_xvfb()
        xd = mock.Mock()
        xd.screen.return_value.root.get_full_property.side_effect = [
            None, mock.Mock(value=[99])]
        def finish_startup(_delay):
            argv = supervisor.spawns["wm"][0]
            callback = shlex.split(argv[argv.index("--startup") + 1])
            subprocess.run(callback, check=True)
        with mock.patch.dict(os.environ, {"KILIX_RUN_WM": "openbox"}), \
                mock.patch.object(xapp.shutil, "which", return_value="/usr/bin/openbox"), \
                mock.patch.object(session, "connect", return_value=xd), \
                mock.patch.object(xapp.time, "sleep", side_effect=finish_startup):
            self.assertTrue(session.start_window_manager())
        argv, kwargs, process = supervisor.spawns["wm"]
        self.assertEqual(argv[0], "/usr/bin/openbox")
        self.assertIn("--sm-disable", argv)
        self.assertNotIn("openbox-session", argv)
        self.assertTrue(Path(argv[2]).is_file())
        self.assertEqual(kwargs["env"]["DISPLAY"], ":77")
        self.assertIs(session.window_manager, process)

    def test_private_wm_property_alone_does_not_mean_ready(self):
        session = xapp.XAppSession("early", 640, 480, supervisor=FakeSupervisor())
        session.start_xvfb()
        xd = mock.Mock()
        xd.screen.return_value.root.get_full_property.return_value = mock.Mock(value=[99])
        with mock.patch.dict(os.environ, {"KILIX_RUN_WM": "openbox"}), \
                mock.patch.object(xapp.shutil, "which", return_value="/usr/bin/openbox"), \
                mock.patch.object(session, "connect", return_value=xd):
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                session.start_window_manager(timeout=0.05)
        self.assertIsNone(session.app)

    def test_private_wm_timeout_fails_instead_of_launching_unmanaged_app(self):
        session = xapp.XAppSession("timeout", 640, 480, supervisor=FakeSupervisor())
        session.start_xvfb()
        with mock.patch.dict(os.environ, {"KILIX_RUN_WM": "openbox"}), \
                mock.patch.object(xapp.shutil, "which", return_value="/usr/bin/openbox"), \
                mock.patch.object(session, "connect", return_value=mock.Mock()):
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                session.start_window_manager(timeout=0)
        self.assertIsNone(session.app)

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

    def start_clipboard_with_log(self, runtime):
        supervisor = FakeSupervisor()
        supervisor.runtime_dir = runtime
        session = xapp.XAppSession("clipboard-log", 64, 48, supervisor=supervisor)
        session.start_xvfb()
        with mock.patch.dict(os.environ, {"KILIX_HOST_CLIP": "1", "PLEB_DESKTOP_DISPLAY": ":0"}):
            session.start_clipboard(timeout=0.01)
        argv, kwargs, _process = supervisor.spawns["clipboard"]
        self.assertIn("--private-display", argv)
        return kwargs["stderr"]

    def test_clipboard_log_is_created_owner_only_whatever_the_umask(self):
        with tempfile.TemporaryDirectory() as runtime:
            previous = os.umask(0)
            try:
                # Creation itself must be private: no window between a
                # umask-mode create and a later chmod.
                with mock.patch.object(xapp.os, "chmod"), mock.patch.object(xapp.os, "fchmod"):
                    self.start_clipboard_with_log(runtime)
            finally:
                os.umask(previous)
            self.assertEqual((Path(runtime) / "clipboard.log").stat().st_mode & 0o777, 0o600)

    def test_clipboard_log_never_follows_a_planted_link(self):
        with tempfile.TemporaryDirectory() as runtime, tempfile.TemporaryDirectory() as elsewhere:
            target = Path(elsewhere) / "victim"
            (Path(runtime) / "clipboard.log").symlink_to(target)
            stderr = self.start_clipboard_with_log(runtime)
            self.assertFalse(target.exists())
            self.assertEqual(stderr, subprocess.DEVNULL)

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
