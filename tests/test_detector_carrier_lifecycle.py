"""Owned fake helper lifecycle controls; these do not invoke Content or models."""
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CarrierLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="detector-lifecycle-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = self.root / "host"
        self.helper = self.host / "third_party/kilix-content/src/kilix_content/source_carrier.py"
        self.helper.parent.mkdir(parents=True)
        self.helper.write_text("""import os,pathlib,sys,time
action=sys.argv[1]
if os.environ.get('STOP_AT') == action:
    pathlib.Path(os.environ['PID_FILE']).write_text(str(os.getpid()))
    time.sleep(30)
if os.environ.get('FAIL_AT') == action: sys.exit(23)
if action == 'prepare-git':
    out=pathlib.Path(sys.argv[sys.argv.index('--destination')+1])
    out.mkdir(); (out/'gitconfig').write_text('')
    print(out/'gitconfig')
""")
        self.env = dict(os.environ, PID_FILE=str(self.root / "pid"))
        self.command = ["bash", "-c", 'set -e; . "$1"; detector_carrier_prepare "$2" '
                        'https://example.invalid/x.git 0000000000000000000000000000000000000000 '
                        '"$3" 0; printf "complete\\n"', "fixture",
                        str(ROOT / "scripts/detector-carrier.sh"), str(self.host), str(self.root)]

    def run_adapter(self, **extra):
        return subprocess.run(self.command, env=dict(self.env, **extra), capture_output=True,
                              text=True, timeout=10)

    def test_success_and_helper_failure(self):
        self.assertEqual(self.run_adapter().returncode, 0)
        result = self.run_adapter(FAIL_AT="prepare-git")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("offline Git pool was refused", result.stderr)
        self.assertEqual(list(self.root.glob(".detector-carrier.*")), [])

    def test_termination_cleans_owned_helper(self):
        for action, sig in (("materialize", signal.SIGTERM), ("prepare-git", signal.SIGINT)):
            with self.subTest(action=action, signal=sig):
                pidfile = self.root / "pid"
                pidfile.unlink(missing_ok=True)
                process = subprocess.Popen(self.command, env=dict(self.env, STOP_AT=action),
                                           start_new_session=True, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 5
                    while not pidfile.exists() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue(pidfile.exists(), "helper did not start")
                    helper_pid = int(pidfile.read_text())
                    os.kill(process.pid, sig)
                    process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 128 + sig)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(helper_pid, 0)
                    self.assertEqual(list(self.root.glob(".detector-carrier.*")), [])
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    if process.poll() is None:
                        process.communicate(timeout=5)

    def test_unreaped_descendant_preserves_pool(self):
        # Adopt the fixture's orphan so this test can reap it itself. A live
        # group member after the helper exits must keep the pool for review.
        libc = ctypes.CDLL(None, use_errno=True)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)  # PR_SET_CHILD_SUBREAPER
        self.helper.write_text("""import os,pathlib,subprocess,sys,time
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
pathlib.Path(os.environ['PID_FILE']).write_text(str(os.getpid())+' '+str(p.pid))
time.sleep(30)
""")
        process = subprocess.Popen(self.command, env=self.env, start_new_session=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        descendant = None
        helper = None
        try:
            pidfile = self.root / "pid"
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(pidfile.exists())
            helper, descendant = map(int, pidfile.read_text().split())
            os.kill(process.pid, signal.SIGTERM)
            _, error = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 143)
            self.assertIn("cleanup uncertain; preserving", error)
            self.assertEqual(len(list(self.root.glob(".detector-carrier.*"))), 1)
            waited, _ = os.waitpid(descendant, 0)
            self.assertEqual(waited, descendant)
            descendant = None
        finally:
            if helper is not None:
                try:
                    os.killpg(helper, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.communicate(timeout=5)
            if descendant is not None:
                try:
                    os.kill(descendant, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(descendant, 0)
                except ChildProcessError:
                    pass


if __name__ == "__main__":
    unittest.main()
