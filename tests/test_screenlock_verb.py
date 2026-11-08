"""Screen Lock is an early TUI verb, with no desktop or engine side effects."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ScreenLockVerb(unittest.TestCase):
    def test_power_controls_native_and_text_options_delegate_without_desktop_setup(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            host = base / "host"
            (host / "config").mkdir(parents=True)
            shutil.copy2(ROOT / "kilix", host / "kilix")
            (host / "config/content_app.py").write_text('import json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            env = {"PATH": "/usr/bin:/bin", "HOME": str(base / "home")}
            for tail in (["--native", "--show"], ["--show"], ["--help"], ["--version"]):
                result = subprocess.run([host / "kilix", "power-controls", *tail], env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), ["run", "kilix-power-controls", "--", *tail])
            self.assertFalse((base / "home").exists())

    def test_dispatch_preserves_arguments_and_does_not_initialize_host_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            command = base / "pleb"
            command.write_text('#!/usr/bin/python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            command.chmod(0o700)
            env = {"PATH": str(base) + ":/usr/bin:/bin", "HOME": str(base / "home")}
            for alias in ("tui", "kilix-tui"):
                args = ["--set", "off", "--idle-seconds", "0"]
                result = subprocess.run([ROOT / "kilix", alias, "screen-lock", *args], env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), ["screen-lock", *args])
            self.assertFalse((base / "home").exists())


if __name__ == "__main__":
    unittest.main()
