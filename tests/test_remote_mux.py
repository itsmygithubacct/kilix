import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
REMOTE_MUX = importlib.import_module("remote_mux")


class RemoteMultiplexerTests(unittest.TestCase):

    def test_broker_session_is_strictly_hex_and_bounded(self):
        valid = "0123456789abcdef0123456789abcdef"
        self.assertEqual(REMOTE_MUX.validated_session(valid), valid)
        for invalid in (
                "", "short", "0123456789abcdeg",
                "../0123456789abcdef", "a" * 65):
            with self.subTest(invalid=invalid):
                with self.assertRaises(RuntimeError):
                    REMOTE_MUX.validated_session(invalid)

    def test_frame_socket_is_private_and_cannot_replace_an_existing_path(self):
        session = "0123456789abcdef0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                    REMOTE_MUX, "SESSION_HOME", Path(directory) / "session"):
                path = REMOTE_MUX.frame_socket(session)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                path.write_bytes(b"do not replace")
                with self.assertRaises(RuntimeError):
                    REMOTE_MUX.frame_socket(session)

    def test_input_helper_targets_one_broker_session(self):
        session = "0123456789abcdef0123456789abcdef"
        with tempfile.TemporaryDirectory() as directory:
            password = Path(directory) / "password"
            password.write_text("secret")
            password.chmod(0o600)
            with mock.patch.object(
                    REMOTE_MUX, "RC_PASSWORD_FILE", os.fspath(password)), \
                 mock.patch.object(REMOTE_MUX, "KITTEN", "kitten"):
                command = REMOTE_MUX.input_helper(session)
        self.assertIn(
            f"env:KITTY_PTY_BROKER_SESSION={session}", command)
        self.assertIn("--stdin", command)
        self.assertNotIn("--match all", command)

    def test_serve_wires_observer_tap_and_separate_input(self):
        session = "0123456789abcdef0123456789abcdef"
        ns = REMOTE_MUX.parser().parse_args([
            "serve", "42", "--socket", "/tmp/live.sock",
        ])
        window = {
            "id": 42,
            "title": "live test",
            "lines": 31,
            "columns": 92,
            "env": {"KITTY_PTY_BROKER_SESSION": session},
        }
        with mock.patch.object(
                REMOTE_MUX, "target_window", return_value=window), \
             mock.patch.object(
                 REMOTE_MUX, "build_binary", return_value="/bin/kmx-serve"), \
             mock.patch.object(
                 REMOTE_MUX, "broker_binary", return_value="/bin/broker"), \
             mock.patch.object(
                 REMOTE_MUX, "frame_socket",
                 return_value=Path("/tmp/frame.tap")), \
             mock.patch.object(
                 REMOTE_MUX, "input_helper", return_value="scoped-input"), \
             mock.patch.object(REMOTE_MUX.os, "execv") as execute:
            self.assertEqual(REMOTE_MUX.cmd_serve(ns), 1)

        executable, argv = execute.call_args.args
        self.assertEqual(executable, "/bin/kmx-serve")
        self.assertEqual(argv[0], executable)
        self.assertIn("--broker-session", argv)
        self.assertIn(session, argv)
        self.assertIn("--tap-socket", argv)
        self.assertIn("/tmp/frame.tap", argv)
        self.assertIn("--input-command", argv)
        self.assertIn("scoped-input", argv)
        self.assertEqual(argv[argv.index("--rows") + 1], "31")
        self.assertEqual(argv[argv.index("--cols") + 1], "92")


if __name__ == "__main__":
    unittest.main()
