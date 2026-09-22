"""Modified mouse gestures reach an isolated X server and leave no held keys."""
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import unittest

from Xlib import X, display

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import xinject  # noqa: E402


@unittest.skipUnless(shutil.which("Xvfb"), "needs Xvfb")
class MouseModifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        read_fd, write_fd = os.pipe()
        try:
            server = subprocess.Popen(
                [shutil.which("Xvfb"), "-displayfd", str(write_fd),
                 "-screen", "0", "640x480x24", "-nolisten", "tcp", "-noreset"],
                pass_fds=(write_fd,), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)

        def stop_server():
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

        cls.addClassCleanup(stop_server)
        try:
            if not select.select([read_fd], [], [], 10)[0]:
                raise RuntimeError("private Xvfb did not become ready")
            number = int(os.read(read_fd, 32).strip())
        finally:
            os.close(read_fd)
        cls.xd = display.Display(f":{number}")
        cls.addClassCleanup(cls.xd.close)
        screen = cls.xd.screen()
        cls.window = screen.root.create_window(
            0, 0, 640, 480, 0, screen.root_depth, X.InputOutput, X.CopyFromParent,
            event_mask=X.KeyPressMask | X.KeyReleaseMask | X.ButtonPressMask
            | X.ButtonReleaseMask | X.PointerMotionMask)
        cls.window.map()
        cls.window.set_input_focus(X.RevertToParent, X.CurrentTime)
        cls.xd.sync()

    def setUp(self):
        self.inj = xinject.Injector(self.xd, 640, 480)
        self.addCleanup(self.inj.release_all)
        self.events()

    def events(self):
        self.xd.sync()
        events = []
        while self.xd.pending_events():
            events.append(self.xd.next_event())
        return events

    def mouse(self, bits, press=True, x=40):
        self.inj.mouse({"b": bits, "press": press, "x": x, "y": 40},
                       (0, 0, 640, 480))

    def state(self):
        self.xd.sync()
        return self.xd.screen().root.query_pointer().mask

    def test_control_shift_and_alt_click_drag_release(self):
        for bits, expected in ((16, X.ControlMask), (4, X.ShiftMask), (8, X.Mod1Mask)):
            with self.subTest(bits=bits):
                self.inj.release_all()
                self.events()
                self.mouse(bits)
                pressed = [e for e in self.events() if e.type == X.ButtonPress]
                self.assertEqual([e.state for e in pressed], [expected])
                self.assertEqual(self.state(), expected | X.Button1Mask)
                self.mouse(bits | 32, x=60)
                moved = [e for e in self.events() if e.type == X.MotionNotify]
                self.assertEqual([e.state for e in moved], [expected | X.Button1Mask])
                self.mouse(bits, press=False, x=60)
                released = [e for e in self.events() if e.type == X.ButtonRelease]
                self.assertEqual([e.state for e in released], [expected | X.Button1Mask])
                self.assertEqual(self.state(), 0)

    def test_modifier_can_be_released_during_a_drag(self):
        self.mouse(16)
        self.assertEqual(self.state(), X.ControlMask | X.Button1Mask)
        self.inj.chord(chr(57442), 0, 3)
        self.assertEqual(self.state(), X.Button1Mask)
        self.mouse(32, x=70)
        self.mouse(0, press=False, x=70)
        self.assertEqual(self.state(), 0)

    def test_modifier_can_be_pressed_during_a_drag_and_leave_is_not_a_click(self):
        self.mouse(0)
        self.inj.chord(chr(57443), xinject.MOD_ALT, 1)
        self.assertEqual(self.state(), X.Mod1Mask | X.Button1Mask)
        self.events()
        self.mouse(256)
        self.assertEqual(self.events(), [])
        self.assertEqual(self.state(), X.Mod1Mask | X.Button1Mask)
        self.mouse(8, press=False)
        self.assertEqual(self.state(), 0)

    def test_modifiers_stay_owned_until_the_last_mouse_button_is_released(self):
        self.mouse(16)
        self.mouse(16 | 2)
        self.mouse(16, press=False)
        self.assertEqual(self.state(), X.ControlMask | X.Button3Mask)
        self.mouse(16 | 2, press=False)
        self.assertEqual(self.state(), 0)

    def test_mouse_release_with_no_modifier_bits_does_not_leave_a_modifier(self):
        self.mouse(8)
        self.assertEqual(self.state(), X.Mod1Mask | X.Button1Mask)
        self.mouse(0, press=False)
        self.assertEqual(self.state(), 0)
        self.events()
        self.inj.chord("a", 0, 1)
        self.assertEqual([e.state for e in self.events() if e.type == X.KeyPress], [0])

    def test_mouse_and_keyboard_share_a_held_modifier(self):
        self.inj.chord("a", xinject.MOD_CTRL, 1)
        self.mouse(16)
        self.inj.chord("a", 0, 3)
        self.assertEqual(self.state(), X.ControlMask | X.Button1Mask)
        self.mouse(16, press=False)
        self.assertEqual(self.state(), 0)

    def test_modified_wheel_and_hover_are_scoped_to_their_events(self):
        self.mouse(64 | 16, x=80)
        wheel = [e for e in self.events() if e.type in (X.ButtonPress, X.ButtonRelease)]
        self.assertEqual([(e.detail, e.state) for e in wheel],
                         [(4, X.ControlMask), (4, X.ControlMask | X.Button4Mask)])
        self.assertEqual(self.state(), 0)
        self.mouse(32 | 4, x=90)
        moved = [e for e in self.events() if e.type == X.MotionNotify]
        self.assertEqual([e.state for e in moved], [X.ShiftMask])
        self.assertEqual(self.state(), 0)

    def test_focus_cleanup_releases_mouse_and_keyboard_ownership(self):
        self.inj.chord("a", xinject.MOD_SHIFT, 1)
        self.mouse(16 | 4)
        self.inj.release_all()
        self.assertEqual(self.state(), 0)
        self.assertFalse(any(self.xd.query_keymap()))
        self.assertEqual(self.inj._mod_holds, {})
        self.assertEqual(self.inj._btns_down, set())

    # ---- right and middle buttons -----------------------------------------
    # Everything above asserts Button1Mask. The owner's report was about
    # modified input inside a `kilix run` app pane, so the buttons that pane
    # can also receive get the same treatment: the X button number in the
    # event, its mask in the press/motion/release state, and nothing held
    # afterwards. SGR's low two bits are the button index (kitty's
    # encode_button in src/kitty/mouse.c): 0 left, 1 middle, 2 right.

    def test_modified_right_and_middle_click_drag_release(self):
        for bits, button, bmask in ((2, 3, X.Button3Mask), (1, 2, X.Button2Mask)):
            for mod, expected in ((16, X.ControlMask), (4, X.ShiftMask),
                                  (8, X.Mod1Mask)):
                with self.subTest(button=button, mod=mod):
                    self.inj.release_all()
                    self.events()
                    self.mouse(mod | bits)
                    pressed = [e for e in self.events() if e.type == X.ButtonPress]
                    self.assertEqual([(e.detail, e.state) for e in pressed],
                                     [(button, expected)])
                    self.assertEqual(self.state(), expected | bmask)
                    self.mouse(mod | bits | 32, x=60)
                    moved = [e for e in self.events() if e.type == X.MotionNotify]
                    self.assertEqual([e.state for e in moved], [expected | bmask])
                    self.mouse(mod | bits, press=False, x=60)
                    released = [e for e in self.events()
                                if e.type == X.ButtonRelease]
                    self.assertEqual([(e.detail, e.state) for e in released],
                                     [(button, expected | bmask)])
                    self.assertEqual(self.state(), 0)
                    self.assertEqual(self.inj._btns_down, set())

    def test_modifier_pressed_and_released_during_a_right_drag(self):
        self.mouse(2)
        self.assertEqual(self.state(), X.Button3Mask)
        self.inj.chord(chr(57443), xinject.MOD_ALT, 1)
        self.assertEqual(self.state(), X.Mod1Mask | X.Button3Mask)
        self.events()
        self.mouse(8 | 2 | 32, x=70)     # the drag report now carries Alt
        moved = [e for e in self.events() if e.type == X.MotionNotify]
        self.assertEqual([e.state for e in moved], [X.Mod1Mask | X.Button3Mask])
        self.inj.chord(chr(57443), 0, 3)
        self.assertEqual(self.state(), X.Button3Mask)
        self.mouse(2, press=False, x=70)
        self.assertEqual(self.state(), 0)
        self.assertFalse(any(self.xd.query_keymap()))

    def test_modifiers_stay_owned_until_the_last_of_two_buttons_releases(self):
        # The left-button case above releases the FIRST button first. Release
        # the second one first instead, which is where a per-button bug hides.
        self.mouse(16)                   # ctrl + left press
        self.mouse(16 | 2)               # ctrl + right press, left still down
        self.assertEqual(self.state(),
                         X.ControlMask | X.Button1Mask | X.Button3Mask)
        self.mouse(16 | 2, press=False)
        self.assertEqual(self.state(), X.ControlMask | X.Button1Mask)
        self.assertEqual(self.inj._btns_down, {1})
        self.mouse(16, press=False)
        self.assertEqual(self.state(), 0)
        self.assertEqual(self.inj._btns_down, set())
        self.mouse(16 | 2)               # and the middle button in that role
        self.mouse(16 | 1)
        self.mouse(16 | 2, press=False)
        self.assertEqual(self.state(), X.ControlMask | X.Button2Mask)
        self.mouse(16 | 1, press=False)
        self.assertEqual(self.state(), 0)

    def test_right_release_with_no_modifier_bits_does_not_leave_a_modifier(self):
        self.mouse(8 | 2)
        self.assertEqual(self.state(), X.Mod1Mask | X.Button3Mask)
        self.mouse(2, press=False)
        self.assertEqual(self.state(), 0)
        self.events()
        self.inj.chord("a", 0, 1)
        self.assertEqual([e.state for e in self.events()
                          if e.type == X.KeyPress], [0])

    def test_focus_cleanup_releases_right_and_middle_button_ownership(self):
        self.inj.chord("a", xinject.MOD_SHIFT, 1)
        self.mouse(4 | 2)
        self.mouse(4 | 1)
        self.assertEqual(self.inj._btns_down, {2, 3})
        self.assertEqual(self.state(),
                         X.ShiftMask | X.Button2Mask | X.Button3Mask)
        self.inj.release_all()
        self.assertEqual(self.state(), 0)
        self.assertFalse(any(self.xd.query_keymap()))
        self.assertEqual(self.inj._mod_holds, {})
        self.assertEqual(self.inj._btns_down, set())

    def test_wheel_notches_are_paired_and_leave_a_held_drag_alone(self):
        self.mouse(64 | 1 | 16, x=80)    # wheel down + ctrl
        wheel = [e for e in self.events()
                 if e.type in (X.ButtonPress, X.ButtonRelease)]
        self.assertEqual([(e.detail, e.state) for e in wheel],
                         [(5, X.ControlMask),
                          (5, X.ControlMask | X.Button5Mask)])
        self.assertEqual(self.state(), 0)
        self.assertEqual(self.inj._btns_down, set())
        self.mouse(2)                    # a wheel notch mid right-drag
        self.events()
        self.mouse(64, x=85)
        self.assertEqual(self.inj._btns_down, {3})
        self.assertEqual(self.state(), X.Button3Mask)
        self.mouse(2, press=False, x=85)
        self.assertEqual(self.state(), 0)

    def test_horizontal_wheel_does_not_scroll_vertically(self):
        # kitty sends horizontal scroll as SGR 66/67 -- encode_button's
        # (button - 4) | SCROLL_BUTTON_INDICATOR for X buttons 6 and 7
        # (src/kitty/mouse.c, encode_mouse_scroll(w, s > 0 ? 6 : 7, mods)).
        # X has no mask bit above Button5Mask, so the button number is the
        # whole assertion.
        for bits, button in ((66, 6), (67, 7)):
            with self.subTest(bits=bits):
                self.inj.release_all()
                self.events()
                self.mouse(bits | 16, x=80)
                wheel = [e for e in self.events()
                         if e.type in (X.ButtonPress, X.ButtonRelease)]
                self.assertEqual([e.detail for e in wheel], [button, button])
                self.assertEqual([e.state for e in wheel][0], X.ControlMask)
                self.assertEqual(self.state(), 0)
                self.assertEqual(self.inj._btns_down, set())

    def test_extra_mouse_buttons_are_not_aliased_onto_the_first_four(self):
        # kitty encodes physical buttons 4-7 as SGR 128-131 --
        # (button - 8) | EXTRA_BUTTON_INDICATOR over button_map's `button + 5`
        # (src/kitty/mouse.c) -- and glfw's X11 backend derives those from
        # X buttons 8-11 with `event->xbutton.button - Button1 - 4`
        # (src/glfw/x11_window.c). The round trip is exact, so 128 is X 8.
        # SGR 131 is left out: X 11 is past Xvfb's ten-button pointer, and the
        # server's error reply for it reaches this suite as X protocol noise.
        for bits, button in ((128, 8), (129, 9), (130, 10)):
            with self.subTest(bits=bits):
                self.inj.release_all()
                self.events()
                self.mouse(bits)
                pressed = [e for e in self.events() if e.type == X.ButtonPress]
                self.assertEqual([e.detail for e in pressed], [button])
                self.assertEqual(self.inj._btns_down, {button})
                self.mouse(bits, press=False)
                released = [e for e in self.events()
                            if e.type == X.ButtonRelease]
                self.assertEqual([e.detail for e in released], [button])
                self.assertEqual(self.inj._btns_down, set())

    def test_an_extra_button_click_does_not_end_a_drag_it_aliases_onto(self):
        # A side button clicked mid-drag must not be decoded as the button
        # that is already held: its release would end the real drag and drop
        # the modifier the gesture still owns.
        for drag, button, bmask, extra in ((2, 3, X.Button3Mask, 130),
                                           (1, 2, X.Button2Mask, 129),
                                           (0, 1, X.Button1Mask, 128)):
            with self.subTest(button=button, extra=extra):
                self.inj.release_all()
                self.events()
                self.mouse(16 | drag)
                self.assertEqual(self.state(), X.ControlMask | bmask)
                self.mouse(16 | extra)
                self.mouse(16 | extra, press=False)
                self.assertEqual(self.inj._btns_down, {button})
                self.assertEqual(self.state(), X.ControlMask | bmask)
                self.mouse(16 | drag, press=False)
                self.assertEqual(self.state(), 0)
                self.assertEqual(self.inj._btns_down, set())

    def test_move_click_tracks_every_real_button_and_pairs_only_wheel_notches(self):
        # move_click is the `kilix share` path: a viewer's press/release
        # carries a press flag, a wheel notch does not. A flagged button is a
        # held button whatever its number, or release_all cannot free it.
        for button, bmask in ((1, X.Button1Mask), (2, X.Button2Mask),
                              (3, X.Button3Mask)):
            with self.subTest(button=button):
                self.inj.move_click(100, 100, button=button, press=True)
                self.assertEqual(self.state(), bmask)
                self.assertEqual(self.inj._btns_down, {button})
                self.inj.move_click(100, 100, button=button, press=False)
                self.assertEqual(self.state(), 0)
                self.assertEqual(self.inj._btns_down, set())
        for button in (4, 5):
            with self.subTest(wheel=button):
                self.events()
                self.inj.move_click(100, 100, button=button)
                notch = [e for e in self.events()
                         if e.type in (X.ButtonPress, X.ButtonRelease)]
                self.assertEqual([e.detail for e in notch], [button, button])
                self.assertEqual(self.inj._btns_down, set())
        for button in (4, 5, 8):
            with self.subTest(held=button):
                self.events()
                self.inj.move_click(100, 100, button=button, press=True)
                pressed = [e for e in self.events() if e.type == X.ButtonPress]
                self.assertEqual([e.detail for e in pressed], [button])
                self.assertEqual(self.inj._btns_down, {button})
                self.inj.move_click(100, 100, button=button, press=False)
                self.assertEqual(self.inj._btns_down, set())


if __name__ == "__main__":
    unittest.main()
