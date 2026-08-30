"""kilix_sdk.wayland lifecycle tests without starting Weston."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from kilix_sdk import wayland  # noqa: E402


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.stdin = self.stdout = self.stderr = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class FakeSupervisor:
    def __init__(self, root: str, *, socket_on_start=True, weston_rc=None):
        self.runtime_dir = os.path.join(root, "runtime")
        self.lockdir = os.path.join(root, "locks")
        os.makedirs(self.runtime_dir, mode=0o700)
        os.makedirs(self.lockdir, mode=0o700)
        self.socket_on_start = socket_on_start
        self.weston_rc = weston_rc
        self.spawns = []
        self.sockets = []
        self.events = []
        self.cleaned = 0

    def spawn(self, name, argv, **kwargs):
        index = sum(1 for item in self.spawns if item[0] == "weston")
        weston_rc = (
            self.weston_rc[index]
            if isinstance(self.weston_rc, (list, tuple))
            else self.weston_rc
        )
        socket_on_start = (
            self.socket_on_start[index]
            if isinstance(self.socket_on_start, (list, tuple))
            else self.socket_on_start
        )
        process = FakeProcess(weston_rc if name == "weston" else None)
        self.spawns.append((name, list(argv), kwargs, process))
        if name == "weston" and socket_on_start:
            endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            endpoint.bind(os.path.join(self.runtime_dir, "wayland-0"))
            self.sockets.append(endpoint)
        if name == "weston" and weston_rc is not None:
            output = kwargs.get("stdout")
            output.write(b"fixture compositor failure\n")
            output.flush()
        return process

    def cleanup(self):
        self.events.append("cleanup")
        self.cleaned += 1
        for endpoint in self.sockets:
            endpoint.close()
        self.sockets.clear()
        for _name, _argv, kwargs, _process in self.spawns:
            output = kwargs.get("stdout")
            if output is not None and hasattr(output, "close"):
                output.close()


class WaylandSessionTests(unittest.TestCase):
    def test_real_supervisor_owns_socket_client_and_shutdown(self):
        fixture_source = textwrap.dedent("""\
            #!/usr/bin/env python3
            import os
            import signal
            import socket
            import sys
            import time

            socket_name = next(
                value.split("=", 1)[1]
                for value in sys.argv[1:]
                if value.startswith("--socket=")
            )
            endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            endpoint.bind(os.path.join(os.environ["XDG_RUNTIME_DIR"], socket_name))
            endpoint.listen(1)
            running = True

            def stop(_signum, _frame):
                global running
                running = False

            signal.signal(signal.SIGTERM, stop)
            while running:
                time.sleep(0.02)
            endpoint.close()
        """)
        with tempfile.TemporaryDirectory() as temporary:
            compositor = Path(temporary, "fixture-weston")
            compositor.write_text(fixture_source, encoding="utf-8")
            compositor.chmod(
                compositor.stat().st_mode | stat.S_IXUSR
            )
            environment = {
                "DISPLAY": ":77",
                "KILIX_SESSION_HOME": os.path.join(temporary, "session"),
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                session = wayland.NestedWaylandSession(
                    "real-fixture", exclusive="real-fixture"
                )
                weston = session.start_x11(
                    640, 480, executable=str(compositor), timeout=2
                )
                client = session.launch_client(
                    "client", ["/bin/sleep", "30"]
                )
                self.assertTrue(stat.S_ISSOCK(os.stat(session.socket_path).st_mode))
                session.close()

            self.assertIsNotNone(weston.poll())
            self.assertIsNotNone(client.poll())
            self.assertFalse(Path(session.runtime_dir).exists())

    def test_start_uses_cross_version_x11_contract_and_private_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            host_runtime = os.path.join(temporary, "host-runtime")
            os.makedirs(os.path.join(host_runtime, "pulse"))
            Path(host_runtime, "pipewire-0").touch()
            supervisor = FakeSupervisor(temporary)
            with mock.patch.dict(os.environ, {
                "DISPLAY": ":77",
                "XDG_RUNTIME_DIR": host_runtime,
                "WAYLAND_DISPLAY": "parent-wayland",
                "WAYLAND_SOCKET": "9",
                "WESTON_CONFIG_FILE": "/tmp/untrusted.ini",
            }, clear=True):
                session = wayland.NestedWaylandSession(
                    "fixture", supervisor=supervisor)
                compositor = session.start_x11(
                    960, 600, executable="/bin/true", timeout=1)

            self.assertIs(compositor, supervisor.spawns[0][3])
            name, argv, kwargs, _process = supervisor.spawns[0]
            self.assertEqual(name, "weston")
            self.assertEqual(argv[0], "/bin/true")
            self.assertIn("--no-config", argv)
            self.assertIn("--socket=wayland-0", argv)
            self.assertIn("--width=960", argv)
            self.assertIn("--height=600", argv)
            self.assertIn("--idle-time=0", argv)
            self.assertNotIn("--use-pixman", argv)
            self.assertFalse(any("backend" in value for value in argv))
            self.assertEqual(session.renderer, "gl")
            environment = kwargs["env"]
            self.assertEqual(environment["DISPLAY"], ":77")
            self.assertEqual(environment["XDG_RUNTIME_DIR"], session.runtime_dir)
            self.assertEqual(
                environment["PULSE_RUNTIME_PATH"],
                os.path.join(host_runtime, "pulse"),
            )
            self.assertEqual(environment["PIPEWIRE_RUNTIME_DIR"], host_runtime)
            self.assertNotIn("WAYLAND_DISPLAY", environment)
            self.assertNotIn("WAYLAND_SOCKET", environment)
            self.assertNotIn("WESTON_CONFIG_FILE", environment)
            session.close()
            self.assertEqual(supervisor.cleaned, 1)

    def test_auto_renderer_retries_pixman_after_gl_startup_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = FakeSupervisor(
                temporary,
                socket_on_start=(False, True),
                weston_rc=(4, None),
            )
            with mock.patch.dict(os.environ, {"DISPLAY": ":1"}, clear=True):
                session = wayland.NestedWaylandSession(
                    "fixture", supervisor=supervisor)
                compositor = session.start_x11(
                    640, 480, executable="/bin/true", timeout=1)

            self.assertIs(compositor, supervisor.spawns[1][3])
            self.assertNotIn("--use-pixman", supervisor.spawns[0][1])
            self.assertIn("--use-pixman", supervisor.spawns[1][1])
            self.assertEqual(session.renderer, "pixman")
            session.close()

    def test_client_cannot_redirect_the_owned_wayland_socket(self):
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = FakeSupervisor(temporary)
            with mock.patch.dict(os.environ, {"DISPLAY": ":1"}, clear=True):
                session = wayland.NestedWaylandSession(
                    "fixture", supervisor=supervisor)
                session.start_x11(640, 480, executable="/bin/true", timeout=1)
                client = session.launch_client(
                    "client",
                    ["fixture-client"],
                    env={
                        "XDG_RUNTIME_DIR": "/tmp/wrong",
                        "WAYLAND_DISPLAY": "wrong",
                        "WAYLAND_SOCKET": "8",
                        "FIXTURE": "yes",
                    },
                )

            _name, _argv, kwargs, _process = supervisor.spawns[1]
            environment = kwargs["env"]
            self.assertEqual(environment["XDG_RUNTIME_DIR"], session.runtime_dir)
            self.assertEqual(environment["WAYLAND_DISPLAY"], "wayland-0")
            self.assertEqual(environment["XDG_SESSION_TYPE"], "wayland")
            self.assertEqual(environment["FIXTURE"], "yes")
            self.assertNotIn("WAYLAND_SOCKET", environment)
            client.returncode = 7
            self.assertEqual(
                session.wait(client, poll_interval=0.001),
                wayland.SessionExit("client", 7),
            )
            session.close()

    def test_compositor_exit_is_reported_and_cleanup_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = FakeSupervisor(
                temporary, socket_on_start=False, weston_rc=4)
            with mock.patch.dict(os.environ, {"DISPLAY": ":1"}, clear=True):
                session = wayland.NestedWaylandSession(
                    "fixture", supervisor=supervisor)
                with self.assertRaisesRegex(
                    wayland.WaylandSessionError,
                    r"gl renderer exited with rc=4: fixture compositor failure",
                ):
                    session.start_x11(
                        640, 480, executable="/bin/true", timeout=1,
                        renderer="gl")
            session.close()
            self.assertEqual(supervisor.cleaned, 1)

    def test_missing_display_names_dimensions_and_timeout_are_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ("", "../escape", "has/slash"):
                with self.subTest(name=name):
                    with self.assertRaises(ValueError):
                        wayland.NestedWaylandSession(
                            name, supervisor=FakeSupervisor(temporary + name.replace("/", "_")))

        for width, height in ((0, 10), (10, 0), (16385, 10)):
            with self.subTest(size=(width, height)), \
                    tempfile.TemporaryDirectory() as temporary:
                session = wayland.NestedWaylandSession(
                    "fixture", supervisor=FakeSupervisor(temporary))
                with self.assertRaises(ValueError):
                    session.start_x11(
                        width, height, executable="/bin/true", timeout=1)
                session.close()

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(os.environ, {}, clear=True):
            session = wayland.NestedWaylandSession(
                "fixture", supervisor=FakeSupervisor(temporary))
            with self.assertRaisesRegex(
                wayland.WaylandSessionError, "an X DISPLAY is required"):
                session.start_x11(640, 480, executable="/bin/true", timeout=1)
            session.close()

        with tempfile.TemporaryDirectory() as temporary:
            session = wayland.NestedWaylandSession(
                "fixture", supervisor=FakeSupervisor(temporary))
            with self.assertRaises(ValueError):
                session.start_x11(
                    640, 480, executable="/bin/true", renderer="unknown")
            session.close()

    def test_exclusive_identity_blocks_only_while_the_owner_is_live(self):
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.dict(
                    os.environ,
                    {"KILIX_SESSION_HOME": os.path.join(temporary, "session")},
                    clear=False,
                ):
            first = wayland.NestedWaylandSession(
                "fixture-one", exclusive="android")
            with self.assertRaises(wayland.WaylandSessionBusy):
                wayland.NestedWaylandSession(
                    "fixture-two", exclusive="android")
            first.close()
            third = wayland.NestedWaylandSession(
                "fixture-three", exclusive="android")
            third.close()

    def test_close_stops_clients_before_the_compositor_supervisor(self):
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = FakeSupervisor(temporary)
            with mock.patch.dict(os.environ, {"DISPLAY": ":1"}, clear=True):
                session = wayland.NestedWaylandSession(
                    "fixture", supervisor=supervisor)
                session.start_x11(640, 480, executable="/bin/true", timeout=1)
                client = session.launch_client("client", ["fixture-client"])
            session.close()
            self.assertTrue(client.terminated)
            self.assertEqual(supervisor.events, ["cleanup"])


if __name__ == "__main__":
    unittest.main()
