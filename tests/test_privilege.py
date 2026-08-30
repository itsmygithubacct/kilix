import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "config"))

from kilix_sdk import privilege


class PrivilegedHelperTests(unittest.TestCase):
    helper = "/usr/bin/true"

    @staticmethod
    def process(returncode=0):
        process = mock.Mock(pid=12345)
        process.wait.return_value = returncode
        process.poll.return_value = returncode
        return process

    def test_helper_must_be_absolute_root_owned_and_not_a_link(self):
        with self.assertRaisesRegex(privilege.PrivilegedHelperError, "absolute"):
            privilege.validate_helper("true")
        self.assertEqual(privilege.validate_helper(self.helper), self.helper)
        with tempfile.TemporaryDirectory() as temporary:
            linked = Path(temporary) / "helper"
            linked.symlink_to(self.helper)
            with self.assertRaises(privilege.PrivilegedHelperError):
                privilege.validate_helper(str(linked))

    def test_terminal_launch_uses_one_fixed_argument_free_helper(self):
        seen = []
        process = self.process()
        with mock.patch.object(privilege.os, "geteuid", return_value=1000), \
             mock.patch.object(privilege, "_interactive_terminal", return_value=True), \
             mock.patch.object(privilege.shutil, "which", return_value="/usr/bin/sudo"):
            code = privilege.run_helper(
                self.helper,
                popen=lambda argv, **kwargs: seen.append((argv, kwargs)) or process,
            )
        self.assertEqual(code, 0)
        self.assertEqual(seen, [
            (["/usr/bin/sudo", "--", self.helper], {"process_group": 0})
        ])

    def test_gui_launch_uses_xterm_without_a_shell(self):
        seen = []

        def which(command):
            return {"sudo": "/usr/bin/sudo", "xterm": "/usr/bin/xterm"}.get(command)

        with mock.patch.object(privilege.os, "geteuid", return_value=1000), \
             mock.patch.object(privilege, "_interactive_terminal", return_value=False), \
             mock.patch.object(privilege.shutil, "which", side_effect=which), \
             mock.patch.dict(os.environ, {"DISPLAY": ":9"}):
            code = privilege.run_helper(
                self.helper,
                title="Android\nsetup",
                popen=lambda argv, **kwargs: seen.append((argv, kwargs)) or
                self.process(7),
            )
        self.assertEqual(code, 7)
        self.assertEqual(seen[0][0], [
            "/usr/bin/xterm", "-T", "Androidsetup", "-e",
            "/usr/bin/sudo", "--", self.helper,
        ])
        self.assertNotIn("sh", seen[0][0])

    def test_headless_noninteractive_launch_fails_before_running(self):
        popen = mock.Mock()
        with mock.patch.object(privilege.os, "geteuid", return_value=1000), \
             mock.patch.object(privilege, "_interactive_terminal", return_value=False), \
             mock.patch.object(privilege.shutil, "which", side_effect=lambda c: "/usr/bin/sudo" if c == "sudo" else None), \
             mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(privilege.PrivilegedHelperError, "terminal or an X display"):
                privilege.run_helper(self.helper, popen=popen)
        popen.assert_not_called()

    def test_interruption_stops_the_complete_installer_process_group(self):
        process = self.process()
        process.wait.side_effect = privilege.PrivilegedHelperError("interrupted")
        process.poll.return_value = None
        with mock.patch.object(privilege.os, "geteuid", return_value=0), \
             mock.patch.object(privilege, "_stop_process") as stop:
            with self.assertRaisesRegex(privilege.PrivilegedHelperError, "interrupted"):
                privilege.run_helper(self.helper, popen=lambda *_a, **_k: process)
        stop.assert_called_once_with(process, process_group=True)


if __name__ == "__main__":
    unittest.main()
