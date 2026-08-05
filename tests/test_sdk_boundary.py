import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import kilix_sdk
from kilix_sdk import content, graphics, paths, settings, state, term


class KilixSdkBoundaryTests(unittest.TestCase):
    def test_paths_resolve_to_host_checkout(self):
        self.assertEqual(Path(paths.kilix_home()), ROOT)
        self.assertEqual(Path(paths.defaults_dir()), ROOT / "config")
        self.assertEqual(Path(paths.launcher()), ROOT / "kilix")

    def test_user_config_uses_project_storage_and_honors_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {
                    "KILIX_STORAGE_HOME": tmp,
                    "KILIX_CONFIG_HOME": "",
                    "KITTY_CONFIG_DIRECTORY": ""}):
                self.assertEqual(Path(paths.config_dir()), Path(tmp) / "config")
            override = str(Path(tmp) / "custom")
            with mock.patch.dict(os.environ, {"KITTY_CONFIG_DIRECTORY": override}):
                self.assertEqual(Path(paths.config_dir()), Path(override))

    def test_gpu_terminal_source_layout_and_external_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                os.environ.pop("GPU_TERMINAL_SOURCE_HOME", None)
                os.environ.pop("KILIX95_DIR", None)
                self.assertEqual(Path(paths.source_home()), home / "gpu_terminal")
                self.assertEqual(
                    Path(paths.kilix95_home()),
                    home / "gpu_terminal" / "kilix-desktops" / "kilix-95")
            custom = Path(tmp) / "sources" / "desktop"
            with mock.patch.dict(os.environ, {"KILIX95_DIR": str(custom)}):
                self.assertEqual(Path(paths.kilix95_home()), custom)

    def test_sdk_contract_is_versioned(self):
        # 1.5 added the shared session-logging settings both providers read;
        # 1.6 adds the shared voice settings behind the two chrome widgets;
        # 1.7 adds the shared coding-agent policy the Settings app reads;
        # 1.8 adds the freedesktop application scanner (xdgapps);
        # 1.9 adds xdgapps.entries_in and the grouped(force=) cache refresh.
        self.assertEqual(kilix_sdk.SDK_API_VERSION, (1, 9))
        self.assertEqual(kilix_sdk.SDK_VERSION, "1.9.0")
        kilix_sdk.require_compatible("1.0")
        kilix_sdk.require_compatible("1.5")
        kilix_sdk.require_compatible("1.6")
        kilix_sdk.require_compatible("1.7")
        kilix_sdk.require_compatible("1.8")
        kilix_sdk.require_compatible("1.9")
        with self.assertRaises(kilix_sdk.IncompatibleSDKError):
            kilix_sdk.require_compatible("1.10")
        with self.assertRaises(kilix_sdk.IncompatibleSDKError):
            kilix_sdk.require_compatible("2.0")

    def test_xdgapps_scanner_is_part_of_the_sdk_contract(self):
        # 1.8: one freedesktop scanner for every desktop and the launcher
        # catalog. Discovery only — launching stays with each consumer.
        # 1.9: entries_in reads a folder of user launchers with the same
        # parser, and grouped(force=) refreshes the scan cache.
        from kilix_sdk import xdgapps
        for name in ("scan", "grouped", "bucket", "app_dirs", "entries_in",
                     "parse_desktop_file", "build_entry", "BUCKET_ORDER"):
            self.assertTrue(hasattr(xdgapps, name), name)
            self.assertIn(name, xdgapps.__all__)
        with tempfile.TemporaryDirectory() as tmp:
            apps = Path(tmp) / "data" / "applications"
            apps.mkdir(parents=True)
            (apps / "sample.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Sample\n"
                "Exec=/usr/bin/true %F\nCategories=Utility;\n")
            with mock.patch.dict(os.environ, {
                    "XDG_DATA_HOME": str(Path(tmp) / "data"),
                    "XDG_DATA_DIRS": str(Path(tmp) / "empty")}):
                entries = xdgapps.scan(force=True)
                self.assertEqual([e["name"] for e in entries], ["Sample"])
                self.assertEqual(entries[0]["exec"], "/usr/bin/true")
                self.assertEqual(xdgapps.bucket(entries[0]), "Accessories")
                groups = xdgapps.grouped(force=True)
                self.assertEqual(list(groups), ["Accessories"])
            folder = xdgapps.entries_in(str(apps))
            self.assertEqual([e["name"] for e in folder], ["Sample"])
            self.assertEqual(folder[0]["id"], "sample.desktop")
        parsed = xdgapps.parse_desktop_file(str(apps / "sample.desktop"))
        self.assertIsNone(parsed)          # the temp dir is gone: no crash
        self.assertEqual(xdgapps.entries_in(str(apps)), [])

    def test_voice_settings_are_part_of_the_sdk_contract(self):
        # A provider compiled against 1.6 may rely on these names existing.
        for name in (
            "VOICE_MARKER", "VOICE_KEYS", "VOICE_VALUE_KEYS",
            "VOICE_CHOICE_SPECS", "VOICE_TOKEN_SPECS", "VOICE_TOGGLES",
            "tts_engine", "tts_voice", "tts_rate", "tts_extent",
            "tts_max_chars", "stt_engine", "stt_model", "stt_submit",
            "stt_max_seconds", "stt_silence_ms", "voice_device_in",
            "voice_device_out", "voice_history",
        ):
            self.assertTrue(hasattr(settings, name), name)
        for key in ("KILIX_CHROME_SPEAK", "KILIX_CHROME_DICTATE",
                    settings.VOICE_PUNCTUATION_KEY):
            self.assertIn(key, settings.TOGGLE_BY_KEY)
        for key in settings.VOICE_VALUE_KEYS:
            self.assertIn(key, settings.MANAGED_KEYS)
        # The one value that must never gain a third choice.
        self.assertEqual(
            settings.VOICE_CHOICE_SPECS[settings.VOICE_STT_SUBMIT_KEY],
            ("never", ("never", "confirm")))

    def test_coding_agent_setting_is_part_of_the_sdk_contract(self):
        for name in (
            "CODING_MARKER", "CODING_KEYS", "CODING_CHOICE_SPECS",
            "CODING_YOLO_KEY", "CODING_YOLO_DEFAULT", "CODING_YOLO_CHOICES",
            "coding_yolo",
        ):
            self.assertTrue(hasattr(settings, name), name)
            self.assertIn(name, settings.__all__)
        self.assertIn(settings.CODING_YOLO_KEY, settings.MANAGED_KEYS)
        self.assertEqual(
            settings.CODING_CHOICE_SPECS[settings.CODING_YOLO_KEY],
            ("off", ("off", "on")))

    def test_session_logging_settings_are_part_of_the_sdk_contract(self):
        # A provider compiled against 1.5 may rely on these names existing.
        for name in (
            "TRANSCRIPT_GRAPHICS_KEY", "TRANSCRIPT_GRAPHICS_CHOICES",
            "TRANSCRIPT_GRAPHICS_DEFAULT", "TRANSCRIPT_LIMIT_KEY",
            "TRANSCRIPT_LIMIT_CHOICES", "TRANSCRIPT_LIMIT_DEFAULT",
            "TRANSCRIPT_TOTAL_KEY", "TRANSCRIPT_TOTAL_CHOICES",
            "TRANSCRIPT_TOTAL_DEFAULT", "TRANSCRIPT_ARCHIVE_KEY",
            "TRANSCRIPT_ARCHIVE_CHOICES", "TRANSCRIPT_ARCHIVE_DEFAULT",
            "transcript_enabled", "transcript_graphics", "transcript_limit",
            "transcript_total", "transcript_archive_total",
        ):
            self.assertTrue(hasattr(settings, name), name)
            self.assertIn(name, settings.__all__)
        self.assertIn("KILIX_TRANSCRIPT", settings.TOGGLE_BY_KEY)
        self.assertIn(settings.TRANSCRIPT_GRAPHICS_KEY, settings.MANAGED_KEYS)
        self.assertIn(settings.TRANSCRIPT_LIMIT_KEY, settings.MANAGED_KEYS)
        self.assertIn(settings.TRANSCRIPT_TOTAL_KEY, settings.MANAGED_KEYS)
        self.assertIn(settings.TRANSCRIPT_ARCHIVE_KEY, settings.MANAGED_KEYS)

    def test_content_exposes_pinned_catalog_contract(self):
        catalog = content.default_catalog()
        lander = catalog.require("terminal-lander")
        self.assertEqual(lander.source_type, "git")
        self.assertEqual(len(lander.ref), 40)
        self.assertEqual(catalog.require("kilix-rancher").binary,
                         "kilix-rancher")
        self.assertEqual(catalog.require("kilix-pong").launch_mode,
                         "terminal")
        self.assertEqual(catalog.require("kilix-lights").binary,
                         "bin/kilix-lights")
        self.assertEqual(catalog.require("super-kilix").binary,
                         "super-kilix")
        self.assertIs(content.InstallError, __import__(
            "kilix_content", fromlist=["InstallError"]).InstallError)

    def test_term_exposes_parser_contract(self):
        self.assertTrue(hasattr(term.Term, "read_input"))
        self.assertIn("A", term.SPECIAL_CSI)
        self.assertIn(13, term.SPECIAL_U)

    def test_graphics_exposes_public_tmux_wrapper(self):
        wrapped = graphics.wrap_tmux_passthrough("\x1b_Ga=d,d=A\x1b\\")
        self.assertTrue(wrapped.startswith("\x1bPtmux;"))
        self.assertIn("\x1b\x1b_G", wrapped)

    def test_graphics_exposes_shared_presenter_contract(self):
        self.assertIs(graphics.FramePresenter,
                      __import__("gfx").FramePresenter)
        self.assertEqual(graphics.FRAME_BYTES, 3)
        self.assertTrue(callable(graphics.diff_rect))

    def test_graphics_exposes_exclusive_frame_writer(self):
        self.assertIs(graphics.write_frame, __import__("gfx").write_frame)

    def test_state_exposes_pinned_python_binding(self):
        self.assertEqual(state.KILIX_STATE_ABI, (0, 4))
        self.assertEqual(state.binding_version, "0.1.0")
        self.assertEqual(state.Store.__mro__[1].__module__, "kilix_state.store")


if __name__ == "__main__":
    unittest.main()
