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

    def test_help_is_read_only_and_loop_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "TMPDIR": "/tmp",
                   "XDG_STATE_HOME": str(home / "state")}
            def command(*args, expected=0):
                result = subprocess.run([str(ROOT / "kilix"), "ralph", *args],
                    env=env, cwd=home, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stderr)
                return result
            command("--help")
            self.assertEqual(list(home.iterdir()), [])
            command("target", "add", "--target", "mail", "--mailbox")
            first = json.loads(command("enqueue", "--target", "mail", "--text", "first").stdout)
            command("message", "cancel", first["id"])
            loop = json.loads(command("create", "--target", "mail", "--text", "next",
                "--interval", "60", "--max-wakes", "1").stdout)
            command("run", loop["id"], "--once", "--poll", "0", expected=1)
            state = json.loads(command("status", loop["id"]).stdout)
            self.assertEqual(state["state"], "paused")
            command("serve", "--once")
            self.assertEqual(json.loads(command("queue").stdout), [])
            self.assertFalse((home / ".local/gpu_terminal/settings.conf").exists())
