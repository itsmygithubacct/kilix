"""Terminal-only routing, literal media arguments, and help without an engine."""
import contextlib
import fcntl
import importlib.util
import io
import os
import pty
import struct
import termios
import threading
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
measure_cell_size = video.cell_size


class TerminalMediaTests(unittest.TestCase):
    def setUp(self):
        # main() asks the controlling terminal for its cell size; keep tests off it.
        patcher = patch.object(video, 'cell_size', return_value=None)
        self.cell_size = patcher.start()
        self.addCleanup(patcher.stop)

    def command(self, args):
        with patch.object(video.shutil, 'which', side_effect=lambda name: '/usr/bin/mpv' if name == 'mpv' else None), \
             patch.object(video.sys.stdin, 'isatty', return_value=True), \
             patch.object(video.sys.stdout, 'isatty', return_value=True), \
             patch.object(video.subprocess, 'run') as probe, \
             patch.object(video, 'run_player', return_value=0) as execute:
            probe.return_value.stdout = 'mpv v0.41.0'

            video.main(args)
        return execute.call_args.args[0]

    def test_named_fork_precedes_private_and_system_players(self):
        with patch.object(video.shutil, 'which', side_effect=lambda name: '/usr/bin/kilix-mpv' if name == 'kilix-mpv' else '/usr/bin/mpv'), \
             patch.object(video.os, 'access', return_value=True), \
             patch.object(video.sys.stdin, 'isatty', return_value=True), \
             patch.object(video.sys.stdout, 'isatty', return_value=True), \
             patch.object(video, 'supports_terminal_fullscreen', return_value=False), \
             patch.object(video.subprocess, 'run') as probe, \
             patch.object(video, 'run_player', return_value=0) as run:
            self.assertEqual(video.main(['clip.mp4']), 0)
            self.assertEqual(run.call_args.args[0][0], '/usr/bin/kilix-mpv')
            probe.assert_not_called()

    def test_configured_fork_build_is_the_named_player(self):
        with tempfile.TemporaryDirectory() as directory:
            player = Path(directory) / 'mpv'
            player.write_text('#!/bin/sh\n')
            for configured, mode, expected in ((str(player), 0o755, str(player)),
                                               (str(player), 0o644, '/usr/bin/kilix-mpv'),
                                               ('mpv', 0o755, '/usr/bin/kilix-mpv')):
                player.chmod(mode)
                with self.subTest(configured=configured, mode=oct(mode)), \
                     patch.dict(video.os.environ, {'KILIX_VIDEO_PLAYER': configured}, clear=True), \
                     patch.object(video.shutil, 'which', return_value='/usr/bin/kilix-mpv'), \
                     patch.object(video.sys.stdin, 'isatty', return_value=True), \
                     patch.object(video.sys.stdout, 'isatty', return_value=True), \
                     patch.object(video.subprocess, 'run') as probe, \
                     patch.object(video, 'run_player', return_value=0) as run:
                    self.assertEqual(video.main(['clip.mp4']), 0)
                    self.assertEqual(run.call_args.args[0][0], expected)
                    # A named fork build skips the system mpv 0.40 resize check.
                    probe.assert_not_called()

    def test_literal_source_and_terminal_output(self):
        source = '-strange $(touch forbidden); clip.mp4'
        command = self.command(['--', source])
        self.assertEqual(command[-2:], ['--', source])
        for option in ['--no-config', '--load-scripts=no', '--vo=kitty', '--force-window=no',
                       '--term-osd=no', '--term-status-msg=']:
            self.assertIn(option, command)

    def test_local_shared_memory_requires_named_player_and_direct_terminal(self):
        with patch.dict(video.os.environ, {}, clear=True), \
             patch.object(video.shutil, 'which', return_value='/usr/bin/kilix-mpv'), \
             patch.object(video.sys.stdin, 'isatty', return_value=True), \
             patch.object(video.sys.stdout, 'isatty', return_value=True), \
             patch.object(video, 'run_player', return_value=0) as run:
            self.assertEqual(video.main(['--transport', 'shm', 'clip.mp4']), 0)
            self.assertIn('--vo-kitty-use-shm=yes', run.call_args.args[0])
            for key in ('SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY', 'TMUX'):
                with patch.dict(video.os.environ, {key: 'test'}), \
                     contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    video.main(['--transport', 'shm', 'clip.mp4'])
                self.assertEqual(caught.exception.code, 2)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                video.main(['--vo', 'tct', '--transport', 'shm', 'clip.mp4'])
            self.assertEqual(caught.exception.code, 2)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            self.command(['--transport', 'shm', 'clip.mp4'])
        self.assertEqual(caught.exception.code, 2)

    def test_auto_transport_proves_locality_and_keeps_explicit_override(self):
        with patch.dict(video.os.environ, {}, clear=True), \
             patch.object(video.shutil, 'which', return_value='/usr/bin/kilix-mpv'), \
             patch.object(video.sys.stdin, 'isatty', return_value=True), \
             patch.object(video.sys.stdout, 'isatty', return_value=True), \
             patch.object(video, 'local_shared_memory_terminal', return_value=True) as local, \
             patch.object(video, 'run_player', return_value=0) as run:
            video.main(['clip.mp4'])
            self.assertIn('--vo-kitty-use-shm=yes', run.call_args.args[0])
            local.return_value = False
            video.main(['clip.mp4'])
            self.assertNotIn('--vo-kitty-use-shm=yes', run.call_args.args[0])
            local.return_value = True
            video.main(['--transport', 'inline', 'clip.mp4'])
            self.assertNotIn('--vo-kitty-use-shm=yes', run.call_args.args[0])
            video.main(['--vo', 'tct', 'clip.mp4'])
            self.assertNotIn('--vo-kitty-use-shm=yes', run.call_args.args[0])

    def test_local_terminal_proof_fails_closed(self):
        with patch.dict(video.os.environ, {'KITTY_PID': str(os.getpid())}, clear=True):
            self.assertFalse(video.local_shared_memory_terminal())  # this process is Python
            with patch.object(Path, 'read_text', return_value='kitty\n'), \
                 patch.object(video.os.path, 'samefile', return_value=True):
                self.assertTrue(video.local_shared_memory_terminal())
                for key in ('SSH_CONNECTION', 'SSH_CLIENT', 'SSH_TTY', 'TMUX'):
                    with patch.dict(video.os.environ, {key: 'set'}):
                        self.assertFalse(video.local_shared_memory_terminal())
                with patch.object(video.os.path, 'samefile', return_value=False):
                    self.assertFalse(video.local_shared_memory_terminal())
                with patch.object(video.os.path, 'samefile', side_effect=PermissionError):
                    self.assertFalse(video.local_shared_memory_terminal())
            for pid in ('', '0', '1', '../self', '999999999999', '１２'):
                with patch.dict(video.os.environ, {'KITTY_PID': pid}):
                    self.assertFalse(video.local_shared_memory_terminal())

    def test_fullscreen_requires_support_and_kilix_context(self):
        with patch.dict(video.os.environ, {'KILIX_STORAGE_HOME': '/private/test'}, clear=True), \
             patch.object(video.shutil, 'which', return_value='/usr/bin/kilix-mpv'), \
             patch.object(video.sys.stdin, 'isatty', return_value=True), \
             patch.object(video.sys.stdout, 'isatty', return_value=True), \
             patch.object(video.subprocess, 'run') as probe, \
             patch.object(video, 'run_player', return_value=0) as run:
            probe.return_value.returncode = 0
            probe.return_value.stdout = ' --vo-kitty-terminal-fullscreen Flag\n'
            video.main(['clip.mp4'])
            self.assertIn('--vo-kitty-terminal-fullscreen=yes', run.call_args.args[0])
            probe.return_value.stdout = ' --vo-kitty-use-shm Flag\n'
            video.main(['clip.mp4'])
            self.assertNotIn('--vo-kitty-terminal-fullscreen=yes', run.call_args.args[0])
            probe.side_effect = subprocess.TimeoutExpired('probe', 5)
            video.main(['clip.mp4'])
            self.assertNotIn('--vo-kitty-terminal-fullscreen=yes', run.call_args.args[0])

    def test_stream(self):
        source = 'https://example.org/live.m3u8?a=1&b=2'
        self.assertEqual(self.command([source])[-1], source)

    def test_explicit_options(self):
        command = self.command(['--vo', 'tct', '--mute', '--loop', 'clip.mp4'])
        for option in ['--vo=tct', '--mute=yes', '--loop-file=inf']:
            self.assertIn(option, command)

    def test_wsl_streams_silence_while_paused_muted_or_not(self):
        for wsl in (False, True):
            for args in (['--mute', 'clip.mp4'], ['clip.mp4']):
                with self.subTest(wsl=wsl, args=args), patch.object(video, 'wsl_kernel', return_value=wsl):
                    command = self.command(args)
                    self.assertEqual('--audio-stream-silence=yes' in command, wsl)
                    self.assertEqual('--mute=yes' in command, '--mute' in args)
                    self.assertFalse([o for o in command if o.startswith('--ao')])

    def test_wsl_kernel_reads_the_release(self):
        for release, expected in (('5.15.167.4-microsoft-standard-WSL2\n', True),
                                  ('4.4.0-19041-Microsoft\n', True),
                                  ('6.12.0-amd64\n', False)):
            with self.subTest(release=release), \
                 patch.object(video.Path, 'read_text', autospec=True, return_value=release) as read:
                self.assertEqual(video.wsl_kernel(), expected)
                self.assertEqual(read.call_args.args[0], Path('/proc/sys/kernel/osrelease'))
        with patch.object(video.Path, 'read_text', side_effect=FileNotFoundError):
            self.assertFalse(video.wsl_kernel())

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
             patch.object(video.shutil, 'which', side_effect=lambda name: '/usr/bin/mpv' if name == 'mpv' else None), \
             patch.object(video.sys.stdin, 'isatty', return_value=True), \
             patch.object(video.sys.stdout, 'isatty', return_value=True), \
             patch.object(video.subprocess, 'run') as probe, \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            probe.return_value.stdout = 'mpv v0.40.0 Copyright'
            video.main(['clip.mp4'])
        self.assertEqual(caught.exception.code, 2)

    def test_requires_terminal(self):
        with patch.object(video.shutil, 'which', side_effect=lambda name: '/usr/bin/mpv' if name == 'mpv' else None), \
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
                            "tty.setraw(0)\nos.write(1,b'\\x1b[?1003h\\x1b[?2026h')\n"
                            "os.write(1,b'\\x1b_Ga=T,f=24,s=2,v=2,m=1;AAAA')\n"
                            "os.write(2,b'decoder failed\\n')\n"
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
                self.assertIn(b'\x1b[?2026l', output)
                # The image command the player left open is terminated before the reset.
                opened = output.index(b'\x1b_Ga=T')
                self.assertEqual(output.index(b'\x1b\\', opened), output.index(video.TERMINAL_RESET))
                restored = output.index(video.TERMINAL_RESET) + len(video.TERMINAL_RESET)
                self.assertIn(b'decoder failed', output[restored:])
                self.assertIn(b'player terminated by signal 6', output[restored:])
            finally:
                os.close(master)
                os.close(slave)

    def test_stream_silence_notice_is_not_replayed(self):
        notice = b'[ao/pulse] The --audio-stream-silence option is set. This will break certain player behavior.'
        for extra in (b'[vd] decoder warning', b''):
            payload = notice + b'\n' + extra
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                fake = Path(tmp) / 'mpv'
                fake.write_text("#!/usr/bin/python3\nimport os,sys\n"
                                "if '--version' in sys.argv: print('mpv test'); sys.exit(0)\n"
                                f"os.write(2,{payload!r})\n")
                fake.chmod(0o700)
                master, slave = pty.openpty()
                try:
                    proc = subprocess.Popen(
                        ['/usr/bin/python3', '-B', str(ROOT / 'config/terminal_video.py'), 'clip.mp4'],
                        stdin=slave, stdout=slave, stderr=slave,
                        env={'PATH': tmp + ':/usr/bin:/bin', 'HOME': tmp})
                    self.assertEqual(proc.wait(timeout=5), 0)
                    output = b''
                    while select.select([master], [], [], 0.1)[0]:
                        output += os.read(master, 65536)
                    self.assertNotIn(b'audio-stream-silence', output)
                    after = output[output.index(video.TERMINAL_RESET) + len(video.TERMINAL_RESET):]
                    # Nothing left to report prints nothing, not a blank line.
                    self.assertEqual(after, extra + b'\r\n' if extra else b'')
                finally:
                    os.close(master)
                    os.close(slave)

    def test_cell_size_asks_the_terminal_only_without_pixels(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        name = os.ttyname(slave)
        written = []
        def answer(reply):
            data = b''
            while not data.endswith(b'[16t'):
                data += os.read(master, 64)
            written.append(data)
            typed, escape, rest = reply.partition(b'\x1b')
            if typed:  # typed-ahead text arrives in its own read before the reply
                os.write(master, typed)
                time.sleep(0.05)
            os.write(master, escape + rest)
        original = termios.tcgetattr(slave)
        # Typed-ahead text is skipped; zero, oversized or incomplete replies are not sizes.
        for reply, expected in ((b'\x1b[6;26;12t', '12x26'), (b'exit\x1b[6;26;12t', '12x26'),
                                (b'\x1b[6;0;12t', None), (b'\x1b[6;26;1025t', None),
                                (b'\x1b[6;26;12', None)):
            with self.subTest(reply=reply):
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 34, 158, 0, 0))
                worker = threading.Thread(target=answer, args=(reply,))
                worker.start()
                self.assertEqual(measure_cell_size(name, timeout=0.5), expected)
                worker.join(5)
                self.assertEqual(termios.tcgetattr(slave), original)
        self.assertEqual(written, [b'\x1b[16t'] * 5)
        # No cell grid, a background job (SIGTTOU would stop it), or a pty with pixels:
        # never queried.
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 0, 0, 0, 0))
        self.assertIsNone(measure_cell_size(name, timeout=0.5))
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 34, 158, 0, 0))
        with patch.object(video.os, 'tcgetpgrp', return_value=os.getpgrp() + 1):
            self.assertIsNone(measure_cell_size(name, timeout=0.5))
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 34, 158, 1896, 884))
        self.assertIsNone(measure_cell_size(name, timeout=0.5))
        self.assertEqual(select.select([master], [], [], 0.2)[0], [])
        self.assertIsNone(measure_cell_size(str(Path(name).parent / 'missing-tty')))

    def test_only_kitty_output_gets_the_measured_cell_size(self):
        self.cell_size.return_value = '12x26'
        with patch.dict(video.os.environ, {'KEEP': 'yes'}, clear=True), \
             patch.object(video, 'wsl_kernel', return_value=True), \
             patch.object(video.shutil, 'which', return_value='/usr/bin/kilix-mpv'), \
             patch.object(video.sys.stdin, 'isatty', return_value=True), \
             patch.object(video.sys.stdout, 'isatty', return_value=True), \
             patch.object(video, 'run_player', return_value=0) as run:
            video.main(['clip.mp4'])
            self.assertEqual(run.call_args.args[1], {'KEEP': 'yes', 'KILIX_MPV_CELL_SIZE': '12x26'})
            video.main(['--vo', 'tct', 'clip.mp4'])
            self.assertIsNone(run.call_args.args[1])
            self.assertEqual(self.cell_size.call_count, 1)
            self.cell_size.return_value = None
            video.main(['clip.mp4'])
            self.assertIsNone(run.call_args.args[1])

    def test_measured_cell_size_reaches_the_player(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'kilix-mpv'
            fake.write_text("#!/usr/bin/python3\nimport os\n"
                            "os.write(2,b'cell='+os.environ.get('KILIX_MPV_CELL_SIZE','none').encode()+b'\\n')\n")
            fake.chmod(0o700)
            master, slave = pty.openpty()
            proc = None
            try:
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 34, 158, 0, 0))
                proc = subprocess.Popen(
                    ['/usr/bin/python3', '-B', str(ROOT / 'config/terminal_video.py'), 'clip.mp4'],
                    stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                    preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
                    env={'PATH': tmp + ':/usr/bin:/bin', 'HOME': tmp})
                output = b''
                while b'\x1b[16t' not in output:
                    self.assertTrue(select.select([master], [], [], 5)[0], output)
                    output += os.read(master, 65536)
                os.write(master, b'\x1b[6;26;12t')
                self.assertEqual(proc.wait(timeout=5), 0)
                while select.select([master], [], [], 0.1)[0]:
                    output += os.read(master, 65536)
                self.assertIn(b'cell=12x26', output)
            finally:
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    proc.wait()
                os.close(master)
                os.close(slave)

    def test_wsl_refuses_shared_memory_transport(self):
        with patch.object(video, 'wsl_kernel', return_value=True), \
             patch.object(video.shutil, 'which', return_value='/usr/bin/kilix-mpv'), \
             patch.object(video.sys.stdin, 'isatty', return_value=True), \
             patch.object(video.sys.stdout, 'isatty', return_value=True), \
             patch.object(video, 'run_player', return_value=0) as run, \
             contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as caught:
            video.main(['--transport', 'shm', 'clip.mp4'])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn('inline transport in WSL', error.getvalue())
        run.assert_not_called()

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
