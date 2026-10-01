"""Exercise launcher worker ownership without starting an engine or broker."""
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'kilix').read_text()
HELPERS = SOURCE[SOURCE.index('_kilix_process_start_tick() {'):
                 SOURCE.index('\n_kilix_validate_private_storage_layout || exit 1')]
STUBS = '''
_kilix_read_transcript_config() { _KILIX_TRANSCRIPT_TOTAL=1; _KILIX_TRANSCRIPT_ARCHIVE=1; }
_kilix_transcript_reap() { echo pass >> "$TEST_PASSES"; }
'''


class TranscriptReaperLifecycleTests(unittest.TestCase):
    def test_frontend_exit_stops_worker_within_poll_interval(self):
        with tempfile.TemporaryDirectory() as temporary:
            passes = Path(temporary) / 'passes'
            owner = subprocess.Popen(['sleep', '60'])
            worker = subprocess.Popen(['bash', '-c', HELPERS + STUBS + '''
TEST_PASSES=$2
identity=$(_kilix_process_start_tick "$1") || exit 2
_kilix_transcript_reap_periodically "$1" "$identity" /unused
''', 'reaper-test', str(owner.pid), str(passes)])
            try:
                deadline = time.monotonic() + 2
                while not passes.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(passes.exists())
                owner.terminate()
                owner.wait(timeout=2)
                worker.wait(timeout=2)
                self.assertEqual(worker.returncode, 0)
                self.assertEqual(passes.read_text(), 'pass\n')
            finally:
                for child in (worker, owner):
                    if child.poll() is None:
                        child.kill()
                    child.wait()

    def test_live_reused_pid_with_changed_start_tick_does_not_reap(self):
        with tempfile.TemporaryDirectory() as temporary:
            passes = Path(temporary) / 'passes'
            result = subprocess.run(['bash', '-c', HELPERS + STUBS + '''
TEST_PASSES=$1
identity=$(_kilix_process_start_tick "$$") || exit 2
_kilix_transcript_reap_periodically "$$" "$((identity + 1))" /unused
''', 'reaper-test', str(passes)], timeout=2)
            self.assertEqual(result.returncode, 0)
            self.assertFalse(passes.exists())

    def test_proc_comm_with_spaces_and_parentheses_parses_start_tick(self):
        # Bash sees its child's kernel comm rather than splitting on the first ).
        program = '''import ctypes,time
ctypes.CDLL(None).prctl(15, b'odd ) name', 0, 0, 0)
time.sleep(60)
'''
        owner = subprocess.Popen(['python3', '-c', program])
        try:
            deadline = time.monotonic() + 2
            while Path(f'/proc/{owner.pid}/comm').read_text().strip() != 'odd ) name':
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            result = subprocess.run(['bash', '-c', HELPERS + '''
_kilix_process_start_tick "$1"
''', 'identity-test', str(owner.pid)], capture_output=True, text=True, timeout=2)
            record = Path(f'/proc/{owner.pid}/stat').read_text()
            expected = record.rsplit(') ', 1)[1].split()[19]
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), expected)
        finally:
            owner.terminate()
            owner.wait(timeout=2)


if __name__ == '__main__':
    unittest.main()
