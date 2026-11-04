"""Add an owned QMP monitor to a graphical QEMU pane."""
import base64
import os
import sys
import tempfile


class QemuPane:
    def __init__(self, command):
        self.directory = tempfile.TemporaryDirectory(prefix='kilix-qmp-')
        self.path = os.path.join(self.directory.name, 'monitor')
        self.command = [*command, '-qmp', f'unix:{self.path},server=on,wait=off']

    def publish(self, value):
        encoded = base64.b64encode(value.encode()).decode()
        try:
            sys.stdout.write(f'\x1b]1337;SetUserVar=kilix_qemu_monitor={encoded}\x07')
            sys.stdout.flush()
        except OSError:
            pass

    def close(self):
        self.publish('')
        self.directory.cleanup()
