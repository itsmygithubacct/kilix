"""Protocol edges retain key identity; real XTest effects stay private."""
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
import browse
import xinject
from Xlib import X, XK, display


class ProtocolKeys(unittest.TestCase):
    def setUp(self):
        self.term = object.__new__(apprun.RunTerm)

    def test_shifted_glyph_is_text_and_base_key_is_stable_on_both_edges(self):
        for base, shifted in (("9", "("), ("0", ")"), ("1", "!"), ("a", "A")):
            press = self.term._parse_csi(f"{ord(base)}:{ord(shifted)};2:1", "u")
            release = self.term._parse_csi(f"{ord(base)};1:3", "u")
            self.assertEqual((press["key"], release["key"]), (base, base))
            self.assertEqual(press["text"], shifted)
            self.assertEqual((press["event"], release["event"]), (1, 3))
        browser = object.__new__(browse.Term)
        self.assertEqual(browser._parse_csi("57:40;2", "u")["key"], "(")

    def test_lock_bits_do_not_change_identity_or_create_a_synthetic_modifier(self):
        for modifiers, expected in ((65, "A"), (66, "a"), (129, "a")):
            press = self.term._parse_csi(f"97:65;{modifiers}:1", "u")
            release = self.term._parse_csi(f"97;{modifiers}:3", "u")
            self.assertEqual((press["key"], release["key"]), ("a", "a"))
            self.assertEqual(press["text"], expected)
        for code, name in ((13, "Enter"), (9, "Tab"), (27, "Escape"), (127, "Backspace")):
            self.assertEqual(self.term._parse_csi(f"{code};1:3", "u")["key"], name)
        self.assertEqual(self.term._parse_csi("1;1:3", "B")["key"], "ArrowDown")


@unittest.skipUnless(shutil.which("Xvfb"), "private Xvfb unavailable")
class PrivateKeyboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="kilix-keyboard-private-")
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
        # Explicit owned display, never DISPLAY or the live guest/host server.
        cls.display_name = ":" + number
        cls.xd = display.Display(cls.display_name)
        cls.addClassCleanup(cls.xd.close)

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
        self.window = self.xd.screen().root.create_window(
            0, 0, 320, 200, 0, self.xd.screen().root_depth,
            event_mask=X.KeyPressMask | X.KeyReleaseMask)
        self.addCleanup(self.window.destroy)
        self.window.map()
        self.window.set_input_focus(X.RevertToParent, X.CurrentTime)
        self.xd.sync()
        while self.xd.pending_events():
            self.xd.next_event()

    def inject(self, params):
        ev = self.term._parse_csi(params, "u")
        self.inj.chord(ev["key"], ev["mods"] - 1, ev["event"])
        self.xd.sync()

    def held(self):
        bitmap = bytes(self.xd.query_keymap())
        return [i for i in range(256) if bitmap[i // 8] & (1 << (i % 8))]

    def test_parenthesis_releases_after_shift_and_next_letter_is_lowercase(self):
        base_code = self.xd.keysym_to_keycode(ord("9"))
        self.inject("57:40;2:1")
        self.assertIn(base_code, self.held())
        self.inject("57441;1:3")  # release physical Shift before the digit
        self.inject("57;1:3")
        self.assertEqual(self.held(), [])
        self.assertEqual(self.xd.screen().root.query_pointer().mask & X.ShiftMask, 0)
        self.inject("97;1:1")
        self.inject("97;1:3")
        events = []
        while self.xd.pending_events():
            ev = self.xd.next_event()
            if ev.type in (X.KeyPress, X.KeyRelease):
                events.append(ev)
        presses = [ev for ev in events if ev.type == X.KeyPress]
        digit = next(ev for ev in presses if ev.detail == base_code)
        self.assertEqual(self.xd.keycode_to_keysym(digit.detail, 1), ord("("))
        letter = next(ev for ev in presses if ev.detail == self.xd.keysym_to_keycode(ord("a")))
        self.assertEqual(letter.state & (X.ShiftMask | X.LockMask), 0)
        self.assertEqual(self.xd.keycode_to_keysym(letter.detail, 0), ord("a"))
        self.assertEqual(self.held(), [])

    def test_duplicate_press_and_focus_loss_release_all_actual_keys(self):
        self.inject("97:65;2:1")
        self.inject("97;1:1")
        self.inject("97;1:3")
        self.assertEqual(self.held(), [])
        self.inject("57:40;2:1")
        pane = object.__new__(apprun.AppPane)
        pane.inj = self.inj
        pane.on_focus({"kind": "focus", "in": False})
        self.xd.sync()
        self.assertEqual(self.held(), [])
        self.assertEqual(self.inj._mod_holds, {})


if __name__ == "__main__":
    unittest.main()
