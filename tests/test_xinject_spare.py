"""Scratch candidates come from actual owned-X keymap responses."""
import os
import subprocess
from unittest.mock import patch

import private_input_fixture as fixture
import xinject
from Xlib import XK


class ScratchKeymap(fixture.NativeInputFixture):
    def test_private_x_refuses_a_child_without_its_cookie(self):
        env = dict(os.environ,XAUTHORITY=str(self.root/'absent-auth'))
        r = subprocess.run(['/usr/bin/python3','-I','-c',
                            'from Xlib import display;display.Display()'],env=env,
                           capture_output=True,text=True,timeout=3)
        self.assertNotEqual(r.returncode,0)
        self.assertIn('Authorization required',r.stderr)


    def test_reusable_actual_keymap_excludes_media_function_and_control(self):
        first = self.xd.display.info.min_keycode
        # Populate real server responses, including repeated and mixed rows.
        rows = [(0,0)]*40
        protected = [0x1008ff12,0x1008ff13,XK.XK_F1,XK.XK_Control_L,
                     0x01110000,0x0100d800,0x01000001]
        rows[32:32+len(protected)] = [(sym,sym) for sym in protected]
        rows[39] = (0x01004e00,XK.XK_F2)
        rows[31] = (0x01004e00,0x01004e00)
        rows[30] = (0xe4,0xe4)
        self.xd.change_keyboard_mapping(first,rows)
        self.xd.sync()
        # Limit only the observed range; rows still come from the real server.
        original_info = self.xd.display.info
        with patch.object(original_info,'max_keycode',first+39):
            spare = self.inj._find_spare()
        for offset in range(32,40):
            self.assertNotIn(first+offset,spare,hex(rows[offset][0]))
        self.assertIn(first+31,spare) # actual Unicode, recycled from an earlier owner
        self.assertIn(first+30,spare) # Latin-1 recycled slot
        self.assertIn(first+29,spare) # empty slot
        self.assertLessEqual(len(spare),xinject.SCRATCH_KEYCODES)


def load_tests(loader, tests, pattern):
    return fixture.x11_sandbox.sandbox_load_tests(loader,tests,__name__)
