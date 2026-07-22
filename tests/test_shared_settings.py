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

from kilix_sdk import content, settings


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

    def test_game_schema_covers_builtins_and_catalog(self):
        catalog_games = {
            spec.content_id for spec in content.default_catalog()
            if spec.kind == "game" or spec.content_id == "dosbox"
        }
        configured_games = set(settings.GAME_KEY_BY_ID)
        self.assertEqual(
            configured_games - {"minesweeper", "solitaire"}, catalog_games)
        self.assertTrue(all(
            settings.defaults()[key] == "1"
            for key in settings.GAME_KEY_BY_ID.values()))

    def test_game_update_gets_its_own_section_in_an_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            path.write_text("# existing chrome preferences\nKILIX_CHROME_CLOCK=0\n")
            settings.update({"KILIX_GAME_DOOM": False}, str(path))
            text = path.read_text()
            self.assertIn(settings.GAMES_MARKER, text)
            self.assertIn("KILIX_GAME_DOOM=0", text)
            self.assertFalse(settings.game_enabled("doom", str(path)))

    def test_ensure_adds_game_defaults_to_an_existing_shared_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            original = (
                "# keep this comment\n"
                "KILIX_CHROME_CLOCK=0\n"
                "KILIX_GAME_DOOM=0\n"
            )
            path.write_text(original)
            settings.ensure_file(str(path))
            text = path.read_text()
            self.assertIn(original, text)
            self.assertIn(settings.GAMES_MARKER, text)
            for key in settings.GAME_KEY_BY_ID.values():
                expected = "0" if key == "KILIX_GAME_DOOM" else "1"
                self.assertIn(f"{key}={expected}", text)

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

    def test_game_cli_names_and_listing_use_same_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            env = dict(os.environ)
            env["GPU_TERMINAL_SETTINGS_FILE"] = str(path)
            result = subprocess.run([
                str(ROOT / "kilix-settings"),
                "--set", "bashed-earth=off",
                "--set", "game_kilix_pong=off",
                "--print-games",
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn("bashed-earth=off\tBashed Earth", result.stdout)
            self.assertIn("kilix-pong=off\tKilix Pong", result.stdout)
            self.assertFalse(settings.game_enabled("bashed-earth", str(path)))
            self.assertFalse(settings.game_enabled("kilix-pong", str(path)))

    def test_kilix_games_subcommand_changes_root_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            for key in list(env):
                if key.startswith("KILIX") or key.startswith("GPU_TERMINAL"):
                    env.pop(key)
            env["GPU_TERMINAL_HOME"] = tmp
            result = subprocess.run([
                str(ROOT / "kilix"), "games", "disable", "doom", "kilix-pong"
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn("doom=off\tDoom", result.stdout)
            self.assertIn("kilix-pong=off\tKilix Pong", result.stdout)
            path = Path(tmp) / "settings.conf"
            self.assertTrue(path.is_file())
            self.assertFalse(settings.game_enabled("doom", str(path)))
            self.assertFalse(settings.game_enabled("kilix-pong", str(path)))
            rejected = subprocess.run([
                str(ROOT / "kilix"), "games", "disable", "network"
            ], env=env, text=True, capture_output=True)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("unknown game", rejected.stderr)
            self.assertTrue(settings.enabled("KILIX_CHROME_NETWORK", str(path)))


if __name__ == "__main__":
    unittest.main()
