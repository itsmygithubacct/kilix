"""The injector never leaves a modifier behind.

A bare Alt press forwarded into the private display, whose release then went
to another pane, latched Mod1 in the X server and turned every later key into
an Alt chord. These tests pin the replacement rule: modifiers are injected
around the key that needs them and released with it, bare modifiers are never
injected alone, and release_all() leaves nothing down.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import xinject  # noqa: E402
from Xlib import X, XK  # noqa: E402

KEYCODES = {ord("a"): 38, ord("l"): 46,
            XK.string_to_keysym("Shift_L"): 50,
            XK.string_to_keysym("Alt_L"): 64,
            XK.string_to_keysym("Control_L"): 37,
            XK.string_to_keysym("Super_L"): 133}
ALT_KEY = chr(57443)          # kitty functional keycode for a bare Alt_L


class FakeDisplay:
    def keysym_to_keycode(self, keysym):
        return KEYCODES.get(keysym, 0)

    def flush(self):
        pass


class ChordTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        patcher = mock.patch.object(
            xinject.xtest, "fake_input",
            lambda xd, etype, detail=0, **kw: self.events.append((etype, detail)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.inj = xinject.Injector(FakeDisplay(), 640, 480)

    def test_a_bare_modifier_is_never_injected(self):
        self.assertFalse(self.inj.chord(ALT_KEY, 0, 1))
        self.assertFalse(self.inj.chord(ALT_KEY, 0, 3))
        self.assertEqual(self.events, [])
        self.assertEqual(self.inj._keys_down, set())

    def test_the_control_a_plain_key_is_injected(self):
        # Without this, "nothing injected" and "the fake never records" are
        # the same observation.
        self.assertTrue(self.inj.chord("a", 0, 1))
        self.assertEqual(self.events, [(X.KeyPress, 38)])

    def test_modifiers_wrap_the_key_and_leave_with_it(self):
        self.inj.chord("l", xinject.MOD_ALT, 1)
        self.inj.chord("l", xinject.MOD_ALT, 3)
        self.assertEqual(self.events, [
            (X.KeyPress, 64), (X.KeyPress, 46),
            (X.KeyRelease, 46), (X.KeyRelease, 64)])
        self.assertEqual(self.inj._keys_down, set(),
                         "a chord must leave nothing held down")

    def test_overlapping_chords_share_a_modifier_by_count(self):
        # Ctrl held, A pressed, then L pressed, then A released: Ctrl must stay
        # down for L, and go up only when L goes up.
        self.inj.chord("a", xinject.MOD_CTRL, 1)
        self.inj.chord("l", xinject.MOD_CTRL, 1)
        self.inj.chord("a", xinject.MOD_CTRL, 3)
        self.assertIn(37, self.inj._keys_down, "Ctrl dropped while L still needs it")
        self.inj.chord("l", xinject.MOD_CTRL, 3)
        self.assertNotIn(37, self.inj._keys_down)
        presses = [e for e in self.events if e == (X.KeyPress, 37)]
        releases = [e for e in self.events if e == (X.KeyRelease, 37)]
        self.assertEqual((len(presses), len(releases)), (1, 1))

    def test_release_all_lets_go_of_everything_and_forgets_the_counts(self):
        self.inj.chord("a", xinject.MOD_SHIFT | xinject.MOD_SUPER, 1)
        self.inj.release_all()
        self.assertEqual(self.inj._keys_down, set())
        self.assertEqual(self.inj._mod_holds, {})
        released = {d for t, d in self.events if t == X.KeyRelease}
        self.assertEqual(released, {38, 50, 133})

    def test_share_path_key_is_unchanged(self):
        # kilix share still forwards a viewer's raw press/release through key().
        self.assertTrue(self.inj.key("a", 1))
        self.assertEqual(self.events, [(X.KeyPress, 38)])


if __name__ == "__main__":
    unittest.main()
