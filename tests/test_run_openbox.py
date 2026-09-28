"""Private-X11 acceptance: Openbox fills the app, preserves dialogs and focus.

Uses the same namespace and owned-Xvfb fixture as the resize acceptance test.
No connection to the operator's desktop is allowed.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_run_resize as resize
import x11_sandbox


def load_tests(loader, tests, pattern):
    return x11_sandbox.sandbox_load_tests(loader, tests, __name__)


@unittest.skipUnless(shutil.which('openbox'), 'needs Openbox')
class RunOpenboxE2E(resize.RunResizeE2E):
    # Exercise resize only with the manager below, not the inherited WM-less test.
    test_resize_choreography = None

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.xd.sync()
            if predicate():
                return
            time.sleep(0.02)
        self.fail('private Openbox did not reach expected state')

    def test_app_dialog_focus_and_resize(self):
        from Xlib import X, Xatom
        import apprun
        from kilix_sdk.xapp import XAppSession

        self.assertTrue(apprun.randr_prepare(self.xd))
        self.assertTrue(apprun.randr_set_screen_size(self.xd, 800, 600))
        mode = apprun.randr_set_monitor_mode(self.xd, 800, 600)
        processes = []

        class Supervisor:
            # The fixture's private server intentionally has no cookie.
            xauth = '/nonexistent-kilix-test-authority'

            def spawn(self, name, argv, **kwargs):
                proc = subprocess.Popen(argv, **kwargs)
                processes.append(proc)
                return proc

        def cleanup():
            for proc in processes:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        self.addCleanup(cleanup)
        session = XAppSession('acceptance', 800, 600, supervisor=Supervisor())
        session.display, session.xd = self.disp, self.xd
        from unittest.mock import patch
        with patch.dict(os.environ, {'KILIX_RUN_WM': 'openbox'}):
            self.assertTrue(session.start_window_manager())
        screen = self.xd.screen()
        root = screen.root
        main = root.create_window(10, 10, 240, 160, 0, screen.root_depth,
                                  X.InputOutput, X.CopyFromParent)
        self.addCleanup(main.destroy)
        main.set_wm_class('kilix-test', 'KilixTest')
        main.map()
        self.wait_for(lambda: (main.get_geometry().width, main.get_geometry().height) == (800, 600))
        dialog = root.create_window(20, 20, 140, 90, 0, screen.root_depth,
                                    X.InputOutput, X.CopyFromParent)
        self.addCleanup(dialog.destroy)
        dialog.set_wm_transient_for(main)
        dialog.change_property(self.xd.intern_atom('_NET_WM_WINDOW_TYPE'), Xatom.ATOM,
                               32, [self.xd.intern_atom('_NET_WM_WINDOW_TYPE_DIALOG')])
        dialog.map()
        self.wait_for(lambda: getattr(self.xd.get_input_focus().focus, 'id', None) == dialog.id)
        self.assertEqual((dialog.get_geometry().width, dialog.get_geometry().height), (140, 90))
        dialog.unmap()
        self.wait_for(lambda: getattr(self.xd.get_input_focus().focus, 'id', None) == main.id)
        self.assertTrue(apprun.randr_prepare(self.xd))
        self.assertTrue(apprun.randr_set_screen_size(self.xd, 1000, 700))
        apprun.randr_set_monitor_mode(self.xd, 1000, 700, old_mode=mode)
        self.wait_for(lambda: (main.get_geometry().width, main.get_geometry().height) == (1000, 700))


if __name__ == '__main__':
    unittest.main()
