import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from kilix_sdk import graphics, paths, term


class KilixSdkBoundaryTests(unittest.TestCase):
    def test_paths_resolve_to_host_checkout(self):
        self.assertEqual(Path(paths.kilix_home()), ROOT)
        self.assertEqual(Path(paths.config_dir()), ROOT / "config")
        self.assertEqual(Path(paths.launcher()), ROOT / "kilix")

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
