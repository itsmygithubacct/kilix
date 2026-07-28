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
            self.assertEqual(values["KILIX_CHROME_VOLUME"], "1")
            self.assertEqual(values["KILIX_CHROME_TEMPERATURE"], "0")
            self.assertEqual(values[settings.CLOCK_FORMAT_KEY], "TIME")
            self.assertEqual(
                values[settings.PANE_MEMORY_MODE_KEY], "auto")
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

    def test_ensure_adds_new_toggle_defaults_to_an_existing_shared_file(self):
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
            self.assertIn("KILIX_CHROME_VOLUME=1", text)
            self.assertIn("KILIX_CHROME_TEMPERATURE=0", text)
            self.assertIn(
                "KILIX_CHROME_BUTTON_SYNCHRONIZE_INPUT=1", text)
            self.assertIn(
                f"{settings.PANE_MEMORY_MODE_KEY}=auto", text)
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
                "--set", "volume=off",
                "--set", "temperature=on",
                "--set", "network=off",
                "--set", "synchronize_input=off",
                "--set", "split_up=off",
                "--set", "pane_memory=always",
                "--print",
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn("KILIX_CHROME_VOLUME=off", result.stdout)
            self.assertIn("KILIX_CHROME_TEMPERATURE=on", result.stdout)
            self.assertIn("KILIX_CHROME_NETWORK=off", result.stdout)
            self.assertIn(
                "KILIX_CHROME_BUTTON_SYNCHRONIZE_INPUT=off", result.stdout)
            self.assertIn("KILIX_CHROME_BUTTON_SPLIT_UP=off", result.stdout)
            self.assertIn(
                f"{settings.PANE_MEMORY_MODE_KEY}=always", result.stdout)
            values = settings.load(str(path))
            self.assertFalse(settings.truthy(values["KILIX_CHROME_VOLUME"]))
            self.assertTrue(settings.truthy(
                values["KILIX_CHROME_TEMPERATURE"]))
            self.assertFalse(settings.truthy(values["KILIX_CHROME_NETWORK"]))
            self.assertFalse(settings.truthy(
                values["KILIX_CHROME_BUTTON_SYNCHRONIZE_INPUT"]))
            self.assertFalse(settings.truthy(
                values["KILIX_CHROME_BUTTON_SPLIT_UP"]))
            self.assertEqual(settings.pane_memory_mode(str(path)), "always")

    def test_memory_mode_validation_and_cli_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            settings.update(
                {settings.PANE_MEMORY_MODE_KEY: "off"}, str(path))
            self.assertEqual(settings.pane_memory_mode(str(path)), "off")
            with self.assertRaises(ValueError):
                settings.update(
                    {settings.PANE_MEMORY_MODE_KEY: "sometimes"}, str(path))

            env = dict(os.environ)
            env["GPU_TERMINAL_SETTINGS_FILE"] = str(path)
            result = subprocess.run([
                str(ROOT / "kilix-settings"),
                "--set", "memory=on",
                "--print",
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn(
                f"{settings.PANE_MEMORY_MODE_KEY}=always", result.stdout)
            self.assertEqual(settings.pane_memory_mode(str(path)), "always")

    def test_session_logging_defaults_to_on_with_bounded_elided_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            settings.ensure_file(str(path))
            self.assertTrue(settings.transcript_enabled(str(path)))
            self.assertEqual(settings.transcript_graphics(str(path)), "elide")
            self.assertEqual(settings.transcript_limit(str(path)), 8 * 1024 * 1024)
            text = path.read_text()
            self.assertIn(settings.SESSION_LOG_MARKER, text)
            self.assertIn("KILIX_TRANSCRIPT=1", text)

    def test_session_logging_keys_reach_an_existing_shared_file(self):
        # Upgrades must pick up the new defaults without losing user content.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            original = "# hand written\nKILIX_CHROME_CLOCK=0\n"
            path.write_text(original)
            settings.ensure_file(str(path))
            text = path.read_text()
            self.assertIn(original, text)
            self.assertIn("KILIX_TRANSCRIPT=1", text)
            self.assertIn(f"{settings.TRANSCRIPT_GRAPHICS_KEY}=elide", text)
            self.assertIn(f"{settings.TRANSCRIPT_LIMIT_KEY}=8M", text)

    def test_transcript_values_are_validated_not_coerced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            settings.update(
                {settings.TRANSCRIPT_GRAPHICS_KEY: "keep"}, str(path))
            self.assertEqual(settings.transcript_graphics(str(path)), "keep")
            with self.assertRaises(ValueError):
                settings.update(
                    {settings.TRANSCRIPT_GRAPHICS_KEY: "sometimes"}, str(path))
            with self.assertRaises(ValueError):
                settings.update(
                    {settings.TRANSCRIPT_LIMIT_KEY: "7M"}, str(path))
            # An unrecognised value already in the file reads back as the
            # default rather than reaching the broker.
            settings.update({settings.CLOCK_FORMAT_KEY: "%H:%M"}, str(path))
            path.write_text(
                path.read_text() + f"\n{settings.TRANSCRIPT_LIMIT_KEY}=99M\n")
            self.assertEqual(settings.transcript_limit(str(path)), 8 * 1024 * 1024)

    def test_transcript_cli_aliases_and_size_suffixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            env = dict(os.environ)
            env["GPU_TERMINAL_SETTINGS_FILE"] = str(path)
            result = subprocess.run([
                str(ROOT / "kilix-settings"),
                "--set", "transcript=off",
                "--set", "transcript_size=32M",
                "--set", "log_graphics=keep",
                "--print",
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn("KILIX_TRANSCRIPT=off", result.stdout)
            self.assertIn(
                f"{settings.TRANSCRIPT_LIMIT_KEY}=32M", result.stdout)
            self.assertIn(
                f"{settings.TRANSCRIPT_GRAPHICS_KEY}=keep", result.stdout)
            self.assertFalse(settings.transcript_enabled(str(path)))

            rejected = subprocess.run([
                str(ROOT / "kilix-settings"), "--set", "transcript_size=7M",
            ], env=env, text=True, capture_output=True, check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("transcript size must be one of", rejected.stderr)

    def test_disable_all_leaves_qualifier_choices_valid(self):
        # KILIX_TRANSCRIPT is the off switch; its two qualifiers have no "off"
        # member and must not be written with one.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            env = dict(os.environ)
            env["GPU_TERMINAL_SETTINGS_FILE"] = str(path)
            subprocess.run(
                [str(ROOT / "kilix-settings"), "--disable-all"],
                env=env, text=True, capture_output=True, check=True)
            self.assertFalse(settings.transcript_enabled(str(path)))
            self.assertIn(
                f"{settings.TRANSCRIPT_GRAPHICS_KEY}=elide", path.read_text())
            self.assertIn(
                f"{settings.TRANSCRIPT_LIMIT_KEY}=8M", path.read_text())

    def test_voice_defaults_reach_a_fresh_and_an_existing_shared_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "settings.conf"
            settings.ensure_file(str(fresh))
            text = fresh.read_text()
            self.assertIn(settings.VOICE_MARKER, text)
            for key in settings.VOICE_KEYS:
                self.assertIn(f"{key}=", text)
            # Both widgets ship visible; nothing about that opens a microphone.
            self.assertTrue(settings.enabled("KILIX_CHROME_SPEAK", str(fresh)))
            self.assertTrue(settings.enabled("KILIX_CHROME_DICTATE", str(fresh)))
            self.assertEqual(settings.tts_engine(str(fresh)), "espeak")
            self.assertEqual(settings.tts_voice(str(fresh)), "en-us")
            self.assertEqual(settings.tts_rate(str(fresh)), 170)
            self.assertEqual(settings.tts_extent(str(fresh)), "screen")
            self.assertEqual(settings.tts_max_chars(str(fresh)), 4000)
            self.assertEqual(settings.stt_engine(str(fresh)), "vosk")
            self.assertEqual(settings.stt_model(str(fresh)), "small-en-us")
            self.assertEqual(settings.stt_submit(str(fresh)), "never")
            self.assertEqual(settings.stt_max_seconds(str(fresh)), 30)
            self.assertEqual(settings.stt_silence_ms(str(fresh)), 900)
            self.assertTrue(settings.enabled(
                settings.VOICE_PUNCTUATION_KEY, str(fresh)))
            self.assertEqual(settings.voice_device_in(str(fresh)), "default")
            self.assertEqual(settings.voice_device_out(str(fresh)), "default")
            self.assertFalse(settings.voice_history(str(fresh)))

            # The upgrade path: an existing file gains the section without
            # losing what the user already wrote.
            existing = Path(tmp) / "existing.conf"
            original = "# hand written\nKILIX_CHROME_CLOCK=0\n"
            existing.write_text(original)
            settings.ensure_file(str(existing))
            upgraded = existing.read_text()
            self.assertIn(original, upgraded)
            self.assertIn(settings.VOICE_MARKER, upgraded)
            self.assertIn(f"{settings.VOICE_STT_SUBMIT_KEY}=never", upgraded)
            self.assertIn(f"{settings.VOICE_HISTORY_KEY}=off", upgraded)

    def test_voice_keys_round_trip_through_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            path.write_text("# custom\nOTHER_PROJECT_SETTING=keep\n")
            settings.update({
                "KILIX_CHROME_SPEAK": False,
                "KILIX_CHROME_DICTATE": False,
                settings.VOICE_TTS_ENGINE_KEY: "mbrola",
                settings.VOICE_TTS_VOICE_KEY: "mb-us1",
                settings.VOICE_TTS_RATE_KEY: "240",
                settings.VOICE_TTS_EXTENT_KEY: "selection",
                settings.VOICE_TTS_MAX_CHARS_KEY: "unlimited",
                settings.VOICE_STT_ENGINE_KEY: "vibevoice",
                settings.VOICE_STT_MODEL_KEY: "vibevoice-asr-bitnet",
                settings.VOICE_STT_SUBMIT_KEY: "confirm",
                settings.VOICE_STT_MAX_SECONDS_KEY: "120",
                settings.VOICE_STT_SILENCE_MS_KEY: "500",
                settings.VOICE_PUNCTUATION_KEY: False,
                settings.VOICE_DEVICE_IN_KEY: "alsa_input.pci-0000_00_1f.3",
                settings.VOICE_DEVICE_OUT_KEY: "alsa_output.pci-0000_00_1f.3",
                settings.VOICE_HISTORY_KEY: "on",
            }, str(path))
            text = path.read_text()
            self.assertIn("OTHER_PROJECT_SETTING=keep", text)
            self.assertIn(settings.VOICE_MARKER, text)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertFalse(settings.enabled("KILIX_CHROME_SPEAK", str(path)))
            self.assertFalse(settings.enabled("KILIX_CHROME_DICTATE", str(path)))
            self.assertEqual(settings.tts_engine(str(path)), "mbrola")
            self.assertEqual(settings.tts_voice(str(path)), "mb-us1")
            self.assertEqual(settings.tts_rate(str(path)), 240)
            self.assertEqual(settings.tts_extent(str(path)), "selection")
            self.assertIsNone(settings.tts_max_chars(str(path)))
            self.assertEqual(settings.stt_engine(str(path)), "vibevoice")
            self.assertEqual(
                settings.stt_model(str(path)), "vibevoice-asr-bitnet")
            self.assertEqual(settings.stt_submit(str(path)), "confirm")
            self.assertEqual(settings.stt_max_seconds(str(path)), 120)
            self.assertEqual(settings.stt_silence_ms(str(path)), 500)
            self.assertFalse(settings.enabled(
                settings.VOICE_PUNCTUATION_KEY, str(path)))
            self.assertEqual(
                settings.voice_device_in(str(path)),
                "alsa_input.pci-0000_00_1f.3")
            self.assertEqual(
                settings.voice_device_out(str(path)),
                "alsa_output.pci-0000_00_1f.3")
            self.assertTrue(settings.voice_history(str(path)))

    def test_voice_values_are_validated_not_coerced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            for key, rejected in (
                (settings.VOICE_TTS_ENGINE_KEY, "piper"),
                (settings.VOICE_TTS_RATE_KEY, "185"),
                (settings.VOICE_TTS_EXTENT_KEY, "everything"),
                (settings.VOICE_TTS_MAX_CHARS_KEY, "2500"),
                (settings.VOICE_STT_ENGINE_KEY, "whisper"),
                (settings.VOICE_STT_MODEL_KEY, "en-us-0.22"),
                (settings.VOICE_STT_MAX_SECONDS_KEY, "600"),
                (settings.VOICE_STT_SILENCE_MS_KEY, "50"),
                (settings.VOICE_HISTORY_KEY, "sometimes"),
                # Not a token: these two reach a synthesiser argument list and
                # an audio server, so anything shaped like a shell word is out.
                (settings.VOICE_TTS_VOICE_KEY, "en-us; rm -rf ~"),
                (settings.VOICE_DEVICE_IN_KEY, "$(pactl list)"),
            ):
                with self.assertRaises(ValueError):
                    settings.update({key: rejected}, str(path))

            # There is no submit policy that presses Enter unasked, and one
            # hand-edited into the file must not become one.
            settings.update(
                {settings.VOICE_STT_SUBMIT_KEY: "confirm"}, str(path))
            with self.assertRaises(ValueError):
                settings.update(
                    {settings.VOICE_STT_SUBMIT_KEY: "always"}, str(path))
            path.write_text(
                path.read_text()
                + f"\n{settings.VOICE_STT_SUBMIT_KEY}=always\n"
                + f"{settings.VOICE_TTS_RATE_KEY}=999\n"
                + f"{settings.VOICE_TTS_VOICE_KEY}=en us\n")
            self.assertEqual(settings.stt_submit(str(path)), "never")
            self.assertEqual(settings.tts_rate(str(path)), 170)
            self.assertEqual(settings.tts_voice(str(path)), "en-us")

    def test_voice_cli_aliases_reach_the_shared_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            env = dict(os.environ)
            env["GPU_TERMINAL_SETTINGS_FILE"] = str(path)
            result = subprocess.run([
                str(ROOT / "kilix-settings"),
                "--set", "speak=off",
                "--set", "mic=off",
                "--set", "wpm=200",
                "--set", "read_extent=scrollback",
                "--set", "voice=mb-us1",
                "--set", "stt_submit=confirm",
                "--set", "voice_model=lgraph-en-us",
                "--set", "punctuation=off",
                "--print",
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn("KILIX_CHROME_SPEAK=off", result.stdout)
            self.assertIn("KILIX_CHROME_DICTATE=off", result.stdout)
            self.assertIn(
                f"{settings.VOICE_TTS_RATE_KEY}=200", result.stdout)
            self.assertIn(
                f"{settings.VOICE_TTS_EXTENT_KEY}=scrollback", result.stdout)
            self.assertIn(
                f"{settings.VOICE_TTS_VOICE_KEY}=mb-us1", result.stdout)
            self.assertIn(
                f"{settings.VOICE_STT_SUBMIT_KEY}=confirm", result.stdout)
            self.assertIn(
                f"{settings.VOICE_STT_MODEL_KEY}=lgraph-en-us", result.stdout)
            self.assertIn(
                f"{settings.VOICE_PUNCTUATION_KEY}=off", result.stdout)
            self.assertFalse(settings.enabled("KILIX_CHROME_SPEAK", str(path)))
            self.assertFalse(settings.enabled("KILIX_CHROME_DICTATE", str(path)))
            self.assertEqual(settings.tts_rate(str(path)), 200)
            self.assertEqual(settings.stt_submit(str(path)), "confirm")

            rejected = subprocess.run([
                str(ROOT / "kilix-settings"), "--set", "stt_submit=always",
            ], env=env, text=True, capture_output=True, check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("stt_submit must be one of", rejected.stderr)
            self.assertEqual(settings.stt_submit(str(path)), "confirm")

    def test_tui_section_aliases_track_real_section_order(self):
        module = _load_settings_tui()
        names = [section for section, _specs in module.UI_SECTIONS]
        self.assertIn("Session logging", names)
        for alias, expected in (
            ("top-bar", "Top bar"),
            ("pane-buttons", "Pane buttons"),
            ("session-logging", "Session logging"),
            ("transcript", "Session logging"),
            ("voice", "Voice"),
            ("speech", "Voice"),
            ("tts", "Voice"),
            ("stt", "Voice"),
            ("games", "Games"),
            ("tools", "Tools"),
        ):
            index = module.SECTION_ALIASES[alias]
            self.assertEqual(names[index], expected)
        # Voice was inserted here rather than appended so that only Games and
        # Tools shift: every earlier numeric --section keeps its meaning.
        self.assertEqual(
            names.index("Voice"), names.index("Session logging") + 1)
        self.assertEqual(names.index("Games"), names.index("Voice") + 1)

    def test_game_cli_names_and_listing_use_same_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            env = dict(os.environ)
            env["GPU_TERMINAL_SETTINGS_FILE"] = str(path)
            result = subprocess.run([
                str(ROOT / "kilix-settings"),
                "--set", "bashed-earth=off",
                "--set", "game_kilix_pong=off",
                "--set", "kilix-lights=off",
                "--set", "super-kilix=off",
                "--print-games",
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn("bashed-earth=off\tBashed Earth", result.stdout)
            self.assertIn("kilix-pong=off\tKilix Pong", result.stdout)
            self.assertIn("kilix-lights=off\tKilix Lights", result.stdout)
            self.assertIn("super-kilix=off\tSuper Kilix", result.stdout)
            self.assertFalse(settings.game_enabled("bashed-earth", str(path)))
            self.assertFalse(settings.game_enabled("kilix-pong", str(path)))
            self.assertFalse(settings.game_enabled("kilix-lights", str(path)))
            self.assertFalse(settings.game_enabled("super-kilix", str(path)))

    def test_kilix_cli_enables_thermometer_in_shared_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            for key in list(env):
                if key.startswith("KILIX") or key.startswith("GPU_TERMINAL"):
                    env.pop(key)
            env["GPU_TERMINAL_HOME"] = tmp
            result = subprocess.run([
                str(ROOT / "kilix"), "settings",
                "--set", "temperature=on", "--print",
            ], env=env, text=True, capture_output=True, check=True)
            path = Path(tmp) / "settings.conf"
            self.assertIn("KILIX_CHROME_TEMPERATURE=on", result.stdout)
            self.assertTrue(settings.enabled(
                "KILIX_CHROME_TEMPERATURE", str(path)))

    def test_kilix_games_subcommand_changes_root_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            for key in list(env):
                if key.startswith("KILIX") or key.startswith("GPU_TERMINAL"):
                    env.pop(key)
            env["GPU_TERMINAL_HOME"] = tmp
            result = subprocess.run([
                str(ROOT / "kilix"), "games", "disable", "doom", "kilix-pong",
                "kilix-lights", "super-kilix"
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn("doom=off\tDoom", result.stdout)
            self.assertIn("kilix-pong=off\tKilix Pong", result.stdout)
            self.assertIn("kilix-lights=off\tKilix Lights", result.stdout)
            self.assertIn("super-kilix=off\tSuper Kilix", result.stdout)
            path = Path(tmp) / "settings.conf"
            self.assertTrue(path.is_file())
            self.assertFalse(settings.game_enabled("doom", str(path)))
            self.assertFalse(settings.game_enabled("kilix-pong", str(path)))
            self.assertFalse(settings.game_enabled("kilix-lights", str(path)))
            self.assertFalse(settings.game_enabled("super-kilix", str(path)))
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
                settings.truthy(values[spec.key]) == spec.default
                for spec in settings.TOP_BAR_TOGGLES
                + settings.PANE_BUTTON_TOGGLES))
            first_frame = "\n".join(
                item[2] for item in screen.frames[0])
            game_count = len(settings.GAME_TOGGLES)
            self.assertIn(
                f"Games: {game_count}/{game_count} enabled", first_frame)

    def test_tui_exposes_thermal_and_volume_top_bar_controls(self):
        tui = _load_settings_tui()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SETTINGS_FILE": str(path),
                    "KITTY_PID": ""}, clear=False):
                screen = FakeScreen([ord("j"), ord(" "), ord("s"), ord("q")])
                self.assertEqual(tui._run_tui(screen, "top-bar"), 0)

            self.assertFalse(settings.enabled(
                "KILIX_CHROME_VOLUME", str(path)))
            self.assertTrue(settings.enabled(
                "KILIX_CHROME_NETWORK", str(path)))
            self.assertFalse(settings.enabled(
                "KILIX_CHROME_TEMPERATURE", str(path)))
            first_frame = "\n".join(item[2] for item in screen.frames[0])
            self.assertIn("Top bar: 7/8 enabled", first_frame)
            self.assertIn("Thermal status", first_frame)
            self.assertIn("Volume", first_frame)
            self.assertIn("Read pane aloud", first_frame)
            self.assertIn("Dictate to pane", first_frame)

    def test_tui_tools_select_pinned_tmux_download_and_tb_install(self):
        tui = _load_settings_tui()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SETTINGS_FILE": str(path),
                    "KITTY_PID": ""}, clear=False):
                memory = FakeScreen([10])
                self.assertEqual(
                    tui._run_tui(memory, "tools"),
                    "tool:memory-monitor",
                )
                manager = FakeScreen([ord("j"), 10])
                self.assertEqual(
                    tui._run_tui(manager, "tools"),
                    "tool:tmux-manager",
                )
                alias = FakeScreen([ord("j"), ord("j"), 10])
                self.assertEqual(
                    tui._run_tui(alias, "tools"),
                    "tool:install-tb",
                )

        self.assertEqual(
            tui._tool_argv("memory-monitor"),
            [str(ROOT / "kilix"), "memory", "--graphics"],
        )
        self.assertEqual(
            tui._tool_argv("tmux-manager"),
            [str(ROOT / "kilix"), "tmux"],
        )
        self.assertEqual(
            tui._tool_argv("install-tb"),
            [str(ROOT / "kilix"), "tmux", "--install-only", "--with-tb"],
        )
        frame = "\n".join(item[2] for item in manager.frames[0])
        self.assertIn("Tmux Manager — download and run", frame)

    def test_tui_cycles_pane_memory_mode(self):
        tui = _load_settings_tui()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SETTINGS_FILE": str(path),
                    "KITTY_PID": ""}, clear=False):
                # Pane memory is the first row: auto -> always.
                screen = FakeScreen([ord(" "), ord("s"), ord("q")])
                self.assertEqual(tui._run_tui(screen, "pane-buttons"), 0)
            self.assertEqual(settings.pane_memory_mode(str(path)), "always")
            first_frame = "\n".join(item[2] for item in screen.frames[0])
            self.assertIn("Pane memory chip", first_frame)
            self.assertIn("[ auto ]", first_frame)

    def test_tui_quit_warning_allows_save_as_the_next_key(self):
        tui = _load_settings_tui()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SETTINGS_FILE": str(path),
                    "KITTY_PID": ""}, clear=False):
                # Top bar -> Pane buttons -> Session logging -> Voice ->
                # Games, then toggle that section's first entry.
                screen = FakeScreen([
                    ord("l"), ord("l"), ord("l"), ord("l"), ord(" "),
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
