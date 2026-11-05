"""Exercise prompt recovery through an actual interactive Bash PTY."""
import os
from pathlib import Path
import pty
import select
import signal
import tempfile
import time
import unittest

RC = Path(__file__).resolve().parents[1] / 'config/kilix.bashrc'


class PromptRecovery(unittest.TestCase):
    def test_crashed_tui_preserves_status_and_prompt_hooks(self):
        for hook in ('PROMPT_COMMAND=\'printf "STATUS=%s\\n" "$?"\'',
                     'PROMPT_COMMAND=(\'printf "STATUS=%s\\n" "$?"\' \':\')'):
            with self.subTest(hook=hook), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                (home / '.bashrc').write_text("PS1='READY> '\n" + hook + '\n')
                pid, fd = pty.fork()
                if pid == 0:
                    os.environ.clear()
                    os.environ.update(HOME=directory, PATH='/usr/bin:/bin', TERM='xterm')
                    os.execv('/bin/bash', ['bash', '--noprofile', '--rcfile', str(RC), '-i'])
                def read_until(needle):
                    data = b''
                    deadline = time.monotonic() + 8
                    while needle not in data and time.monotonic() < deadline:
                        if select.select([fd], [], [], .1)[0]:
                            data += os.read(fd, 65536)
                    self.assertIn(needle, data)
                    return data
                try:
                    read_until(b'READY> ')
                    # A child exits without resetting its requested mouse mode.
                    os.write(fd, b"bash -c 'printf \"\\033[?1003h\"; kill -KILL $$'\n")
                    data = read_until(b'READY> ')
                    self.assertIn(b'STATUS=137', data)
                    self.assertGreater(data.rfind(b'\x1b[?1003l'), data.rfind(b'\x1b[?1003h'))
                    os.write(fd, b"printf 'COMMAND_OK\\n'\n")
                    self.assertIn(b'COMMAND_OK\r\n', read_until(b'READY> '))
                finally:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                    os.close(fd)


if __name__ == '__main__':
    unittest.main()
