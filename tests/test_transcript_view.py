import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

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
            self.assertEqual(output.getvalue(), 'old\nnew\n')
            for session in ('../pane', '.', '', 'missing'):
                with self.assertRaises(ValueError):
                    viewer.find_log(root, session)

    def test_snapshot_is_bounded_at_open_time_and_preserves_utf8(self):
        source = io.BytesIO('retained café\nnot in snapshot\n'.encode())
        output = io.StringIO()
        viewer.copy_text(source, output, len('retained café\n'.encode()))
        self.assertEqual(output.getvalue(), 'retained café\n')
