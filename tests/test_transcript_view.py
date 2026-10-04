import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock

from test_transcript_index import run_launcher

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('transcript_view', ROOT / 'scripts/transcript-view.py')
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)


class TranscriptViewTests(unittest.TestCase):
    def test_strips_terminal_commands_across_every_chunk_boundary(self):
        raw = ('first\r\n\x1b[31mred\x1b[0m\n'
               '\x1b]52;c;clipboard\x07\x1bPremote-control\x1b\\'
               '\x1b_Gimage-payload\x1b\\last café\n')
        expected = 'first\nred\nlast café\n'
        for split in range(len(raw) + 1):
            clean = viewer.PlainText()
            self.assertEqual(clean.feed(raw[:split]) + clean.feed(raw[split:]), expected)

    def test_reads_entire_history_including_start_and_end_through_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / 'logs'
            logs.mkdir()
            text = 'first retained line\n' + 'middle output\n' * 12000 + 'last retained line\n'
            (logs / 'pane-123.log').write_text(text)
            result = run_launcher('transcript', 'view', 'pane-123', storage=root / 'storage', transcript_dir=logs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, text)

    @unittest.skipUnless(shutil.which('zstd'), 'zstd needed')
    def test_reads_archived_log_and_reports_missing_or_invalid_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'recent').mkdir()
            archive = root / 'recent/pane.log.zst'
            archive.write_bytes(subprocess.check_output(['zstd', '-cq'], input=b'old\r\n\x1b[2Jnew\n'))
            output = io.StringIO()
            viewer.snapshot(viewer.find_log(root, 'pane'), output)
            # The clear erased "old" from the screen, but not from the log;
            # "new" was then drawn on the second row, below a blank one.
            self.assertEqual(output.getvalue(), 'old\n\nnew\n')
            for session in ('../pane', '.', '', 'missing'):
                with self.assertRaises(ValueError):
                    viewer.find_log(root, session)

    def test_full_screen_program_reads_as_what_the_pane_showed(self):
        # Each frame repaints the same rows in place. Stripping escapes alone
        # repeats every partial frame; the replay shows each line once.
        frames = ''.join(
            '\x1b[?2026h' + ''.join(f'\x1b[{i};1Hreply line {i}\x1b[K' for i in range(1, n + 1))
            + '\x1b[?2026l' for n in range(1, 6))
        data = ('$ agent\r\n\x1b[?1049h' + frames + '\x1b[?1049l$ exit\r\n').encode()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'pane.log').write_bytes(data)
            output = io.StringIO()
            viewer.snapshot(viewer.find_log(Path(tmp), 'pane'), output)
            text = output.getvalue()
            for i in range(1, 6):
                self.assertEqual(text.count(f'reply line {i}'), 1)
            self.assertLess(text.index('$ agent'), text.index('reply line 1'))
            self.assertLess(text.index('reply line 5'), text.index('$ exit'))
            # Without the pinned replay the viewer still strips escapes.
            output = io.StringIO()
            with unittest.mock.patch.object(viewer, '_replayer', return_value=None):
                viewer.snapshot(viewer.find_log(Path(tmp), 'pane'), output)
            self.assertEqual(output.getvalue().count('reply line 1'), 5)

    def test_clean_prints_the_session_through_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / 'logs'
            logs.mkdir()
            session = '0123456789abcdef'
            (logs / f'{session}.log').write_bytes(
                b'\x1b_kilix-transcript;rows=5;cols=12\x1b\\$ echo hello world again\r\n'
                b'hello world again\r\n$ \r\n')
            (logs / f'{session}.meta').write_text('cwd=/home/pleb\ncmd=/bin/bash --posix\n')
            result = run_launcher('transcript', 'clean', session, '--no-header',
                                  storage=root / 'storage', transcript_dir=logs)
            self.assertEqual(result.returncode, 0, result.stderr)
            # Lines the 12-column pane wrapped read whole again.
            self.assertEqual(result.stdout, '$ echo hello world again\nhello world again\n$\n')
            result = run_launcher('transcript', 'clean', session, '--format', 'json',
                                  storage=root / 'storage', transcript_dir=logs)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"cwd": "/home/pleb"', result.stdout)
            result = run_launcher('transcript', 'clean', storage=root / 'storage', transcript_dir=logs)
            self.assertEqual(result.returncode, 2)

    def test_snapshot_is_bounded_at_open_time_and_preserves_utf8(self):
        source = io.BytesIO('retained café\nnot in snapshot\n'.encode())
        output = io.StringIO()
        viewer.copy_text(source, output, len('retained café\n'.encode()))
        self.assertEqual(output.getvalue(), 'retained café\n')
