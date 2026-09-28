"""Routing and frame ownership must not produce a nested terminal desktop."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import apprun


class WindowPolicy(unittest.TestCase):
    def test_nested_host_requires_explicit_desktop_session(self):
        for command in ('kilix', '/usr/bin/kitty', 'openbox', 'pleb-session'):
            with self.subTest(command=command), patch.object(
                    sys, 'argv', ['apprun.py', command]), patch.object(apprun, 'AppPane') as pane:
                with self.assertRaisesRegex(SystemExit, 'cannot be embedded'):
                    apprun.main()
                pane.assert_not_called()
        with patch.object(sys, 'argv', ['apprun.py', '--desktop-session', 'openbox']), \
                patch.object(apprun, 'AppPane') as pane:
            apprun.main()
            self.assertFalse(pane.call_args.kwargs['manage_windows'])

    def test_managed_frame_is_never_resized_or_refocused_by_runner(self):
        pane = object.__new__(apprun.AppPane)
        pane._pane_wm = True
        frame = Mock()
        pane._visible_app_windows = Mock(return_value=[(frame, Mock())])
        pane.xd = Mock()
        self.assertTrue(pane.fit_app_window(force=True))
        frame.configure.assert_not_called()
        pane.xd.set_input_focus.assert_not_called()
        pane.xapp = Mock()
        pane.xapp.window_manager.poll.return_value = None
        pane.clamp_app_windows = Mock()
        pane.maintain_app_window(1)
        pane.clamp_app_windows.assert_not_called()
        pane.xapp.window_manager.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'Openbox exited'):
            pane.maintain_app_window(2)


if __name__ == '__main__':
    unittest.main()
