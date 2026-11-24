"""kilix_sdk.hostproc answers the same process questions on Linux and FreeBSD.

Each answer is checked against something the test controls (a child it
started, a socket pair it made) or against ps(1), which both hosts ship.
"""
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
from kilix_sdk import hostproc  # noqa: E402
sys.path.insert(0, str(ROOT / "tests"))
from _env_support import sandbox_env  # noqa: E402

SUPPORTED = hostproc.IS_LINUX or hostproc.IS_FREEBSD


def ps_field(pid, field):
    result = subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)],
                            capture_output=True, text=True)
    return result.stdout.strip()


@unittest.skipUnless(SUPPORTED, "Linux or FreeBSD only")
class ProcessFacts(unittest.TestCase):
    def setUp(self):
        self.child = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(self._reap)

    def _reap(self):
        if self.child.poll() is None:
            self.child.kill()
        self.child.wait()

    def test_parent_session_owner_and_state(self):
        pid = self.child.pid
        self.assertEqual(hostproc.parent_pid(pid), os.getpid())
        self.assertEqual(hostproc.session_id(pid), pid)
        self.assertEqual(hostproc.owner_uid(pid), os.getuid())
        self.assertIn(hostproc.process_state(pid), {"S", "I", "R"})
        self.assertEqual(hostproc.parent_pid(os.getpid()), os.getppid())

    def test_executable_and_cmdline(self):
        pid = self.child.pid
        self.assertEqual(hostproc.executable(pid),
                         os.path.realpath(subprocess.run(
                             ["sh", "-c", "command -v sleep"],
                             capture_output=True, text=True).stdout.strip()))
        self.assertEqual(hostproc.cmdline(pid), [b"sleep", b"30"])

    def test_environ_of_own_child(self):
        child = subprocess.Popen(["sleep", "30"], env=sandbox_env(KILIX_HOSTPROC_PROBE="yes"))
        try:
            for _ in range(100):
                values = hostproc.process_environment(child.pid) or []
                if b"KILIX_HOSTPROC_PROBE=yes" in values:
                    break
                time.sleep(0.01)
            self.assertIn(b"KILIX_HOSTPROC_PROBE=yes", values)
        finally:
            child.kill()
            child.wait()

    def test_children_lists_direct_children_only(self):
        self.assertIn(self.child.pid, hostproc.children(os.getpid()))
        self.assertEqual(hostproc.children(self.child.pid), [])

    def test_start_token_names_one_incarnation(self):
        first = hostproc.start_token(self.child.pid)
        self.assertTrue(first)
        self.assertEqual(hostproc.start_token(self.child.pid), first)
        # The token tells incarnations of one pid apart, so it only has to
        # differ for a later start; Linux counts start time in 10 ms ticks.
        time.sleep(0.05)
        other = subprocess.Popen(["sleep", "30"])
        try:
            self.assertNotEqual(hostproc.start_token(other.pid), first)
        finally:
            other.kill()
            other.wait()

    def test_dead_and_zombie_processes_have_no_token(self):
        pid = self.child.pid
        os.kill(pid, signal.SIGKILL)
        for _ in range(200):
            if hostproc.process_state(pid) == "Z":
                break
            time.sleep(0.01)
        self.assertEqual(hostproc.process_state(pid), "Z")
        self.assertIsNone(hostproc.start_token(pid))
        self.child.wait()
        self.assertIsNone(hostproc.start_token(pid))
        self.assertIsNone(hostproc.parent_pid(pid))
        self.assertEqual(ps_field(pid, "pid"), "")


@unittest.skipUnless(SUPPORTED, "Linux or FreeBSD only")
class ProcessControls(unittest.TestCase):
    def run_child(self, body):
        script = textwrap.dedent(f"""
            import os, signal, subprocess, sys, time
            sys.path.insert(0, {str(ROOT / 'config')!r})
            from kilix_sdk import hostproc
        """) + textwrap.dedent(body)
        return subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True, timeout=30)

    def test_parent_death_signal_reaches_the_orphan(self):
        result = self.run_child("""
            report_r, report_w = os.pipe()     # grandchild -> test
            armed_r, armed_w = os.pipe()       # grandchild -> middle
            middle = os.fork()
            if middle == 0:
                os.close(report_r)
                grandchild = os.fork()
                if grandchild == 0:
                    os.close(armed_r)
                    signal.signal(signal.SIGTERM,
                                  lambda *_: (os.write(report_w, b"T"), os._exit(0)))
                    hostproc.set_parent_death_signal(signal.SIGTERM)
                    os.write(armed_w, b"R")
                    time.sleep(20)
                    os._exit(1)
                os.close(armed_w)
                os.read(armed_r, 1)            # exit only once the watch is armed
                os._exit(0)
            os.close(report_w)
            os.close(armed_r)
            os.close(armed_w)
            os.waitpid(middle, 0)
            print(os.read(report_r, 1).decode())
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "T")

    def test_subreaper_adopts_orphaned_grandchildren(self):
        result = self.run_child("""
            hostproc.set_child_subreaper()
            pid_r, pid_w = os.pipe()           # middle -> test: grandchild pid
            go_r, go_w = os.pipe()             # test -> grandchild: exit now
            middle = os.fork()
            if middle == 0:
                grandchild = os.fork()
                if grandchild == 0:
                    os.read(go_r, 1)
                    os._exit(0)
                os.write(pid_w, str(grandchild).encode())
                os._exit(0)
            os.close(pid_w)
            grandchild = int(os.read(pid_r, 32))
            os.waitpid(middle, 0)
            for _ in range(200):
                if hostproc.parent_pid(grandchild) == os.getpid():
                    break
                time.sleep(0.01)
            adopted = grandchild in hostproc.children(os.getpid())
            os.write(go_w, b"x")
            reaped, _ = os.waitpid(grandchild, 0)
            print(adopted, reaped == grandchild)
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "True True")

    def test_no_new_privs_is_accepted(self):
        result = self.run_child("hostproc.set_no_new_privs(); print('ok')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")


@unittest.skipUnless(SUPPORTED, "Linux or FreeBSD only")
class SocketPeers(unittest.TestCase):
    def test_peer_credentials_name_this_process(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        pid, uid, gid = hostproc.peer_credentials(left)
        self.assertEqual(uid, os.geteuid())
        self.assertEqual(gid, os.getegid())
        self.assertIn(pid, {None, os.getpid()})
        self.assertTrue(hostproc.peer_is_owner(left))

    def test_unreadable_peer_is_not_the_owner(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        self.assertFalse(hostproc.peer_is_owner(sock))


if __name__ == "__main__":
    unittest.main()
