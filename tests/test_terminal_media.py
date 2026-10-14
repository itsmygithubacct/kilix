"""Terminal-only routing, literal media arguments, and help without an engine."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('terminal_video', ROOT / 'config/terminal_video.py')
video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video)


class TerminalMediaTests(unittest.TestCase):
    def command(self, args):
        with patch.object(video.shutil, 'which', return_value='/usr/bin/mpv'), \
             patch.object(video.sys.stdin, 'isatty', return_value=True), \
             patch.object(video.sys.stdout, 'isatty', return_value=True), \
             patch.object(video.os, 'execv') as execute:
            video.main(args)
        return execute.call_args.args[1]

    def test_literal_source_and_terminal_output(self):
        source = '-strange $(touch forbidden); clip.mp4'
        command = self.command(['--', source])
        self.assertEqual(command[-2:], ['--', source])
        for option in ['--no-config', '--load-scripts=no', '--vo=kitty', '--force-window=no']:
            self.assertIn(option, command)

    def test_stream(self):
        source = 'https://example.org/live.m3u8?a=1&b=2'
        self.assertEqual(self.command([source])[-1], source)

    def test_explicit_options(self):
        command = self.command(['--vo', 'tct', '--mute', '--loop', 'clip.mp4'])
        for option in ['--vo=tct', '--mute=yes', '--loop-file=inf']:
            self.assertIn(option, command)

    def test_no_gui_renderer(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            video.main(['--vo', 'gpu', 'clip.mp4'])
        self.assertEqual(caught.exception.code, 2)

    def test_missing_player(self):
        with patch.object(video.shutil, 'which', return_value=None), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            video.main(['clip.mp4'])
        self.assertEqual(caught.exception.code, 127)

    def test_requires_terminal(self):
        with patch.object(video.shutil, 'which', return_value='/usr/bin/mpv'), \
             patch.object(video.sys.stdin, 'isatty', return_value=False), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            video.main(['clip.mp4'])
        self.assertEqual(caught.exception.code, 2)

    def test_wrapper_help_needs_no_engine_or_storage(self):
        with tempfile.TemporaryDirectory() as home:
            env = {'HOME': home, 'PATH': '/usr/bin:/bin'}
            for verb in ['image', 'video']:
                result = subprocess.run([str(ROOT / 'kilix'), verb, '--help'],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('kilix ' + verb, result.stdout)
            self.assertEqual(list(Path(home).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
