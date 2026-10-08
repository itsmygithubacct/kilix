"""Exercise the real facade and pinned module with private queue state."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RalphFacadeTests(unittest.TestCase):
    def test_mailbox_roundtrip_without_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = {"PATH": os.environ["PATH"], "HOME": str(home),
                   "XDG_CONFIG_HOME": str(home / "config"),
                   "XDG_CACHE_HOME": str(home / "cache"),
                   "XDG_DATA_HOME": str(home / "data"),
                   "XDG_STATE_HOME": str(home / "state")}
            def command(*args):
                done = subprocess.run([str(ROOT / "kilix"), "ralph", *args], env=env,
                                      cwd=home, text=True, capture_output=True, timeout=10)
                self.assertEqual(done.returncode, 0, done.stderr)
                return json.loads(done.stdout)
            command("target", "add", "--target", "test:conversation:generation", "--mailbox")
            msg = command("enqueue", "--target", "test:conversation:generation", "--text", "hello")
            claimed = command("receive", "--target", "test:conversation:generation", "--idle")
            self.assertEqual(msg["id"], claimed["message"]["id"])
            done = command("complete", msg["id"], "--target", "test:conversation:generation")
            self.assertEqual(done["status"], "completed")
            self.assertEqual(command("queue"), [])
            self.assertFalse((home / "state/kilix/agent-delivery").exists())

    def test_missing_module_exits_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PATH": os.environ["PATH"], "HOME": tmp, "KILIX_RALPH_HOME": tmp}
            done = subprocess.run([str(ROOT / "kilix"), "ralph", "--help"],
                                  env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(done.returncode, 1)
            self.assertIn("module unavailable", done.stderr)
