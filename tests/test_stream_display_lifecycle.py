"""A pane's hangup must leave display shutdown to its owner."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("Xvfb"), "Xvfb unavailable")
class DisplayLifetimeTests(unittest.TestCase):
    def test_group_hangup_preserves_clients_until_owned_cleanup(self):
        code = r'''
import json, os, signal, sys
sys.path.insert(0, sys.argv[1])
import stream
from Xlib import display
signal.signal(signal.SIGHUP, lambda *_: None)
sup = stream.StreamSupervisor("hangup-check")
server = None
connection = None
try:
    number = sup.pick_display()
    server = sup.start_xvfb(number, 160, 120)
    os.environ["XAUTHORITY"] = sup.xauth
    connection = display.Display(":" + str(number))
    window = connection.screen().root.create_window(0, 0, 10, 10, 0, 0)
    connection.sync()
    print(json.dumps({"owner": os.getpid(), "server": server.pid}), flush=True)
    sys.stdin.readline()
    # A reset destroys the window and disconnects this client.
    window.get_geometry()
    assert server.poll() is None
finally:
    try:
        if connection is not None:
            connection.close()
    finally:
        sup.cleanup()
assert server.poll() is not None
print("closed", flush=True)
'''
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HOME": tmp, "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                   "GPU_TERMINAL_HOME": tmp + "/storage",
                   "KILIX_SESSION_HOME": tmp + "/session"}
            proc = subprocess.Popen([sys.executable, "-c", code, str(ROOT / "config")],
                                    env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                import select
                self.assertTrue(select.select([proc.stdout], [], [], 10)[0],
                                "display startup timed out")
                ready = proc.stdout.readline()
                self.assertTrue(ready, "display child did not report readiness: " +
                                (proc.stderr.read() if proc.poll() is not None else ""))
                identity = json.loads(ready)
                self.assertEqual(identity["owner"], proc.pid)
                os.killpg(proc.pid, signal.SIGHUP)
                stdout, stderr = proc.communicate("check\n", timeout=10)
                self.assertEqual(proc.returncode, 0, stderr)
                self.assertEqual(stdout.strip(), "closed")
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=10)
