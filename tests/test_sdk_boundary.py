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
                    Path(paths.kilix95_home()), home / "gpu_terminal" / "kilix-95")
            custom = Path(tmp) / "sources" / "desktop"
            with mock.patch.dict(os.environ, {"KILIX95_DIR": str(custom)}):
                self.assertEqual(Path(paths.kilix95_home()), custom)

    def test_sdk_contract_is_versioned(self):
        # 1.5 adds the shared session-logging settings both providers read.
        self.assertEqual(kilix_sdk.SDK_API_VERSION, (1, 5))
        kilix_sdk.require_compatible("1.0")
        kilix_sdk.require_compatible("1.5")
        with self.assertRaises(kilix_sdk.IncompatibleSDKError):
            kilix_sdk.require_compatible("1.6")
        with self.assertRaises(kilix_sdk.IncompatibleSDKError):
            kilix_sdk.require_compatible("2.0")

    def test_session_logging_settings_are_part_of_the_sdk_contract(self):
        # A provider compiled against 1.5 may rely on these names existing.
        for name in (
            "TRANSCRIPT_GRAPHICS_KEY", "TRANSCRIPT_GRAPHICS_CHOICES",
            "TRANSCRIPT_GRAPHICS_DEFAULT", "TRANSCRIPT_LIMIT_KEY",
            "TRANSCRIPT_LIMIT_CHOICES", "TRANSCRIPT_LIMIT_DEFAULT",
            "transcript_enabled", "transcript_graphics", "transcript_limit",
        ):
            self.assertTrue(hasattr(settings, name), name)
        self.assertIn("KILIX_TRANSCRIPT", settings.TOGGLE_BY_KEY)
        self.assertIn(settings.TRANSCRIPT_GRAPHICS_KEY, settings.MANAGED_KEYS)
        self.assertIn(settings.TRANSCRIPT_LIMIT_KEY, settings.MANAGED_KEYS)

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
