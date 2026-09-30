import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
from kilix_tmux import dispatch


class ValidationTests(unittest.TestCase):
    def test_invalid_requests_never_invoke_tmux(self):
        base = {"operation": "send", "socket": "/tmp/unused", "target": "fixture", "text": "hello"}
        requests = [None, [], {}, dict(base, operation=[]),
                    {k: v for k, v in base.items() if k != "socket"},
                    dict(base, socket="relative"), dict(base, target="fix*"),
                    dict(base, target="-x"), dict(base, target="fixture:window"),
                    dict(base, text=""), dict(base, text="line\nEnter"),
                    dict(base, text="\x1b[31m"), dict(base, text="x" * 65537),
                    dict(base, dry_run="false"), dict(base, schema="v2"),
                    dict(base, command="extra"),
                    {"operation": "read", "socket": "/tmp/unused", "target": "fixture", "lines": True},
                    {"operation": "read", "socket": "/tmp/unused", "target": "fixture", "lines": 2001},
                    {"operation": "key", "socket": "/tmp/unused", "target": "fixture", "keys": ["Enter", "-l"]},
                    {"operation": "key", "socket": "/tmp/unused", "target": "fixture", "keys": []}]
        with patch("kilix_tmux.control.subprocess.run") as run:
            for request in requests:
                with self.subTest(request=str(request)[:100]):
                    self.assertEqual(dispatch(request)["code"], "EUSAGE")
            run.assert_not_called()

    def test_timeout_and_missing_executable_are_errors(self):
        req = {"operation": "list", "socket": "/tmp/unused"}
        for exc, code in [(FileNotFoundError(), "ETMUX"),
                          (subprocess.TimeoutExpired("tmux", 5), "ETIMEDOUT")]:
            with patch("kilix_tmux.control.subprocess.run", side_effect=exc):
                self.assertEqual(dispatch(req)["code"], code)

    def test_type_partial_failure_reports_sent_but_not_submitted(self):
        from kilix_tmux.control import ControlError
        sessions = [{"id": "$1", "name": "fixture", "panes": [{"id": "%2"}]}]
        with patch("kilix_tmux.control._snapshot", return_value=sessions), \
             patch("kilix_tmux.control._send_literal") as send, \
             patch("kilix_tmux.control._run", side_effect=ControlError("ETMUX", "lost pane")) as run:
            response = dispatch({"operation": "type", "socket": "/tmp/unused", "target": "fixture", "text": "hello"})
        self.assertFalse(response["ok"])
        self.assertEqual(response["details"]["sent"], 5)
        self.assertFalse(response["details"]["submitted"])
        send.assert_called_once_with("/tmp/unused", "%2", "hello")
        self.assertEqual(run.call_args.args[-1], "Enter")

    def test_buffer_failure_cleans_up_and_does_not_enter(self):
        from kilix_tmux.control import ControlError, _send_literal
        with patch("kilix_tmux.control._run", side_effect=["", ControlError("ETMUX", "lost pane"), ""]) as run:
            with self.assertRaises(ControlError):
                _send_literal("/tmp/unused", "%2", "literal;")
        self.assertEqual(run.call_args_list[0].kwargs["input_text"], "literal;")
        self.assertEqual(run.call_args_list[0].args[1], "load-buffer")
        self.assertEqual(run.call_args_list[-1].args[1], "delete-buffer")


