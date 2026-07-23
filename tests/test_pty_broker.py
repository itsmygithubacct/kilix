import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "kitty" / "pty_broker.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "kilix_test_pty_broker", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load pty_broker module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PtyBrokerIntegrationTests(unittest.TestCase):
    def test_launcher_builds_and_exports_broker(self):
        launcher = (ROOT / "kilix").read_text()
        builder = (ROOT / "scripts" / "build-pty-broker.sh").read_text()
        self.assertIn("build-pty-broker.sh", launcher)
        self.assertIn("KITTY_PTY_BROKER_EXECUTABLE", launcher)
        self.assertIn("KITTY_PTY_BROKER_RUNTIME", launcher)
        self.assertIn("KILIX_PTY_BROKER_AUTO_RECOVER", launcher)
        self.assertIn("kitty-pty-broker", builder)
        self.assertIn("BUILD_DIR=", builder)

    def test_fork_wraps_only_managed_windows(self):
        child = (ROOT / "src" / "kitty" / "child.py").read_text()
        tabs = (ROOT / "src" / "kitty" / "tabs.py").read_text()
        boss = (ROOT / "src" / "kitty" / "boss.py").read_text()
        title_bar = (
            ROOT / "src" / "kitty" / "window_title_bar.py").read_text()
        self.assertIn("wrap_command(", child)
        self.assertIn("use_pty_broker=(", tabs)
        self.assertIn("overlay_for is None and not overlay_behind", tabs)
        self.assertIn("KITTY_PTY_BROKER_BYPASS", tabs)
        self.assertIn("recover_pty_broker_sessions", boss)
        self.assertIn("kilix_close_persistent_window", boss)
        self.assertIn("kilix_close_persistent_window", title_bar)

    def test_configuration_and_command_are_bounded(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "broker"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(
                stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            runtime = Path(temporary) / "runtime"
            runtime.mkdir()
            environment = {
                "KITTY_PTY_BROKER_EXECUTABLE": str(executable),
                "KITTY_PTY_BROKER_RUNTIME": str(runtime),
                "KITTY_PTY_BROKER_JOURNAL_LIMIT": "12345",
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                self.assertEqual(
                    module.configuration(),
                    (str(executable), str(runtime)))
                command = module.wrap_command(
                    str(executable), str(runtime), "a-session",
                    ["/bin/sh", "-l"])
            self.assertEqual(command[:5], [
                str(executable), "--runtime-dir", str(runtime), "run", "--id"])
            self.assertIn("12345", command)
            self.assertEqual(command[-3:], ["--", "/bin/sh", "-l"])
            self.assertTrue(module.valid_session_id("a-session"))
            self.assertFalse(module.valid_session_id("../session"))
            with self.assertRaises(ValueError):
                module.wrap_command(
                    str(executable), str(runtime), "../bad", ["/bin/sh"])


if __name__ == "__main__":
    unittest.main()
