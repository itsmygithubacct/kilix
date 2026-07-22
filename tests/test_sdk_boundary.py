import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import kilix_sdk
from kilix_sdk import content, graphics, paths, term


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
        self.assertEqual(kilix_sdk.SDK_API_VERSION, (1, 3))
        kilix_sdk.require_compatible("1.0")
        with self.assertRaises(kilix_sdk.IncompatibleSDKError):
            kilix_sdk.require_compatible("2.0")

    def test_content_exposes_pinned_catalog_contract(self):
        catalog = content.default_catalog()
        lander = catalog.require("terminal-lander")
        self.assertEqual(lander.source_type, "git")
        self.assertEqual(len(lander.ref), 40)
        self.assertEqual(catalog.require("kilix-rancher").binary,
                         "kilix-rancher")
        self.assertEqual(catalog.require("kilix-pong").launch_mode,
                         "terminal")
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


if __name__ == "__main__":
    unittest.main()
