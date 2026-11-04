import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'config'))
from qemu_pane import QemuPane

class QemuPaneTests(unittest.TestCase):
    def test_monitor_keeps_existing_qemu_arguments_and_is_private(self):
        command = ['/usr/bin/qemu-system-x86_64', '-name', 'guest with spaces',
                   '-qmp', 'unix:/existing/monitor,server=on,wait=off']
        pane = QemuPane(command)
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            try:
                self.assertEqual(pane.command[:-2], command)
                self.assertEqual(pane.command[-2], '-qmp')
                self.assertEqual(os.stat(pane.directory.name).st_mode & 0o777, 0o700)
                pane.publish(pane.path)
                self.assertIn('SetUserVar=kilix_qemu_monitor=', output.getvalue())
            finally:
                pane.close()
            self.assertTrue(output.getvalue().endswith('SetUserVar=kilix_qemu_monitor=\x07'))
        self.assertFalse(os.path.exists(pane.directory.name))

    def test_disconnected_terminal_still_removes_monitor_directory(self):
        pane = QemuPane(['qemu-system-x86_64'])
        with patch('sys.stdout.write', side_effect=OSError('terminal gone')):
            pane.close()
        self.assertFalse(os.path.exists(pane.directory.name))
