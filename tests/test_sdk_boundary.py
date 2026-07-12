import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import kilix_sdk
from kilix_sdk import graphics, paths, term


class KilixSdkBoundaryTests(unittest.TestCase):
    def test_paths_resolve_to_host_checkout(self):
        self.assertEqual(Path(paths.kilix_home()), ROOT)
        self.assertEqual(Path(paths.defaults_dir()), ROOT / "config")
        self.assertEqual(Path(paths.launcher()), ROOT / "kilix")

    def test_user_config_uses_xdg_and_honors_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {
                    "XDG_CONFIG_HOME": tmp, "KITTY_CONFIG_DIRECTORY": ""}):
                self.assertEqual(Path(paths.config_dir()), Path(tmp) / "kilix")
            override = str(Path(tmp) / "custom")
            with mock.patch.dict(os.environ, {"KITTY_CONFIG_DIRECTORY": override}):
                self.assertEqual(Path(paths.config_dir()), Path(override))

    def test_sdk_contract_is_versioned(self):
        self.assertEqual(kilix_sdk.SDK_API_VERSION, (1, 0))
        kilix_sdk.require_compatible("1.0")
        with self.assertRaises(kilix_sdk.IncompatibleSDKError):
            kilix_sdk.require_compatible("2.0")

    def test_term_exposes_parser_contract(self):
        self.assertTrue(hasattr(term.Term, "read_input"))
        self.assertIn("A", term.SPECIAL_CSI)
        self.assertIn(13, term.SPECIAL_U)

    def test_graphics_exposes_public_tmux_wrapper(self):
        wrapped = graphics.wrap_tmux_passthrough("\x1b_Ga=d,d=A\x1b\\")
        self.assertTrue(wrapped.startswith("\x1bPtmux;"))
        self.assertIn("\x1b\x1b_G", wrapped)


if __name__ == "__main__":
    unittest.main()
