import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from kilix_sdk import settings


class SharedSettingsTests(unittest.TestCase):
    def test_default_path_is_at_shared_gpu_terminal_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_HOME": tmp,
                    "GPU_TERMINAL_SETTINGS_FILE": ""}):
                self.assertEqual(
                    Path(settings.settings_path()), Path(tmp) / "settings.conf")

    def test_first_creation_migrates_legacy_clock_and_battery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SETTINGS_FILE": str(path),
                    "KILIX_CHROME_CLOCK": "0",
                    "KILIX_CHROME_BATTERY": "0",
                    "KILIX_CHROME_CLOCK_FORMAT": "TIME"}, clear=False):
                settings.ensure_file()
                values = settings.load()
            self.assertEqual(values["KILIX_CHROME_CLOCK"], "0")
            self.assertEqual(values["KILIX_CHROME_CALENDAR"], "0")
            self.assertEqual(values["KILIX_CHROME_BATTERY"], "0")
            self.assertEqual(values[settings.CLOCK_FORMAT_KEY], "TIME")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_atomic_update_preserves_unknown_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            path.write_text("# custom\nOTHER_PROJECT_SETTING=keep\n")
            settings.update({
                "KILIX_CHROME_NETWORK": False,
                "KILIX_CHROME_BUTTON_SPLIT_LEFT": False,
            }, str(path))
            text = path.read_text()
            self.assertIn("# custom", text)
            self.assertIn("OTHER_PROJECT_SETTING=keep", text)
            self.assertIn("KILIX_CHROME_NETWORK=0", text)
            self.assertIn("KILIX_CHROME_BUTTON_SPLIT_LEFT=0", text)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_noninteractive_tui_controls_use_same_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            env = dict(os.environ)
            env["GPU_TERMINAL_SETTINGS_FILE"] = str(path)
            result = subprocess.run([
                str(ROOT / "kilix-settings"),
                "--set", "network=off",
                "--set", "split_up=off",
                "--print",
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn("KILIX_CHROME_NETWORK=off", result.stdout)
            self.assertIn("KILIX_CHROME_BUTTON_SPLIT_UP=off", result.stdout)
            values = settings.load(str(path))
            self.assertFalse(settings.truthy(values["KILIX_CHROME_NETWORK"]))
            self.assertFalse(settings.truthy(
                values["KILIX_CHROME_BUTTON_SPLIT_UP"]))


if __name__ == "__main__":
    unittest.main()
