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

    def test_qemu_detach_refuses_before_profile_or_process_creation(self):
        # QEMU accepts both spellings; neither may escape pane ownership.
        for flag in ('-daemonize', '--daemonize'):
            with patch.object(sys, 'argv', ['apprun.py', '/usr/bin/qemu-system-x86_64',
                                           flag]), \
                    patch.object(apprun, 'prepare_app_command') as profile, \
                    patch.object(apprun, 'AppPane') as pane:
                with self.assertRaisesRegex(SystemExit, 'cannot be owned by a tab'):
                    apprun.main()
                profile.assert_not_called()
                pane.assert_not_called()

    def test_missing_terminal_cleans_temporary_browser_profile(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'scratch-profile'
            profile.mkdir()
            with patch.object(sys, 'argv', ['apprun.py', 'google-chrome']), \
                    patch.object(apprun, 'prepare_app_command',
                                 return_value=(['google-chrome'], str(profile))), \
                    patch.object(apprun, 'RunTerm', side_effect=RuntimeError('no terminal')):
                with self.assertRaisesRegex(RuntimeError, 'no terminal'):
                    apprun.main()
            self.assertFalse(profile.exists())

    def test_foreground_qemu_reaches_pane_without_changing_arguments(self):
        command = ['qemu-system-x86_64', '-name', 'guest with spaces',
                   '-nodefaults', '-display', 'gtk', '-m', '128']
        with patch.object(sys, 'argv', ['apprun.py', *command]), \
                patch.object(apprun, 'AppPane') as pane:
            apprun.main()
            self.assertEqual(pane.call_args.args[0], command)
            pane.return_value.run.assert_called_once_with()

    def test_rendered_content_receipt_is_private_once_only_and_never_overwrites(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'tab.ready'
            pane = object.__new__(apprun.AppPane)
            with patch.dict(os.environ, {'KILIX_RUN_READY_FILE': str(receipt)}):
                self.assertTrue(pane._mark_content_ready(apprun.CONTENT_READY_CHANGED))
                self.assertEqual(receipt.read_bytes(), b'content-ready\n')
                self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
                self.assertFalse(pane._mark_content_ready(apprun.CONTENT_READY_CHANGED))
                other = object.__new__(apprun.AppPane)
                with self.assertRaises(FileExistsError):
                    other._mark_content_ready(apprun.CONTENT_READY_CHANGED)
                receipt.unlink()
                target = Path(directory) / 'keep'
                target.write_text('unchanged')
                receipt.symlink_to(target)
                with self.assertRaises(FileExistsError):
                    other._mark_content_ready(apprun.CONTENT_READY_CHANGED)
                self.assertEqual(target.read_text(), 'unchanged')

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
