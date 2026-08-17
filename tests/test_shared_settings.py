import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
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
                values[settings.PANE_CPU_MODE_KEY], "auto")
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

    def test_concurrent_updates_preserve_every_managed_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            path.write_text("#" + " padding" * 32768 + "\n")
            specs = [spec for spec in settings.TOGGLE_SPECS if spec.default][:12]
            start = threading.Barrier(len(specs))

            def write_one(spec):
                start.wait()
                settings.update({spec.key: False}, str(path))

            with ThreadPoolExecutor(max_workers=len(specs)) as pool:
                list(pool.map(write_one, specs))
            values = settings.load(str(path))
            self.assertTrue(all(values[spec.key] == "0" for spec in specs))
            self.assertEqual(
                stat.S_IMODE(Path(str(path) + ".lock").stat().st_mode), 0o600)

    def test_unsafe_or_oversized_settings_are_not_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "victim.conf"
            victim.write_text("OTHER_PROJECT_SETTING=keep\n")
            linked = root / "linked.conf"
            linked.symlink_to(victim)
            for operation in (
                    lambda: settings.read_text(str(linked)),
                    lambda: settings.ensure_file(str(linked)),
                    lambda: settings.update({"KILIX_CHROME_CLOCK": False}, str(linked))):
                with self.subTest(operation=operation):
                    with self.assertRaises(OSError):
                        operation()
            self.assertEqual(victim.read_text(), "OTHER_PROJECT_SETTING=keep\n")

            oversized = root / "oversized.conf"
            oversized.write_bytes(b"x" * (settings.SETTINGS_MAX_BYTES + 1))
            with self.assertRaises(OSError):
                settings.load(str(oversized))
            before = oversized.stat().st_size
            with self.assertRaises(OSError):
                settings.update({"KILIX_CHROME_CLOCK": False}, str(oversized))
            self.assertEqual(oversized.stat().st_size, before)

            fifo = root / "settings.fifo"
            os.mkfifo(fifo)
            with self.assertRaises(OSError):
                settings.read_text(str(fifo))

    def test_environment_migration_cannot_add_lines_or_invalid_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with mock.patch.dict(os.environ, {
                    settings.CLOCK_FORMAT_KEY: "%H:%M\nUNMANAGED=injected",
                    settings.VOICE_TTS_ENGINE_KEY: "not-an-engine"}, clear=False):
                settings.ensure_file(str(path))
            text = path.read_text()
            self.assertNotIn("\nUNMANAGED=", text)
            self.assertIn(f"{settings.CLOCK_FORMAT_KEY}=%H:%M UNMANAGED=injected", text)
            self.assertIn(
                f"{settings.VOICE_TTS_ENGINE_KEY}="
                f"{settings.VOICE_TTS_ENGINE_DEFAULT}", text)

    def test_relative_update_uses_the_same_atomic_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            try:
                os.chdir(tmp)
                result = settings.update({"KILIX_CHROME_CLOCK": False}, "local.conf")
            finally:
                os.chdir(previous)
            self.assertEqual(Path(result), Path(tmp) / "local.conf")
            self.assertFalse(settings.enabled("KILIX_CHROME_CLOCK", result))

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

    def test_coding_yolo_is_bounded_and_gets_its_own_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            path.write_text("# existing chrome preferences\nKILIX_CHROME_CLOCK=0\n")

            self.assertFalse(settings.coding_yolo(str(path)))
            settings.update({settings.CODING_YOLO_KEY: "ON"}, str(path))

            text = path.read_text()
            self.assertIn(settings.CODING_MARKER, text)
            self.assertIn(f"{settings.CODING_YOLO_KEY}=on", text)
            self.assertTrue(settings.coding_yolo(str(path)))
            with self.assertRaises(ValueError):
                settings.update(
                    {settings.CODING_YOLO_KEY: "sometimes"}, str(path))

    def test_ensure_preserves_existing_bytes_and_supplies_new_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            original = (
                "# keep this comment\n"
                "KILIX_CHROME_CLOCK=0\n"
                "KILIX_GAME_DOOM=0\n"
            )
            path.write_text(original)
            settings.ensure_file(str(path))
            self.assertEqual(path.read_text(), original)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            values = settings.load(str(path))
            self.assertEqual(values["KILIX_CHROME_VOLUME"], "1")
            self.assertEqual(values["KILIX_CHROME_TEMPERATURE"], "0")
            self.assertEqual(
                values["KILIX_CHROME_BUTTON_SYNCHRONIZE_INPUT"], "1")
            self.assertEqual(values[settings.PANE_CPU_MODE_KEY], "auto")
            self.assertEqual(values[settings.PANE_MEMORY_MODE_KEY], "auto")
            for key in settings.GAME_KEY_BY_ID.values():
                expected = "0" if key == "KILIX_GAME_DOOM" else "1"
                self.assertEqual(values[key], expected)

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
                "--set", "pane_cpu=always",
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
                f"{settings.PANE_CPU_MODE_KEY}=always", result.stdout)
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
            self.assertEqual(settings.pane_cpu_mode(str(path)), "always")
            self.assertEqual(settings.pane_memory_mode(str(path)), "always")

    def test_cpu_and_memory_mode_validation_and_cli_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            settings.update(
                {
                    settings.PANE_CPU_MODE_KEY: "off",
                    settings.PANE_MEMORY_MODE_KEY: "off",
                }, str(path))
            self.assertEqual(settings.pane_cpu_mode(str(path)), "off")
            self.assertEqual(settings.pane_memory_mode(str(path)), "off")
            with self.assertRaises(ValueError):
                settings.update(
                    {settings.PANE_MEMORY_MODE_KEY: "sometimes"}, str(path))
            with self.assertRaises(ValueError):
                settings.update(
                    {settings.PANE_CPU_MODE_KEY: "sometimes"}, str(path))

            env = dict(os.environ)
            env["GPU_TERMINAL_SETTINGS_FILE"] = str(path)
            result = subprocess.run([
                str(ROOT / "kilix-settings"),
                "--set", "cpu=on",
                "--set", "memory=on",
                "--print",
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn(
                f"{settings.PANE_CPU_MODE_KEY}=always", result.stdout)
            self.assertIn(
                f"{settings.PANE_MEMORY_MODE_KEY}=always", result.stdout)
            self.assertEqual(settings.pane_cpu_mode(str(path)), "always")
            self.assertEqual(settings.pane_memory_mode(str(path)), "always")

    def test_session_logging_defaults_to_on_with_bounded_elided_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            settings.ensure_file(str(path))
            self.assertTrue(settings.transcript_enabled(str(path)))
            self.assertEqual(settings.transcript_graphics(str(path)), "elide")
            self.assertEqual(settings.transcript_limit(str(path)), 8 * 1024 * 1024)
            self.assertEqual(
                settings.transcript_total(str(path)), 5 * 1024 ** 3)
            self.assertEqual(
                settings.transcript_archive_total(str(path)), 1 * 1024 ** 3)
            text = path.read_text()
            self.assertIn(settings.SESSION_LOG_MARKER, text)
            self.assertIn("KILIX_TRANSCRIPT=1", text)

    def test_session_logging_defaults_do_not_rewrite_an_existing_file(self):
        # Upgrades pick up new effective defaults without migrating user bytes.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            original = "# hand written\nKILIX_CHROME_CLOCK=0\n"
            path.write_text(original)
            settings.ensure_file(str(path))
            self.assertEqual(path.read_text(), original)
            self.assertTrue(settings.transcript_enabled(str(path)))
            self.assertEqual(settings.transcript_graphics(str(path)), "elide")
            self.assertEqual(
                settings.transcript_limit(str(path)), 8 * 1024 * 1024)
            self.assertEqual(
                settings.transcript_total(str(path)), 5 * 1024 ** 3)
            self.assertEqual(
                settings.transcript_archive_total(str(path)), 1 * 1024 ** 3)

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
                "--set", "transcript_total=10G",
                "--set", "transcript_archive=5G",
                "--set", "log_graphics=keep",
                "--print",
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn("KILIX_TRANSCRIPT=off", result.stdout)
            self.assertIn(
                f"{settings.TRANSCRIPT_LIMIT_KEY}=32M", result.stdout)
            self.assertIn(
                f"{settings.TRANSCRIPT_GRAPHICS_KEY}=keep", result.stdout)
            self.assertIn(
                f"{settings.TRANSCRIPT_TOTAL_KEY}=10G", result.stdout)
            self.assertIn(
                f"{settings.TRANSCRIPT_ARCHIVE_KEY}=5G", result.stdout)
            self.assertFalse(settings.transcript_enabled(str(path)))

            rejected = subprocess.run([
                str(ROOT / "kilix-settings"), "--set", "transcript_size=7M",
            ], env=env, text=True, capture_output=True, check=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("transcript size must be one of", rejected.stderr)

    def test_disable_all_leaves_qualifier_choices_valid(self):
        # KILIX_TRANSCRIPT is the main off switch. Qualifiers without an "off"
        # member retain valid defaults; the optional older tier may become off.
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
            self.assertIn(
                f"{settings.TRANSCRIPT_TOTAL_KEY}=5G", path.read_text())
            self.assertIn(
                f"{settings.TRANSCRIPT_ARCHIVE_KEY}=off", path.read_text())

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

            # The upgrade path: an existing file remains byte-exact, while
            # readers supply every newly introduced default dynamically.
            existing = Path(tmp) / "existing.conf"
            original = "# hand written\nKILIX_CHROME_CLOCK=0\n"
            existing.write_text(original)
            settings.ensure_file(str(existing))
            self.assertEqual(existing.read_text(), original)
            self.assertEqual(settings.stt_submit(str(existing)), "never")
            self.assertFalse(settings.voice_history(str(existing)))

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

    def test_speech_model_default_always_selects_its_matching_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            for model, engine in (
                ("small-en-us", "vosk"),
                ("lgraph-en-us", "vosk"),
                ("vibevoice-asr-bitnet", "vibevoice"),
            ):
                self.assertEqual(settings.stt_engine_for_model(model), engine)
                settings.set_stt_default(model, str(path))
                self.assertEqual(settings.stt_model(str(path)), model)
                self.assertEqual(settings.stt_engine(str(path)), engine)
            with self.assertRaisesRegex(ValueError, "unknown speech model"):
                settings.set_stt_default("not-in-the-catalog", str(path))

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

            defaulted = subprocess.run([
                str(ROOT / "kilix-settings"),
                "--default-model", "vibevoice-asr-bitnet", "--print",
            ], env=env, text=True, capture_output=True, check=True)
            self.assertIn(
                f"{settings.VOICE_STT_MODEL_KEY}=vibevoice-asr-bitnet",
                defaulted.stdout)
            self.assertIn(
                f"{settings.VOICE_STT_ENGINE_KEY}=vibevoice",
                defaulted.stdout)

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

    def test_voice_tui_install_key_returns_the_selected_default(self):
        module = _load_settings_tui()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SETTINGS_FILE": str(path),
                    "KITTY_PID": ""}, clear=False):
                screen = FakeScreen([ord("i")])
                result = module._run_tui(screen, "voice")
            self.assertEqual(result, "model:small-en-us")
            self.assertFalse(path.exists())
            self.assertEqual(module._model_install_argv("small-en-us"), [
                str(ROOT / "kilix"), "stt",
                "--install", "small-en-us", "--default", "small-en-us",
            ])

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
                    "KITTY_PID": ""}, clear=True):
                screen = FakeScreen([ord("n"), ord("s"), ord("q")])
                self.assertEqual(tui._run_tui(screen, "games"), 0)

            values = settings.load(str(path))
            self.assertTrue(all(
                not settings.truthy(values[key])
                for key in settings.GAME_KEY_BY_ID.values()))
            self.assertTrue(all(
                settings.truthy(values[spec.key]) == spec.effective_default()
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
                    "KITTY_PID": ""}, clear=True):
                screen = FakeScreen([
                    ord("j"), ord("j"), ord(" "), ord("s"), ord("q")])
                self.assertEqual(tui._run_tui(screen, "top-bar"), 0)

            self.assertFalse(settings.enabled(
                "KILIX_CHROME_VOLUME", str(path)))
            self.assertTrue(settings.enabled(
                "KILIX_CHROME_NETWORK", str(path)))
            self.assertFalse(settings.enabled(
                "KILIX_CHROME_TEMPERATURE", str(path)))
            first_frame = "\n".join(item[2] for item in screen.frames[0])
            self.assertIn("KILIX TUI", first_frame)
            self.assertIn("Kilix · Settings", first_frame)
            self.assertIn("▶1 Top bar", first_frame)
            self.assertIn("─" * 20, first_frame)
            self.assertNotIn(" // ", first_frame)
            self.assertIn("Top bar: 8/10 enabled", first_frame)
            self.assertIn("Thermal status", first_frame)
            self.assertIn("Volume", first_frame)
            self.assertIn("Read pane aloud", first_frame)
            self.assertIn("Dictate to pane", first_frame)

    def test_start_menu_default_is_deferred_and_desktop_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "settings.conf")
            with mock.patch.dict(os.environ, {}, clear=True):
                settings.ensure_file(path)
                self.assertFalse(settings.enabled(
                    "KILIX_CHROME_START_MENU", path))
            self.assertNotIn("KILIX_CHROME_START_MENU=", Path(path).read_text())
            with mock.patch.dict(os.environ, {
                    "XDG_CURRENT_DESKTOP": "Pleb"}, clear=True):
                self.assertTrue(settings.enabled(
                    "KILIX_CHROME_START_MENU", path))
            settings.update({"KILIX_CHROME_START_MENU": "off"}, path)
            with mock.patch.dict(os.environ, {
                    "XDG_CURRENT_DESKTOP": "Pleb"}, clear=True):
                self.assertFalse(settings.enabled(
                    "KILIX_CHROME_START_MENU", path))

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

    def test_tui_cycles_pane_cpu_and_memory_modes(self):
        tui = _load_settings_tui()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SETTINGS_FILE": str(path),
                    "KITTY_PID": ""}, clear=False):
                # CPU and memory are the first two rows: auto -> always.
                screen = FakeScreen([
                    ord(" "), ord("j"), ord(" "), ord("s"), ord("q")
                ])
                self.assertEqual(tui._run_tui(screen, "pane-buttons"), 0)
            self.assertEqual(settings.pane_cpu_mode(str(path)), "always")
            self.assertEqual(settings.pane_memory_mode(str(path)), "always")
            first_frame = "\n".join(item[2] for item in screen.frames[0])
            self.assertIn("Pane CPU use", first_frame)
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


