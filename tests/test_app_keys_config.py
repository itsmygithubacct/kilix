"""Kilix hands its window keys to a program that asks: a window whose
program sets the user variable kilix_app_keys (Zell's pane rooms do) gets
Alt+arrows, Ctrl+Tab, Ctrl+Shift+Tab and Ctrl+Alt+Z itself.

kitty takes the last matching map, and a map with no action unmaps the key
for the windows its --when-focus-on expression matches (kitty_tests/keys.py
covers that). So each key needs its own map first, then the unmap.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONF = ROOT / 'config' / 'kitty.conf'
KEYS = ('alt+left', 'alt+right', 'alt+up', 'alt+down', 'ctrl+tab', 'ctrl+shift+tab', 'ctrl+alt+z')
WHEN = '--when-focus-on var:kilix_app_keys'


def maps(text):
    """(line number, key, action, when) for each map outside a mode."""
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        words = line.split()
        if not words or words[0] != 'map' or '--mode' in words or '--new-mode' in words:
            continue
        words, when = words[1:], ''
        if words[:1] == ['--when-focus-on']:
            when, words = words[1], words[2:]
        out.append((number, words[0], ' '.join(words[1:]), when))
    return out


class AppKeysTests(unittest.TestCase):
    def test_each_window_key_is_handed_over_after_kilix_maps_it(self):
        found = maps(CONF.read_text())
        for key in KEYS:
            ours = [m for m in found if m[1] == key and not m[3]]
            handed = [m for m in found if m[1] == key and m[3]]
            self.assertTrue(ours, key)
            self.assertEqual(len(handed), 1, key)
            number, _key, action, when = handed[0]
            self.assertEqual((action, when), ('', 'var:kilix_app_keys'), key)
            # After every map of its own: the last match wins.
            self.assertGreater(number, max(m[0] for m in ours), key)

    def test_nothing_else_is_handed_over(self):
        handed = {m[1] for m in maps(CONF.read_text()) if m[3] == 'var:kilix_app_keys'}
        self.assertEqual(handed, set(KEYS))
        self.assertEqual(CONF.read_text().count(WHEN), len(KEYS))


if __name__ == '__main__':
    unittest.main()
