"""Nested session policy tests; real ownership is exercised by the app suite."""
import os
import socket
import subprocess
import time
from urllib.parse import quote_from_bytes
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from kilix_sdk import privilege, wayland


class PrivilegeTests(unittest.TestCase):
    def test_only_fixed_helper_is_accepted(self):
        for value in ("/tmp/helper", privilege.WAYDROID_SETUP + " --yes", "sudo"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                privilege.run_helper(value, title="Setup")

    def test_declining_never_elevates(self):
        with mock.patch.object(privilege, "_validate"), mock.patch("builtins.input", return_value="no"), mock.patch.object(privilege.subprocess, "run") as run:
            self.assertEqual(privilege._terminal(privilege.WAYDROID_SETUP, "Setup"), 1)
            run.assert_not_called()

    def test_consent_preserves_failure_and_fixed_argv(self):
        with mock.patch.object(privilege, "_validate"), mock.patch("builtins.input", return_value="yes"), mock.patch.object(privilege.subprocess, "run", return_value=SimpleNamespace(returncode=7)) as run:
            self.assertEqual(privilege._terminal(privilege.WAYDROID_SETUP, "Setup"), 7)
            run.assert_called_once_with(["/usr/bin/sudo", "--", privilege.WAYDROID_SETUP], check=False)

    def test_gui_uses_prompt_in_terminal_without_shell(self):
        with mock.patch.object(privilege, "_validate"), mock.patch.object(privilege.sys.stdin, "isatty", return_value=False), mock.patch.dict(os.environ, {"DISPLAY": ":9"}, clear=True), mock.patch.object(privilege.subprocess, "run", return_value=SimpleNamespace(returncode=1)) as run:
            self.assertEqual(privilege.run_helper(privilege.WAYDROID_SETUP, title="Setup"), 1)
            argv = run.call_args.args[0]
            self.assertEqual(argv[:4], ["/usr/bin/xterm", "-T", "Setup", "-e"])
            self.assertEqual(argv[-2:], [privilege.WAYDROID_SETUP, "Setup"])
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_headless_does_not_elevate(self):
        with mock.patch.object(privilege, "_validate"), mock.patch.object(privilege.sys.stdin, "isatty", return_value=False), mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(privilege.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "consent"):
                privilege.run_helper(privilege.WAYDROID_SETUP, title="Setup")
            run.assert_not_called()

    def test_writable_or_symlink_helper_is_rejected(self):
        for mode in (0o100777, 0o120777):
            with self.subTest(mode=mode), mock.patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=mode, st_uid=0)):
                with self.assertRaisesRegex(RuntimeError, "unsafe"):
                    privilege._validate(privilege.WAYDROID_SETUP)


class WaylandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {"KILIX_SESSION_HOME": self.temp.name, "DISPLAY": ":9"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_global_exclusion_and_reacquisition(self):
        with wayland.NestedWaylandSession("fixture", exclusive="waydroid-test") as first:
            runtime = Path(first.supervisor.runtime_dir)
            with self.assertRaises(BlockingIOError):
                with wayland.NestedWaylandSession("fixture", exclusive="waydroid-test"):
                    self.fail("second owner acquired lock")
            self.assertTrue(runtime.is_dir())
        self.assertFalse(runtime.exists())
        with wayland.NestedWaylandSession("fixture", exclusive="waydroid-test"):
            pass

    def test_client_cleanup_precedes_compositor_and_failure_retains_lock(self):
        session = wayland.NestedWaylandSession("fixture", exclusive="waydroid-test")
        session.__enter__()
        self.addCleanup(session.close)
        events = []
        compositor = mock.Mock()
        compositor.close.side_effect = lambda: events.append("compositor")
        client = mock.Mock()
        client.close.side_effect = RuntimeError("incomplete")
        session.owners = [compositor, client]
        with self.assertRaisesRegex(RuntimeError, "incomplete") as caught:
            session.close()
        self.assertIs(caught.exception.resource, session)
        self.assertIsNotNone(session.lock)
        self.assertTrue(Path(session.supervisor.runtime_dir).exists())
        client.close.side_effect = lambda: events.append("client")
        session.close()
        self.assertEqual(events[-2:], ["client", "compositor"])
        self.assertIsNone(session.lock)

    def test_environment_preserves_bus_and_host_audio(self):
        audio = Path(self.temp.name) / "pulse"
        audio.mkdir()
        (audio / "native").touch()
        with mock.patch.dict(os.environ, {"KILIX_SESSION_HOME": self.temp.name, "XDG_RUNTIME_DIR": self.temp.name, "DBUS_SESSION_BUS_ADDRESS": "unix:path=/fixture/bus", "WAYLAND_SOCKET": "12"}, clear=True):
            session = wayland.NestedWaylandSession("fixture", exclusive="waydroid-test")
            try:
                env = session.environment()
                self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/fixture/bus")
                self.assertEqual(env["PULSE_SERVER"], "unix:" + str(audio / "native"))
                self.assertNotIn("WAYLAND_SOCKET", env)
                self.assertEqual(env["XDG_RUNTIME_DIR"], session.supervisor.runtime_dir)
            finally:
                session.close()