@unittest.skipUnless(__import__("shutil").which("tmux"), "tmux unavailable")
class PrivateServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kt-test-")
        self.directory = Path(self.tmp.name)
        self.socket = str(self.directory / "socket")
        self.env = dict(os.environ, HOME=self.tmp.name, PS1="fixture> ", TERM="xterm-256color")
        self.env.pop("TMUX", None)
        self.tmux("-f", "/dev/null", "new-session", "-d", "-s", "fixture", "-x", "200", "-y", "30",
                  "/bin/bash --noprofile --norc")
        self.tmux("set-option", "-g", "default-shell", "/bin/bash")
        self.tmux("set-option", "-g", "default-command", "/bin/bash --noprofile --norc")

    def tearDown(self):
        try:
            subprocess.run(["tmux", "-S", self.socket, "kill-server"], env=self.env,
                           capture_output=True, timeout=5)
        finally:
            self.tmp.cleanup()

    def tmux(self, *args):
        return subprocess.run(["tmux", "-S", self.socket, *args], env=self.env,
                              capture_output=True, text=True, timeout=5, check=True).stdout

    def call(self, operation, **fields):
        result = dispatch(dict(operation=operation, socket=self.socket, **fields))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["schema"], "kilix.tmux/v1")
        return result["data"]

    def await_receipt(self, path, expected):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if path.exists() and path.read_bytes() == expected.encode():
                return
            time.sleep(.02)
        self.fail(f"no target execution receipt: {path}")

    def test_lifecycle_stable_ids_and_dry_run(self):
        initial = self.call("list")["sessions"]
        self.assertEqual(initial[0]["name"], "fixture")
        self.call("new", name="new_session", cwd=self.tmp.name, dry_run=True)
        self.assertEqual(self.call("list")["sessions"], initial)
        created = self.call("new", name="new_session", cwd=self.tmp.name)["session"]
        self.assertEqual(dispatch(dict(operation="new", socket=self.socket, name="new_session"))["code"], "EEXIST")
        self.call("rename", target=created["id"], new_name="renamed", dry_run=True)
        self.assertIn("new_session", [s["name"] for s in self.call("list")["sessions"]])
        renamed = self.call("rename", target=created["id"], new_name="renamed")["session"]
        self.assertEqual(renamed["id"], created["id"])
        self.call("close", target=created["id"], dry_run=True)
        self.call("close", target="renamed")
        self.assertEqual([s["name"] for s in self.call("list")["sessions"]], ["fixture"])

    def test_no_prefix_or_wildcard_targeting(self):
        self.call("new", name="fixture_longer")
        for op, fields in [("read", {}), ("send", {"text": "hello"}),
                           ("type", {"text": "hello"}), ("key", {"keys": ["Enter"]}),
                           ("rename", {"new_name": "bad"}), ("close", {})]:
            response = dispatch(dict(operation=op, socket=self.socket, target="fixt", **fields))
            self.assertEqual(response["code"], "ENOENT")
        self.assertEqual(len(self.call("list")["sessions"]), 2)

    def test_send_never_submits_and_key_enter_executes_in_target(self):
        receipt = self.directory / "receipt"
        text = f"printf '%s' 'literal $(echo unexpected); \"quotes\"' > {shlex.quote(str(receipt))}"
        pane = self.call("list")["sessions"][0]["panes"][0]["id"]
        result = self.call("send", target=pane, text=text)
        self.assertFalse(result["submitted"])
        time.sleep(.1)
        self.assertFalse(receipt.exists())
        submitted = self.call("key", target=pane, keys=["Enter"])
        self.assertTrue(submitted["submitted"])
        self.await_receipt(receipt, 'literal $(echo unexpected); "quotes"')

    def test_type_returns_submission_and_real_target_receipt(self):
        receipt = self.directory / "receipt"
        self.call("type", target="fixture", text=f"printf '%s' '✓ done' > {shlex.quote(str(receipt))}", dry_run=True)
        self.assertFalse(receipt.exists())
        result = self.call("type", target="fixture", text=f"printf '%s' '✓ done' > {shlex.quote(str(receipt))}")
        self.assertTrue(result["submitted"])
        self.assertEqual(result["completion"], "unknown")
        self.await_receipt(receipt, "✓ done")

    def test_trailing_semicolon_preserved_byte_for_byte_without_enter(self):
        # A raw reader records bytes received by the target, independent of
        # capture-pane rendering or shell echo. No benchmark prompts here.
        receipt = self.directory / "bytes"
        reader = self.directory / "reader.py"
        reader.write_text("import os, tty\nfrom pathlib import Path\ntty.setraw(0)\n"
                          f"p=Path({str(receipt)!r})\n"
                          "while True:\n b=os.read(0,1)\n with p.open('ab') as f: f.write(b)\n")
        pane = self.tmux("new-window", "-d", "-t", "=fixture:", "-P", "-F", "#{pane_id}",
                         f"{shlex.quote(sys.executable)} -u {shlex.quote(str(reader))}").strip()
        time.sleep(.1)
        payload = "literal $HOME `never_run` 'quotes' ✓;"
        result = self.call("send", target=pane, text=payload)
        self.assertFalse(result["submitted"])
        self.await_receipt(receipt, payload)
        self.assertEqual(receipt.read_bytes(), payload.encode())
        result = self.call("type", target=pane, text=payload)
        self.assertTrue(result["submitted"])
        self.await_receipt(receipt, payload + payload + "\r")

    def test_ambiguous_session_refused_explicit_panes_work(self):
        self.tmux("split-window", "-d", "-t", "=fixture:", "/bin/bash --noprofile --norc")
        for op, fields in [("read", {}), ("send", {"text": "hello"}),
                           ("type", {"text": "hello"}), ("key", {"keys": ["Enter"]})]:
            result = dispatch(dict(operation=op, socket=self.socket, target="fixture", **fields))
            self.assertEqual(result["code"], "EAMBIGUOUS")
        panes = self.call("list")["sessions"][0]["panes"]
        for pane in panes:
            self.call("read", target=pane["id"], lines=1)
            self.call("read", target=f"fixture:{pane['window']}.{pane['index']}", lines=1)

    def test_read_bound_and_literal_dash(self):
        self.call("send", target="fixture", text="-n does not become a tmux flag")
        time.sleep(.05)
        self.assertIn("-n does not become a tmux flag", self.call("read", target="fixture")["text"])
        self.call("key", target="fixture", keys=["C-u"])
        receipt = self.directory / "done"
        self.call("type", target="fixture", text=f"printf '1\\n2\\n3\\n'; touch {shlex.quote(str(receipt))}")
        self.await_receipt(receipt, "")
        text = self.call("read", target="fixture", lines=2)["text"]
        self.assertLessEqual(len(text.splitlines()), 2)
        self.assertIn("3", text)

    def test_explicit_other_socket_cannot_reach_fixture(self):
        other = str(self.directory / "other")
        self.assertEqual(dispatch(dict(operation="list", socket=other))["data"]["sessions"], [])
        self.assertEqual(dispatch(dict(operation="close", socket=other, target="fixture"))["code"], "ENOSERVER")
        self.assertEqual(self.call("list")["sessions"][0]["name"], "fixture")

    def test_cli_flags_envelope_exit_codes_and_help(self):
        for argv in [("--socket", self.socket, "--json", "list"),
                     ("list", "--json", "--socket", self.socket)]:
            proc = subprocess.run([sys.executable, "-B", "-m", "kilix_tmux", *argv], cwd=ROOT / "config",
                                  capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(json.loads(proc.stdout)["ok"])
        proc = subprocess.run([str(ROOT / "kilix"), "tmux", "--json", "close", "fixture"],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["code"], "EUSAGE")


if __name__ == "__main__":
    unittest.main()
