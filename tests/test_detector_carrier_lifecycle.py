"""Owned fake helper lifecycle controls; these do not invoke Content or models."""
import ctypes
import os
from pathlib import Path
import shutil
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
        (self.host / "scripts").mkdir(parents=True)
        shutil.copyfile(ROOT / "scripts/detector-carrier-supervisor.py",
                        self.host / "scripts/detector-carrier-supervisor.py")
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

    def test_stubborn_descendant_is_reaped(self):
        libc = ctypes.CDLL(None, use_errno=True)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)  # PR_SET_CHILD_SUBREAPER
        self.helper.write_text("""import os,pathlib,subprocess,sys,time
code='import signal,time,pathlib,sys;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path(sys.argv[1]).write_text("ready");time.sleep(30)'
ready=pathlib.Path(os.environ['PID_FILE']+'.ready')
p=subprocess.Popen([sys.executable,'-c',code,str(ready)])
while not ready.exists():time.sleep(.01)
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
            _, error = process.communicate(timeout=12)
            self.assertEqual(process.returncode, 143)
            self.assertNotIn("cleanup uncertain", error)
            self.assertEqual(list(self.root.glob(".detector-carrier.*")), [])
            with self.assertRaises(ProcessLookupError):
                os.kill(descendant, 0)
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

    def test_escaped_descendant_refuses_and_preserves_pool(self):
        libc = ctypes.CDLL(None, use_errno=True)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        self.helper.write_text("""import os,pathlib,subprocess,sys,time
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
pathlib.Path(os.environ['PID_FILE']).write_text(str(os.getpid())+' '+str(p.pid))
time.sleep(30)
""")
        # Escaped descendants can inherit stderr; use a file rather than
        # waiting for pipe EOF from an intentionally live fixture process.
        logpath = self.root / "stderr"
        descendant = helper = None
        with logpath.open("w+") as log:
            process = subprocess.Popen(self.command, env=self.env, start_new_session=True,
                                       stdout=subprocess.DEVNULL, stderr=log, text=True)
            try:
                pidfile = self.root / "pid"
                deadline = time.monotonic() + 5
                while not pidfile.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(pidfile.exists())
                helper, descendant = map(int, pidfile.read_text().split())
                os.kill(process.pid, signal.SIGTERM)
                process.wait(timeout=12)
                log.flush()
                self.assertEqual(process.returncode, 143)
                self.assertIn("cleanup uncertain; preserving", logpath.read_text())
                pools = list(self.root.glob(".detector-carrier.*"))
                self.assertEqual(len(pools), 1)
                self.assertTrue((pools[0] / ".cleanup-uncertain").is_file())
                os.kill(descendant, 0)  # Test owns and cleans this escaped fixture.
            finally:
                if descendant is not None:
                    try:
                        os.kill(descendant, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        os.waitpid(descendant, 0)
                    except ChildProcessError:
                        pass
                if helper is not None:
                    try:
                        os.killpg(helper, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

    def test_helper_exit_drains_same_group_descendant(self):
        # Worker 45's two counterexamples: materialize exits nonzero, or exits
        # zero and the subsequent prepare-git fails. A stubborn same-group
        # child must be stopped and reaped before scratch is removed.
        libc = ctypes.CDLL(None, use_errno=True)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        for exit_code in (1, 0):
            with self.subTest(materialize_exit=exit_code):
                self.helper.write_text("""import os,pathlib,signal,subprocess,sys,time
if sys.argv[1] == 'materialize':
    ready=pathlib.Path(os.environ['PID_FILE']+'.ready')
    code='import pathlib,signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path(sys.argv[1]).write_text("ready");time.sleep(30)'
    child=subprocess.Popen([sys.executable,'-c',code,str(ready)])
    while not ready.exists(): time.sleep(.01)
    pathlib.Path(os.environ['PID_FILE']).write_text(str(child.pid))
    sys.exit(int(os.environ['MATERIALIZE_EXIT']))
pathlib.Path(os.environ['PID_FILE']+'.prepare').write_text('called')
sys.exit(1)
""")
                pidfile = self.root / "pid"
                pidfile.unlink(missing_ok=True)
                (self.root / "pid.ready").unlink(missing_ok=True)
                (self.root / "pid.prepare").unlink(missing_ok=True)
                logpath = self.root / "normal-exit-log"
                with logpath.open("w+") as log:
                    process = subprocess.Popen(self.command, env=dict(self.env, MATERIALIZE_EXIT=str(exit_code)),
                                               start_new_session=True, stdout=subprocess.DEVNULL, stderr=log)
                    descendant = None
                    try:
                        deadline = time.monotonic() + 5
                        while not pidfile.exists() and time.monotonic() < deadline:
                            time.sleep(.02)
                        self.assertTrue(pidfile.exists())
                        descendant = int(pidfile.read_text())
                        process.wait(timeout=12)
                        log.flush()
                        self.assertEqual(process.returncode, 1)
                        self.assertEqual((self.root / "pid.prepare").exists(), exit_code == 0)
                        with self.assertRaises(ProcessLookupError):
                            os.kill(descendant, 0)
                        descendant = None
                        self.assertEqual(list(self.root.glob(".detector-carrier.*")), [])
                        self.assertNotIn("cleanup uncertain", logpath.read_text())
                    finally:
                        if descendant is not None:
                            try:
                                os.kill(descendant, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            try:
                                os.waitpid(descendant, 0)
                            except ChildProcessError:
                                pass
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)

    def test_helper_exit_with_escaped_descendant_retains_scratch(self):
        libc = ctypes.CDLL(None, use_errno=True)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        for exit_code in (1, 0):
            with self.subTest(materialize_exit=exit_code):
                self.helper.write_text("""import os,pathlib,subprocess,sys,time
if sys.argv[1] == 'materialize':
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
    pathlib.Path(os.environ['PID_FILE']).write_text(str(child.pid))
    sys.exit(int(os.environ['MATERIALIZE_EXIT']))
pathlib.Path(os.environ['PID_FILE']+'.prepare').write_text('called')
sys.exit(1)
""")
                pidfile = self.root / "pid"
                pidfile.unlink(missing_ok=True)
                (self.root / "pid.prepare").unlink(missing_ok=True)
                logpath = self.root / "escaped-exit-log"
                with logpath.open("w+") as log:
                    process = subprocess.Popen(self.command, env=dict(self.env, MATERIALIZE_EXIT=str(exit_code)),
                                               start_new_session=True, stdout=subprocess.DEVNULL, stderr=log)
                    descendant = None
                    try:
                        deadline = time.monotonic() + 5
                        while not pidfile.exists() and time.monotonic() < deadline:
                            time.sleep(.02)
                        self.assertTrue(pidfile.exists())
                        descendant = int(pidfile.read_text())
                        process.wait(timeout=12)
                        log.flush()
                        self.assertEqual(process.returncode, 1)
                        self.assertFalse((self.root / "pid.prepare").exists())
                        self.assertIn("cleanup uncertain; preserving", logpath.read_text())
                        pools = list(self.root.glob(".detector-carrier.*"))
                        self.assertEqual(len(pools), 1)
                        self.assertTrue((pools[0] / ".cleanup-uncertain").is_file())
                        os.kill(descendant, 0)
                    finally:
                        if descendant is not None:
                            try:
                                os.kill(descendant, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            try:
                                os.waitpid(descendant, 0)
                            except ChildProcessError:
                                pass
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)
                        # Remove only this test's retained pool after reaping
                        # its own escaped child, so the next subtest is clean.
                        for pool in self.root.glob(".detector-carrier.*"):
                            shutil.rmtree(pool)

    def test_prepare_git_exit_drains_same_group_descendant(self):
        # Exercise both a complete successful preparation and a failing
        # prepare-git helper when the final helper itself leaves a child.
        libc = ctypes.CDLL(None, use_errno=True)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        for exit_code in (0, 1):
            with self.subTest(prepare_exit=exit_code):
                self.helper.write_text("""import os,pathlib,signal,subprocess,sys,time
if sys.argv[1] == 'materialize': sys.exit(0)
ready=pathlib.Path(os.environ['PID_FILE']+'.ready')
code='import pathlib,signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path(sys.argv[1]).write_text("ready");time.sleep(30)'
child=subprocess.Popen([sys.executable,'-c',code,str(ready)])
while not ready.exists(): time.sleep(.01)
pathlib.Path(os.environ['PID_FILE']).write_text(str(child.pid))
if int(os.environ['PREPARE_EXIT']) == 0:
    dest=pathlib.Path(sys.argv[sys.argv.index('--destination')+1]);dest.mkdir()
    (dest/'gitconfig').write_text('');print(dest/'gitconfig')
sys.exit(int(os.environ['PREPARE_EXIT']))
""")
                pidfile = self.root / "pid"
                pidfile.unlink(missing_ok=True)
                (self.root / "pid.ready").unlink(missing_ok=True)
                logpath = self.root / "prepare-exit-log"
                with logpath.open("w+") as log:
                    process = subprocess.Popen(self.command, env=dict(self.env, PREPARE_EXIT=str(exit_code)),
                                               start_new_session=True, stdout=subprocess.DEVNULL, stderr=log)
                    descendant = None
                    try:
                        deadline = time.monotonic() + 5
                        while not pidfile.exists() and time.monotonic() < deadline:
                            time.sleep(.02)
                        self.assertTrue(pidfile.exists())
                        descendant = int(pidfile.read_text())
                        process.wait(timeout=12)
                        log.flush()
                        self.assertEqual(process.returncode, exit_code)
                        with self.assertRaises(ProcessLookupError):
                            os.kill(descendant, 0)
                        descendant = None
                        self.assertEqual(list(self.root.glob(".detector-carrier.*")), [])
                        self.assertNotIn("cleanup uncertain", logpath.read_text())
                    finally:
                        if descendant is not None:
                            try:
                                os.kill(descendant, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            try:
                                os.waitpid(descendant, 0)
                            except ChildProcessError:
                                pass
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
