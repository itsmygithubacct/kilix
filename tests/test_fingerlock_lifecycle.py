"""Process death and socket admission with disposable, unprivileged agents.

No system service, logind session or reader is used. The parent retains the
listening socket like a socket activator, across death of the serving child.
"""
import os
import select
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import fingerlock_agent as agent
from fingerlock_core import AccessDenied

CHILD = r'''
import os, socket, sys
sys.path.insert(0, sys.argv[1])
import fingerlock_agent as agent

def forbidden(*args, **kwargs):
    raise AssertionError("physical services are forbidden in this fixture")

listener = socket.socket(fileno=int(sys.argv[2]))
server = agent.Agent(
    allowed_uid=int(sys.argv[3]), client_factory=forbidden, approve=forbidden,
    session_lookup=lambda pid: agent.CallerSession("fixture", os.getuid(), True,
                                                 "active", "seat0", False, False))
release = server._release_slot
def released(uid):
    release(uid)
    print("released", flush=True)
server._release_slot = released
print("ready", flush=True)
try:
    server.serve(listener)
finally:
    server.custody.close()
    listener.close()
'''


class ProcessLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = str(Path(__file__).resolve().parents[1] / "config")

    def listener(self, name):
        path = str(Path(self.tmp.name) / name)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(path)
        os.chmod(path, 0o600)
        listener.listen(4)
        return listener, agent.AgentClient(path, timeout=3, trusted_uid=os.getuid())

    def start(self, listener, uid):
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", CHILD, self.source,
                                  str(listener.fileno()), str(uid)],
                                 pass_fds=(listener.fileno(),), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.addCleanup(cleanup)
        self.assertTrue(select.select([child.stdout], [], [], 5)[0], "agent did not start")
        self.assertEqual(child.stdout.readline(), b"ready\n")
        return child

    def call(self, child, operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        finally:
            # The response precedes admission release. Synchronize through a
            # fixture-only pipe so a later assertion cannot hit transient EBUSY.
            self.assertTrue(select.select([child.stdout], [], [], 5)[0])
            self.assertEqual(child.stdout.readline(), b"released\n")

    def test_process_crash_and_restart_do_not_restore_custody(self):
        listener, client = self.listener("restart.sock")
        child = self.start(listener, os.getuid())
        self.assertFalse(self.call(child, client.status)["held"])
        self.call(child, client.deposit, bytes(range(32)), "disposable-locker", ttl=60)
        self.assertTrue(self.call(child, client.status)["held"])
        child.kill()
        self.assertLess(child.wait(timeout=5), 0)
        child = self.start(listener, os.getuid())
        self.assertFalse(self.call(child, client.status)["held"])
        self.assertFalse(self.call(child, client.forget))
        with self.assertRaises(AccessDenied):
            self.call(child, client.confirm, "disposable-locker", bytes(32))

    def test_foreign_owner_socket_rejects_without_affecting_own_custody(self):
        own, client = self.listener("own.sock")
        foreign, other = self.listener("foreign.sock")
        child = self.start(own, os.getuid())
        other_child = self.start(foreign, os.getuid() + 1)
        self.call(child, client.deposit, bytes(range(32)), "disposable-locker", ttl=60)
        for call in (other.status, other.forget,
                     lambda: other.deposit(bytes(32), "foreign-locker")):
            with self.subTest(call=call), self.assertRaises(AccessDenied):
                self.call(other_child, call)
        self.assertTrue(self.call(child, client.status)["held"])
        self.assertTrue(self.call(child, client.forget))
        self.assertFalse(self.call(child, client.status)["held"])


if __name__ == "__main__":
    unittest.main()
