"""Terminal-only routing, literal media arguments, and help without an engine."""
import contextlib
import importlib.util
import io
import os
import pty
import termios
import select
import time
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
             patch.object(video.subprocess, 'run') as probe, \
             patch.object(video, 'run_player', return_value=0) as execute:
            probe.return_value.stdout = 'mpv v0.41.0'

            video.main(args)
        return execute.call_args.args[0]

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
        with patch.object(video.os, 'access', return_value=False), \
             patch.object(video.shutil, 'which', return_value=None), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            video.main(['clip.mp4'])
        self.assertEqual(caught.exception.code, 127)

    def test_rejects_known_crashing_system_renderer(self):
        with patch.object(video.os, 'access', return_value=False), \
             patch.object(video.shutil, 'which', return_value='/usr/bin/mpv'), \
             patch.object(video.sys.stdin, 'isatty', return_value=True), \
             patch.object(video.sys.stdout, 'isatty', return_value=True), \
             patch.object(video.subprocess, 'run') as probe, \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            probe.return_value.stdout = 'mpv v0.40.0 Copyright'
            video.main(['clip.mp4'])
        self.assertEqual(caught.exception.code, 2)

    def test_requires_terminal(self):
        with patch.object(video.shutil, 'which', return_value='/usr/bin/mpv'), \
             patch.object(video.sys.stdin, 'isatty', return_value=False), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            video.main(['clip.mp4'])
        self.assertEqual(caught.exception.code, 2)

    def test_native_abort_restores_terminal_and_mouse_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'mpv'
            fake.write_text("#!/usr/bin/python3\nimport os,tty,resource,signal\n"
                            "import sys\nif '--version' in sys.argv: print('mpv test'); sys.exit(0)\n"
                            "resource.setrlimit(resource.RLIMIT_CORE,(0,0))\n"
                            "tty.setraw(0)\nos.write(1,b'\\x1b[?1003h')\n"
                            "os.kill(os.getpid(),signal.SIGABRT)\n")
            fake.chmod(0o700)
            master, slave = pty.openpty()
            original = termios.tcgetattr(slave)
            try:
                proc = subprocess.Popen(
                    ['/usr/bin/python3', '-B', str(ROOT / 'config/terminal_video.py'), 'clip.mp4'],
                    stdin=slave, stdout=slave, stderr=slave,
                    env={'PATH': tmp + ':/usr/bin:/bin', 'HOME': tmp})
                self.assertEqual(proc.wait(timeout=5), 134)
                self.assertEqual(termios.tcgetattr(slave), original)
                output = b''
                while select.select([master], [], [], 0.1)[0]:
                    output += os.read(master, 65536)
                self.assertIn(video.TERMINAL_RESET, output)
            finally:
                os.close(master)
                os.close(slave)

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
