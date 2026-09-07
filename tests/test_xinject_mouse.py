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


if __name__ == "__main__":
    unittest.main()