class TranscriptBudgetTests(unittest.TestCase):
    """The directory-level budgets that bound session logging over time.

    The per-pane cap bounds one file; without these a long-running kiosk grows
    without limit, because panes come and go and nothing reclaims a dead pane's
    log.
    """

    def test_defaults_are_written_and_readable_as_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            settings.ensure_file(str(path))
            text = path.read_text()
            self.assertIn(
                f"{settings.TRANSCRIPT_TOTAL_KEY}="
                f"{settings.TRANSCRIPT_TOTAL_DEFAULT}", text)
            self.assertIn(
                f"{settings.TRANSCRIPT_ARCHIVE_KEY}="
                f"{settings.TRANSCRIPT_ARCHIVE_DEFAULT}", text)
            self.assertEqual(
                settings.transcript_total(str(path)), 5 * 1024 ** 3)
            self.assertEqual(
                settings.transcript_archive_total(str(path)), 1 * 1024 ** 3)

    def test_archive_off_reports_zero_so_logs_are_deleted_not_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            settings.update({settings.TRANSCRIPT_ARCHIVE_KEY: "off"}, str(path))
            self.assertEqual(settings.transcript_archive_total(str(path)), 0)

    def test_unknown_budget_is_rejected_rather_than_silently_defaulted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with self.assertRaises(ValueError):
                settings.update({settings.TRANSCRIPT_TOTAL_KEY: "7G"}, str(path))
            with self.assertRaises(ValueError):
                settings.update(
                    {settings.TRANSCRIPT_ARCHIVE_KEY: "nonsense"}, str(path))

    def test_a_corrupt_value_reads_back_as_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            path.write_text(f"{settings.TRANSCRIPT_TOTAL_KEY}=banana\n")
            self.assertEqual(
                settings.transcript_total(str(path)), 5 * 1024 ** 3)

    def test_cli_accepts_tokens_raw_bytes_and_off(self):
        tui = _load_settings_tui()
        self.assertEqual(
            tui._parse_assignment("transcript_total=10G"),
            (settings.TRANSCRIPT_TOTAL_KEY, "10G"))
        # A scripted caller need not know which spelling the file uses.
        self.assertEqual(
            tui._parse_assignment(f"transcript_total={20 * 1024 ** 3}"),
            (settings.TRANSCRIPT_TOTAL_KEY, "20G"))
        self.assertEqual(
            tui._parse_assignment("transcript_archive=off"),
            (settings.TRANSCRIPT_ARCHIVE_KEY, "off"))
        with self.assertRaises(argparse.ArgumentTypeError):
            tui._parse_assignment("transcript_total=7G")

    def test_tui_session_logging_section_exposes_both_budgets(self):
        tui = _load_settings_tui()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.conf"
            with mock.patch.dict(os.environ, {
                    "GPU_TERMINAL_SETTINGS_FILE": str(path),
                    "KITTY_PID": ""}, clear=False):
                screen = FakeScreen([ord("q")])
                tui._run_tui(screen, "session-logging")
            frame = "\n".join(item[2] for item in screen.frames[0])
            self.assertIn("Recent logs (zstd -3)", frame)
            self.assertIn("Older logs (zstd -9)", frame)

    def test_launcher_reaps_for_the_frontend_lifetime(self):
        launcher = (ROOT / "kilix").read_text()
        self.assertIn("_kilix_transcript_reap_periodically()", launcher)
        self.assertIn('while kill -0 "$frontend_pid"', launcher)
        self.assertIn('flock -n 9', launcher)


class TranscriptArchiveIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("zstd"), "zstd is required")
    def test_dead_log_moves_through_both_tiers_and_reads_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            gpu_home = Path(tmp) / "gpu"
            transcript_dir = gpu_home / "kilix" / "state" / "transcripts"
            transcript_dir.mkdir(parents=True, mode=0o700)
            source = transcript_dir / "dead-session.log"
            payload = b"first line\nsecond line\n" * 128
            source.write_bytes(payload)
            source.chmod(0o600)

            env = {
                key: value for key, value in os.environ.items()
                if not key.startswith(("KILIX", "GPU_TERMINAL"))
            }
            env["GPU_TERMINAL_HOME"] = str(gpu_home)
            env["GPU_TERMINAL_SOURCE_HOME"] = str(ROOT.parent)
            command = [str(ROOT / "kilix"), "transcript"]

            subprocess.run(
                command + ["prune"], env=env, capture_output=True, check=True)
            recent = transcript_dir / "recent" / "dead-session.log.zst"
            self.assertFalse(source.exists())
            self.assertTrue(recent.is_file())
            self.assertEqual(
                subprocess.run(
                    command + ["show", "dead-session"], env=env,
                    capture_output=True, check=True,
                ).stdout,
                payload,
            )
            self.assertEqual(
                subprocess.run(
                    command + ["path", "dead-session"], env=env,
                    text=True, capture_output=True, check=True,
                ).stdout.strip(),
                str(recent),
            )

            subprocess.run(
                command + ["archive"], env=env, capture_output=True, check=True)
            older = transcript_dir / "archive" / "dead-session.log.zst"
            self.assertFalse(recent.exists())
            self.assertTrue(older.is_file())
            self.assertEqual(stat.S_IMODE(older.stat().st_mode), 0o600)
            self.assertEqual(
                subprocess.run(
                    command + ["show", "dead-session"], env=env,
                    capture_output=True, check=True,
                ).stdout,
                payload,
            )
            listing = subprocess.run(
                command, env=env, text=True, capture_output=True, check=True)
            self.assertIn("dead-session.log.zst [older]", listing.stdout)


if __name__ == "__main__":
    unittest.main()
