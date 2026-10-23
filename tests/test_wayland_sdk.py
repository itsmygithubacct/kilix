"""Nested session policy tests; real ownership is exercised by the app suite."""
import os
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

    def test_launch_without_compositor_refuses(self):
        with wayland.NestedWaylandSession("fixture", exclusive="waydroid-test") as session:
            with self.assertRaisesRegex(RuntimeError, "not running"):
                session.launch_client("client", ["fixture"])


if __name__ == "__main__":
    unittest.main()
