"""Keys typed under a non-US pane layout reach the private display intact.

The private Xvfb keeps its default US keymap. In the RC5 VM, with the
physical layout switched to German, a private GTK app received only "zy"
for the keys z y a-umlaut o-umlaut u-umlaut sharp-s: every character the
private keymap lacked was silently dropped, and text pasted or dropped into a
private app lost its non-ASCII characters the same way.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import apprun
import xinject
from Xlib import X, XK, display

MODIFIER_KEYSYMS = {XK.string_to_keysym(name) for name in (
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R")}


@unittest.skipUnless(shutil.which("Xvfb"), "private Xvfb unavailable")
class LayoutIndependentKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="kilix-keyboard-layout-")
        cls.addClassCleanup(cls.temp.cleanup)
        path = Path(cls.temp.name)
        cls.log = (path / "xvfb.log").open("w")
        cls.addClassCleanup(cls.log.close)
        ready = path / "display"
        with ready.open("w") as fd:
            cls.server = subprocess.Popen(
                [shutil.which("Xvfb"), "-displayfd", str(fd.fileno()), "-screen", "0", "640x480x24",
                 "-nolisten", "tcp"], pass_fds=(fd.fileno(),), stdout=subprocess.DEVNULL, stderr=cls.log)
        cls.addClassCleanup(cls.stop_server)
        deadline = time.monotonic() + 5
        while not ready.stat().st_size and time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError("private Xvfb exited")
            time.sleep(.02)
        number = ready.read_text().strip()
        if not number.isdigit():
            raise RuntimeError("private Xvfb did not report a display")
        cls.display_name = ":" + number
        # The injector's connection and an independent client that reads the
        # keymap and events the way an application does.
        cls.xd = display.Display(cls.display_name)
        cls.addClassCleanup(cls.xd.close)
        cls.app = display.Display(cls.display_name)
        cls.addClassCleanup(cls.app.close)

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait(timeout=5)

    def setUp(self):
        self.inj = xinject.Injector(self.xd, 640, 480)
        self.addCleanup(self.inj.release_all)
        self.term = object.__new__(apprun.RunTerm)
        screen = self.app.screen()
        self.window = screen.root.create_window(
            0, 0, 320, 200, 0, screen.root_depth,
            event_mask=X.KeyPressMask | X.KeyReleaseMask)
        self.addCleanup(self.window.destroy)
        self.window.map()
        self.window.set_input_focus(X.RevertToParent, X.CurrentTime)
        self.app.sync()
        self.typed()

    def inject(self, params):
        ev = self.term._parse_csi(params, "u")
        extra = {"shifted": ev["shifted"]} if "shifted" in ev else {}
        self.inj.chord(ev["key"], ev["mods"] - 1, ev["event"], **extra)
        self.xd.sync()

    def typed(self):
        """Keysyms the application sees, resolved with its own keymap."""
        self.app.sync()
        time.sleep(.05)
        out = []
        while self.app.pending_events():
            ev = self.app.next_event()
            if ev.type == X.MappingNotify:
                self.app.refresh_keyboard_mapping(ev)
            elif ev.type == X.KeyPress:
                sym = self.app.keycode_to_keysym(ev.detail, 1 if ev.state & X.ShiftMask else 0)
                if sym not in MODIFIER_KEYSYMS:
                    out.append((sym, ev.state & (X.ShiftMask | X.ControlMask)))
        return out

    def held(self):
        bitmap = bytes(self.xd.query_keymap())
        return [i for i in range(256) if bitmap[i // 8] & (1 << (i % 8))]

    def test_german_letters_absent_from_the_private_keymap_are_typed(self):
        # The pane reports each German key by the code point its layout gives.
        for char in "äöüß":
            self.inject(f"{ord(char)};1:1")
            self.inject(f"{ord(char)};1:3")
        self.assertEqual([sym for sym, _ in self.typed()], [ord(c) for c in "äöüß"])
        self.assertEqual(self.held(), [])

    def test_a_shifted_glyph_follows_the_pane_layout_not_the_us_keymap(self):
        # German Shift+7 is '/', reported as base '7' with alternate '/'.
        self.inject("55:47;2:1")
        self.inject("57441;1:3")
        self.inject("55;1:3")
        # German Shift+2 is '"' and an unshifted '<' needs Shift in a US keymap.
        self.inject("50:34;2:1")
        self.inject("50;1:3")
        self.inject("60;1:1")
        self.inject("60;1:3")
        self.assertEqual([sym for sym, _ in self.typed()], [ord("/"), ord('"'), ord("<")])
        self.assertEqual(self.held(), [])
        self.assertEqual(self.xd.screen().root.query_pointer().mask & X.ShiftMask, 0)

    def test_letters_above_latin1_use_unicode_keysyms(self):
        for char in "жΩ€":
            self.inject(f"{ord(char)};1:1")
            self.inject(f"{ord(char)};1:3")
        self.assertEqual([sym for sym, _ in self.typed()],
                         [xinject.UNICODE_KEYSYM | ord(c) for c in "жΩ€"])
        self.assertEqual(self.held(), [])

    def test_paste_keeps_case_symbols_and_non_ascii(self):
        text = "Ab!/ü 漢\n"
        self.inj.paste(text)
        self.xd.sync()
        expected = [ord("A"), ord("b"), ord("!"), ord("/"), ord("ü"), ord(" "),
                    xinject.UNICODE_KEYSYM | ord("漢"), XK.XK_Return]
        self.assertEqual([sym for sym, _ in self.typed()], expected)
        self.assertEqual(self.held(), [])

    def test_control_chords_still_use_the_base_key_and_modifier(self):
        self.inject("99;5:1")
        self.inject("99;5:3")
        self.assertEqual(self.typed(), [(ord("c"), X.ControlMask)])
        self.assertEqual(self.held(), [])

    def test_lock_keys_do_not_defeat_the_shifted_glyph(self):
        # The keyboard protocol reports NumLock (128) and CapsLock (64) in the
        # modifier field. German Shift+7 is still '/' with either, or both, on.
        for params in ("55:47;130:1", "55:47;66:1", "55:47;194:1"):
            self.inject(params)
            self.inject("55;1:3")
        self.assertEqual([sym for sym, _ in self.typed()], [ord("/")] * 3)
        self.assertEqual(self.held(), [])
        self.assertEqual(self.xd.screen().root.query_pointer().mask & X.ShiftMask, 0)

    def test_lock_keys_do_not_defeat_an_unshifted_glyph_or_a_chord(self):
        # '<' is typed as the glyph the pane produced, not as a lock-shifted key.
        self.inject("60;129:1")
        self.inject("60;129:3")
        # Ctrl+c is still a Ctrl chord on the base key, and Ctrl alone.
        self.inject("99;133:1")
        self.inject("99;133:3")
        typed = self.typed()
        self.assertEqual([sym for sym, _ in typed], [ord("<"), ord("c")])
        self.assertEqual(typed[1][1], X.ControlMask)
        self.assertEqual(self.held(), [])

    def test_control_characters_are_not_keysyms(self):
        for code in list(range(0x20)) + list(range(0x7f, 0xa0)):
            self.assertEqual(self.inj.keysym_for(chr(code)), 0, hex(code))
        self.assertEqual(self.inj.keysym_for(" "), 0x20)
        self.assertEqual(self.inj.keysym_for("~"), 0x7e)
        self.assertEqual(self.inj.keysym_for("\xa0"), 0xa0)

    def test_paste_types_tab_and_drops_other_control_characters(self):
        self.inj.paste("a\tb\r\x01c\x7fd")
        self.xd.sync()
        self.assertEqual([sym for sym, _ in self.typed()],
                         [ord("a"), XK.XK_Tab, ord("b"), ord("c"), ord("d")])
        self.assertEqual(self.held(), [])
        # No spare keycode was spent on a control character.
        self.assertEqual(self.inj._scratch, {})

    def test_more_new_symbols_than_spare_keycodes_are_all_typed(self):
        # A responsive client reads each key before the next one; once every
        # spare keycode is bound, the least recently used one is rebound.
        spare = len(self.inj._find_spare())
        self.assertGreater(spare, 0)
        chars = [chr(0x410 + i) for i in range(spare + 4)]
        seen = []
        for char in chars:
            self.inj.paste(char)
            self.xd.sync()
            seen += [sym for sym, _ in self.typed()]
        self.assertEqual(seen, [xinject.UNICODE_KEYSYM | ord(c) for c in chars])
        self.assertLessEqual(len(set(self.inj._scratch.values())), spare)

    def test_a_rebound_keycode_is_not_used_for_its_old_symbol(self):
        # An earlier injector left 'ä' bound to a spare keycode, so a new
        # connection's keymap cache maps 'ä' to it. Once that keycode is
        # rebound to another symbol, typing 'ä' must not press it.
        self.inj.paste("ä")
        self.xd.sync()
        self.typed()
        fresh = display.Display(self.display_name)
        self.addCleanup(fresh.close)
        inj = xinject.Injector(fresh, 640, 480)
        self.addCleanup(inj.release_all)
        stale = fresh.keysym_to_keycode(ord("ä"))
        self.assertTrue(stale, "the earlier binding is visible to a new connection")
        chars = [chr(0x430 + i) for i in range(len(inj._find_spare()))] + ["ä"]
        seen = []
        for char in chars:
            inj.paste(char)
            fresh.sync()
            seen += [sym for sym, _ in self.typed()]
        self.assertEqual(seen[-1], ord("ä"))
        self.assertEqual(seen, [xinject.UNICODE_KEYSYM | ord(c) for c in chars[:-1]] + [ord("ä")])

if __name__ == "__main__":
    unittest.main()