    def test_spawn_uses_existing_owner_without_rebinding_bus(self):
        with wayland.NestedWaylandSession("fixture", exclusive="waydroid-test") as session:
            with mock.patch.object(wayland, "PrivateBus") as owner_type, mock.patch.object(session.supervisor, "spawn") as spawn:
                session._spawn("client", ["fixture"])
                self.assertEqual(spawn.call_args.kwargs["_kilix_remote_launcher"], owner_type.return_value.launch_primary)
                owner_type.return_value.bind.assert_called_once_with(spawn.return_value)
                owner_type.return_value.environment.assert_not_called()


    def test_real_runtime_bus_survives_nested_environment(self):
        # Punctuation and UTF-8 bytes must be escaped in an explicit address.
        runtime = Path(self.temp.name) / "runtime %;,=~é"
        runtime.mkdir(mode=0o700)
        endpoint = runtime / "bus"
        address = "unix:path=" + quote_from_bytes(os.fsencode(endpoint), safe="/").replace("~", "%7e")
        daemon = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--nopidfile", "--address=" + address],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "HOME": self.temp.name},
        )
        try:
            deadline = time.monotonic() + 5
            while not endpoint.exists() and daemon.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(daemon.poll(), "temporary bus exited before readiness")
            self.assertTrue(endpoint.is_socket(), "temporary bus did not become ready")
            query = (
                "import dbus; b=dbus.SessionBus(); "
                "print(b.call_blocking('org.freedesktop.DBus', '/org/freedesktop/DBus', "
                "'org.freedesktop.DBus', 'GetId', '', ()))"
            )
            baseline = None
            for supplied in (None, "", address):
                env = {"PATH": "/usr/bin:/bin", "HOME": self.temp.name,
                       "XDG_RUNTIME_DIR": str(runtime), "KILIX_SESSION_HOME": self.temp.name}
                if supplied is not None:
                    env["DBUS_SESSION_BUS_ADDRESS"] = supplied
                with self.subTest(address=supplied), mock.patch.dict(os.environ, env, clear=True):
                    original = subprocess.run([sys.executable, "-c", query], env=env,
                                              text=True, capture_output=True, timeout=5, check=True).stdout.strip()
                    baseline = baseline or original
                    self.assertEqual(original, baseline)
                    with wayland.NestedWaylandSession("bus-fixture", exclusive="waydroid-test") as session:
                        nested_env = session.environment()
                        self.assertNotEqual(nested_env["XDG_RUNTIME_DIR"], str(runtime))
                        if supplied:
                            self.assertEqual(nested_env["DBUS_SESSION_BUS_ADDRESS"], supplied)
                        nested = subprocess.run([sys.executable, "-c", query], env=nested_env,
                                                text=True, capture_output=True, timeout=5, check=True).stdout.strip()
                        self.assertEqual(nested, baseline)
                        self.assertTrue(nested_env["DBUS_SESSION_BUS_ADDRESS"])
        finally:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)
            daemon.stderr.close()

    def test_session_bus_resolution_rejects_missing_non_socket_and_other_runtime(self):
        runtime = Path(self.temp.name) / "runtime"
        runtime.mkdir()
        for supplied in (None, ""):
            env = {"XDG_RUNTIME_DIR": str(runtime)}
            if supplied is not None:
                env["DBUS_SESSION_BUS_ADDRESS"] = supplied
            self.assertEqual(wayland.session_bus_address(env), "")
            (runtime / "bus").touch()
            self.assertEqual(wayland.session_bus_address(env), "")
            (runtime / "bus").unlink()
        with socket.socket(socket.AF_UNIX) as endpoint:
            endpoint.bind(str(runtime / "bus"))
            self.assertEqual(wayland.session_bus_address({}), "")
            self.assertEqual(wayland.session_bus_address({"XDG_RUNTIME_DIR": "relative"}), "")
            self.assertEqual(wayland.session_bus_address({"XDG_RUNTIME_DIR": str(runtime / "other")}), "")
        explicit = "unix:abstract=explicit%2cbus;unix:path=/fallback"
        self.assertEqual(wayland.session_bus_address({"DBUS_SESSION_BUS_ADDRESS": explicit}), explicit)

    def test_launch_without_compositor_refuses(self):
        with wayland.NestedWaylandSession("fixture", exclusive="waydroid-test") as session:
            with self.assertRaisesRegex(RuntimeError, "not running"):
                session.launch_client("client", ["fixture"])


if __name__ == "__main__":
    unittest.main()
