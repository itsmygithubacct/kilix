import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'config' if (ROOT / 'config').is_dir() else ROOT))
from kilix_tmux.__main__ import main


class LiteralInput(unittest.TestCase):
    def test_utf8_file_and_stdin_preserve_every_character(self):
        text = ' café\t\'"`$HOME $(never_run) ; '
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'payload'
            path.write_bytes(text.encode())
            for source in (str(path), '-'):
                with mock.patch('sys.stdin', io.StringIO(text)), \
                     mock.patch('kilix_tmux.__main__.dispatch', return_value={
                         'ok': True, 'data': {'operation': 'send', 'dry_run': False}}) as dispatch, \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(['--socket', '/tmp/s', 'send', '%0', '--text-file', source]), 0)
                self.assertEqual(dispatch.call_args.args[0]['text'], text)
                self.assertNotIn('text_file', dispatch.call_args.args[0])

    def test_invalid_file_input_never_contacts_tmux(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'payload'
            with mock.patch('kilix_tmux.control.subprocess.run') as run, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                for data in (b'', b'a\n', b'a\r', b'a\0', b'\xff', b'x'*65537):
                    path.write_bytes(data)
                    self.assertEqual(main(['--socket', '/tmp/s', 'send', '%0', '--text-file', str(path)]), 2)
                self.assertEqual(main(['--socket', '/tmp/s', 'send', '%0', 'x', '--text-file', str(path)]), 2)
                self.assertEqual(main(['--socket', '/tmp/s', 'send', '%0']), 2)
                self.assertEqual(main(['--socket', '/tmp/s', 'send', '%0', '--text-file', str(path/'absent')]), 2)
                run.assert_not_called()
