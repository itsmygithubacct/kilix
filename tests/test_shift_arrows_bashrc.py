"""Shift+arrow keys in a Kilix shell (kilix.bashrc section 8), through a real PTY.

Kilix sends Shift+arrows as ESC [ 1 ; 2 A-D. Without a binding, readline beeps
and inserts the final letter, so Shift+Left typed "D" into the command line.
"""
import os
from pathlib import Path
import pty
import re
import select
import signal
import subprocess
import tempfile
import time
import unittest

RC = Path(__file__).resolve().parents[1] / 'config/kilix.bashrc'
SHIFT = {'up': b'\x1b[1;2A', 'down': b'\x1b[1;2B', 'right': b'\x1b[1;2C', 'left': b'\x1b[1;2D'}
PRINT = b"printf '[%s]\\n' "


class ShiftArrows(unittest.TestCase):
    def shell(self, bashrc='', inputrc=None, lang=None):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        Path(home.name, '.bashrc').write_text("cd \"$HOME\"; PS1='READY> '\n" + bashrc)
        if inputrc is not None:
            Path(home.name, '.inputrc').write_text(inputrc)
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.clear()
            os.environ.update(HOME=home.name, PATH='/usr/bin:/bin', TERM='xterm-kitty')
            if lang:
                os.environ['LANG'] = lang
            os.execv('/bin/bash', ['bash', '--noprofile', '--rcfile', str(RC), '-i'])

        def stop():
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            os.close(fd)
        self.addCleanup(stop)
        self.fd = fd
        self.read_until(b'READY> ')

    def read_until(self, needle):
        data = b''
        deadline = time.monotonic() + 8
        while needle not in data and time.monotonic() < deadline:
            if select.select([self.fd], [], [], .1)[0]:
                data += os.read(self.fd, 65536)
        self.assertIn(needle, data)
        return data

    def run_line(self, *keys):
        for key in keys:
            os.write(self.fd, key)
            time.sleep(.05)
        os.write(self.fd, b'\r')
        return re.findall(rb'\r\[([^\]\r\n]*)\]\r\n', self.read_until(b'READY> '))

    def test_shift_arrows_move_instead_of_typing_letters(self):
        for lang in (None, 'C.UTF-8'):
            with self.subTest(lang=lang):
                self.shell(lang=lang)
                self.assertEqual(self.run_line(PRINT + b'ab', SHIFT['left'], b'Z'), [b'aZb'])
                self.assertEqual(self.run_line(PRINT + b'ab', SHIFT['left'], SHIFT['left'],
                                               SHIFT['right'], b'Z'), [b'aZb'])
                self.assertEqual(self.run_line(PRINT + b'one'), [b'one'])
                self.assertEqual(self.run_line(PRINT + b'two'), [b'two'])
                # Shift+Up walks back through history, Shift+Down comes forward again.
                self.assertEqual(self.run_line(SHIFT['up'], SHIFT['up'], SHIFT['down']), [b'two'])

    def test_a_binding_the_user_already_made_wins(self):
        # A function from ~/.bashrc, one from ~/.inputrc, a macro and a shell
        # command; the C locale lists ESC as \M- and C.UTF-8 as \e.
        for lang in (None, 'C.UTF-8'):
            with self.subTest(lang=lang):
                self.shell(lang=lang,
                           bashrc="bind '\"\\e[1;2D\": backward-delete-char'\n"
                                  "bind '\"\\e[1;2A\": \"MACRO\"'\n"
                                  "bind -x '\"\\e[1;2B\": READLINE_LINE=\"echo [SHELLCMD]\"'\n",
                           inputrc='$include /etc/inputrc\n"\\e[1;2C": backward-delete-char\n')
                self.assertEqual(self.run_line(PRINT + b'ab', SHIFT['left'], b'Z'), [b'aZ'])
                self.assertEqual(self.run_line(PRINT + b'ab', SHIFT['right'], b'Z'), [b'aZ'])
                self.assertEqual(self.run_line(PRINT, SHIFT['up']), [b'MACRO'])
                # A shell-command binding redraws the prompt with its new line.
                os.write(self.fd, SHIFT['down'])
                self.read_until(b'READY> echo [SHELLCMD]')
                self.assertEqual(self.run_line(), [b'SHELLCMD'])
                self.assertEqual(self.run_line(PRINT + b'ab', b'\x1b[D', b'Z'), [b'aZb'])

    def test_no_terminal_means_no_bind_warnings(self):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                ['bash', '--noprofile', '--rcfile', str(RC), '-i', '-c', 'echo ok'],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
                env={'HOME': home, 'PATH': '/usr/bin:/bin', 'TERM': 'dumb'})
        self.assertEqual(result.stdout, 'ok\n')
        self.assertNotIn('bind', result.stderr)
        self.assertNotIn('line editing', result.stderr)


if __name__ == '__main__':
    unittest.main()
