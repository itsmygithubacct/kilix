"""kilix run's pane never exits or loses focus with a key held down."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPRUN = (ROOT / "config" / "apprun.py").read_text()


class ForwardingDisciplineTests(unittest.TestCase):
    def test_keys_are_forwarded_as_chords_not_raw(self):
        self.assertIn("self.inj.chord(ev[\"key\"], mods, etype)", APPRUN)
        self.assertNotIn("self.inj.key(ev[\"key\"], etype)", APPRUN)

    def test_focus_reporting_is_enabled_and_disabled_with_the_pane(self):
        self.assertIn("?1004h", APPRUN)
        self.assertIn("?1004l", APPRUN)

    def test_focus_out_releases_everything(self):
        on_focus = re.search(r"def on_focus\(self, ev\):.*?\n(?=    def )", APPRUN, re.S)
        self.assertIsNotNone(on_focus)
        self.assertIn("release_all()", on_focus.group(0))
        self.assertIn('elif ev["kind"] == "focus":', APPRUN)

    def test_every_exit_path_releases_everything(self):
        finally_block = re.search(r"        finally:\n(.*?)\n            if self\.term:\n                self\.term\.restore\(\)", APPRUN, re.S)
        self.assertIsNotNone(finally_block)
        self.assertIn("release_all()", finally_block.group(1))


if __name__ == "__main__":
    unittest.main()
