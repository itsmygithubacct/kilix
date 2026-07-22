import importlib.machinery
import importlib.util
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


def _load_settings_tui():
    loader = importlib.machinery.SourceFileLoader(
        "kilix_settings_tui_test", str(ROOT / "kilix-settings"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("could not load kilix-settings")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeScreen:
    def __init__(self, keys, height=24, width=100):
        self.keys = list(keys)
        self.height = height
        self.width = width
        self.current_frame = []
        self.frames = []

    def keypad(self, _enabled):
        pass

    def erase(self):
        self.current_frame = []

    def getmaxyx(self):
        return self.height, self.width

    def addnstr(self, row, column, value, count, attributes=0):
        self.current_frame.append(
            (row, column, value[:count], attributes))

    def refresh(self):
        self.frames.append(list(self.current_frame))

    def getch(self):
        if not self.keys:
            raise AssertionError("TUI requested an unexpected key")
        return self.keys.pop(0)


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

    def test_tui_opens_games_and_section_bulk_action_stays_scoped(self):
        tui = _load_settings_tui()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SETTINGS_FILE": str(path),
                    "KITTY_PID": ""}, clear=False):
                screen = FakeScreen([ord("n"), ord("s"), ord("q")])
                self.assertEqual(tui._run_tui(screen, "games"), 0)

            values = settings.load(str(path))
            self.assertTrue(all(
                not settings.truthy(values[key])
                for key in settings.GAME_KEY_BY_ID.values()))
            self.assertTrue(all(
                settings.truthy(values[spec.key])
                for spec in settings.TOP_BAR_TOGGLES
                + settings.PANE_BUTTON_TOGGLES))
            first_frame = "\n".join(
                item[2] for item in screen.frames[0])
            self.assertIn("Games: 13/13 enabled", first_frame)

    def test_tui_quit_warning_allows_save_as_the_next_key(self):
        tui = _load_settings_tui()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SETTINGS_FILE": str(path),
                    "KITTY_PID": ""}, clear=False):
                screen = FakeScreen([
                    ord("l"), ord("l"), ord(" "),
                    ord("q"), ord("s"), ord("q"),
                ])
                self.assertEqual(tui._run_tui(screen), 0)

            self.assertFalse(settings.game_enabled(
                "minesweeper", str(path)))
            self.assertTrue(settings.game_enabled("solitaire", str(path)))
            frames = ["\n".join(item[2] for item in frame)
                      for frame in screen.frames]
            self.assertTrue(any(
                "Unsaved changes: s saves; q again discards." in frame
                for frame in frames))

    def test_games_settings_launcher_targets_games_section(self):
        launcher = (ROOT / "kilix").read_text()
        self.assertIn(
            '"$KILIX_HOME/kilix-settings" --section games', launcher)


if __name__ == "__main__":
    unittest.main()
