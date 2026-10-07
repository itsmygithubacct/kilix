"""`kilix pty` subcommands, run through the real launcher against a fake broker.

The fake is a shell script that records its argv and prints a canned reply, so
these pin what the launcher sends and what it makes of the answer, not what a
real broker would do. The archive tests use real zstd. Everything runs against
scratch storage and runtime directories; nothing here may reach a live broker.
"""

import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "kilix"
FIXTURE = ROOT / "tests" / "fixtures" / "kitten_ls.json"
SESSION = "0123456789abcdef"

FAKE_BROKER = r'''#!/bin/sh
# Records "RUNTIME [--timeout S] VERB ARGS" and replies from $FAKE_DIR/VERB.out.
echo "$*" >> "$FAKE_DIR/calls"
[ "$1" = --runtime-dir ] || exit 64
runtime="$2"; shift 2
timeout=""
[ "$1" != --timeout ] || { timeout="$2"; shift 2; }
verb="$1"
[ ! -e "$FAKE_DIR/hang" ] || exec sleep 60
if [ -e "$FAKE_DIR/$verb.hook" ]; then . "$FAKE_DIR/$verb.hook"; fi
[ ! -f "$FAKE_DIR/$verb.out" ] || cat "$FAKE_DIR/$verb.out"
[ ! -f "$FAKE_DIR/$verb.err" ] || cat "$FAKE_DIR/$verb.err" >&2
if [ -f "$FAKE_DIR/$verb.rc" ]; then exit "$(cat "$FAKE_DIR/$verb.rc")"; fi
exit 0
'''


def status_json(session=SESSION, attached=False):
    return json.dumps({
        "id": session, "broker_pid": 11, "child_pid": 12, "foreground_pgrp": 12,
        "started_millis": 1700000000000, "journal_bytes": 5, "journal_epoch": 1,
        "attached": attached, "replay_complete": True, "rows": 24, "columns": 80,
        "cwd": "/srv/work", "command": "bash"})


class PtyCliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.storage = self.tmp / "storage"
        self.storage.mkdir()
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        self.broker = self.tmp / "broker"
        self.broker.write_text(FAKE_BROKER)
        self.broker.chmod(0o755)
        self.xdg = self.tmp / "xdg"
        self.xdg.mkdir(mode=0o700)
        self.runtime = self.xdg / "kilix-pty-broker"
        self.runtime.mkdir(mode=0o700)
        self.state = self.storage / "state"

    def env(self, **extra):
        env = {key: value for key, value in sandbox_env().items() if key != "COLUMNS"}
        env.update(KILIX_STORAGE_HOME=str(self.storage), XDG_RUNTIME_DIR=str(self.xdg),
                   KITTY_PTY_BROKER_EXECUTABLE=str(self.broker), FAKE_DIR=str(self.fake),
                   HOME=str(self.tmp / "home"))
        env.update(extra)
        return env

    def pty(self, *args, timeout=60, **env):
        return subprocess.run(["bash", str(LAUNCHER), "pty", *args], env=self.env(**env),
                              capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)

    def calls(self):
        path = self.fake / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def reply(self, verb, out=None, err=None, rc=None):
        if out is not None:
            (self.fake / f"{verb}.out").write_text(out)
        if err is not None:
            (self.fake / f"{verb}.err").write_text(err)
        if rc is not None:
            (self.fake / f"{verb}.rc").write_text(str(rc))


class ForwardingTests(PtyCliCase):
    def test_list_json_forwards_flags_and_prints_the_reply_untouched(self):
        body = '[{"id":"%s","reachable":true}]\n' % SESSION
        self.reply("list", out=body)
        result = self.pty("list", "--json", "--all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, body)
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} list --json --all"])

    def test_the_runtime_is_the_one_the_panes_use(self):
        # Not the broker's own default (kitty-pty-broker): the launcher's.
        result = self.pty("path")
        self.assertEqual(result.stdout.strip(), str(self.runtime))
        override = self.tmp / "elsewhere"
        self.assertEqual(
            self.pty("path", KITTY_PTY_BROKER_RUNTIME=str(override)).stdout.strip(),
            str(override))

    def test_timeout_goes_to_the_broker_ahead_of_the_verb(self):
        self.pty("--timeout", "3.5", "list")
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} --timeout 3.5 list"])
        result = self.pty("--timeout", "soon", "list")
        self.assertEqual(result.returncode, 2)

    def test_status_takes_an_id_and_refuses_a_bad_one(self):
        self.reply("status", out=status_json() + "\n")
        result = self.pty("status", SESSION, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["id"], SESSION)
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} status {SESSION} --json"])
        for bad in ("../x", "a b", "", "x" * 65):
            self.assertEqual(self.pty("status", bad).returncode, 2, bad)
        self.assertEqual(len(self.calls()), 1)

    def test_a_broker_failure_is_passed_on(self):
        self.reply("status", err="kitty-pty-broker: query session: not found\n", rc=1)
        result = self.pty("status", SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)

    def test_unknown_options_never_reach_the_broker(self):
        for args in (["list", "--bogus"], ["frobnicate"], ["status"], ["observe"],
                     ["path", "x"], ["reaped", "path"]):
            result = self.pty(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn("usage:", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_help_is_stdout_and_exit_zero(self):
        for flag in ("help", "--help", "-h"):
            result = self.pty(flag)
            self.assertEqual(result.returncode, 0)
            self.assertIn("kill ID [--yes]", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_reaped_passes_through(self):
        self.reply("reaped", out="[]\n")
        self.assertEqual(self.pty("reaped", "--json").stdout, "[]\n")
        self.pty("reaped", "path", SESSION)
        self.assertEqual(self.calls()[-1], f"--runtime-dir {self.runtime} reaped path {SESSION}")

    def test_a_wedged_broker_costs_a_bounded_wait_not_a_hang(self):
        (self.fake / "hang").write_text("")
        started = time.monotonic()
        result = self.pty("list", timeout=30, KILIX_PTY_GUARD="1")
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not answer", result.stderr)

    def test_observe_and_attach_hand_the_terminal_to_the_broker(self):
        self.reply("status", out=status_json(attached=False) + "\n")
        self.assertEqual(self.pty("observe", SESSION, "--from", "1:2").returncode, 0)
        self.assertEqual(self.pty("attach", SESSION, "--resume", "3:4").returncode, 0)
        self.assertEqual(self.calls(), [
            f"--runtime-dir {self.runtime} observe {SESSION} --from 1:2",
            f"--runtime-dir {self.runtime} status {SESSION} --json",
            f"--runtime-dir {self.runtime} attach {SESSION} --resume 3:4"])

    def test_attach_refuses_a_session_that_is_attached(self):
        self.reply("status", out=status_json(attached=True) + "\n")
        result = self.pty("attach", SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("is attached to a pane", result.stderr)
        self.assertIn(f"pty observe {SESSION}", result.stderr)
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} status {SESSION} --json"])


class KillTests(PtyCliCase):
    def setUp(self):
        super().setUp()
        self.reply("status", out=f"{SESSION}\tdetached\tpid=12\tbash\n")

    def killed(self):
        return [call for call in self.calls() if " kill " in call]

    def test_non_interactive_kill_needs_yes(self):
        result = self.pty("kill", SESSION)
        self.assertEqual(result.returncode, 2)
        self.assertIn("without --yes", result.stderr)
        self.assertEqual(self.killed(), [])

    def test_yes_ends_the_session_in_either_position(self):
        for args in (("kill", SESSION, "--yes"), ("kill", "--yes", SESSION)):
            result = self.pty(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.killed()), 2)

    def test_a_missing_session_is_not_asked_about(self):
        self.reply("status", err="kitty-pty-broker: query session: not found\n", rc=1)
        result = self.pty("kill", SESSION, "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.killed(), [])

    def ask(self, answer):
        master, slave = pty.openpty()
        process = subprocess.Popen(
            ["bash", str(LAUNCHER), "pty", "kill", SESSION], env=self.env(),
            stdin=slave, stdout=subprocess.PIPE, stderr=slave, text=True)
        os.close(slave)
        try:
            deadline = time.monotonic() + 30
            seen = b""
            while b"[y/N]" not in seen and time.monotonic() < deadline:
                if select.select([master], [], [], 0.2)[0]:
                    seen += os.read(master, 4096)
            self.assertIn(b"[y/N]", seen)
            os.write(master, answer + b"\n")
            process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            os.close(master)
        return process.returncode

    def test_a_terminal_is_asked_and_anything_but_yes_declines(self):
        self.assertEqual(self.ask(b"n"), 1)
        self.assertEqual(self.killed(), [])
        self.assertEqual(self.ask(b""), 1)
        self.assertEqual(self.killed(), [])
        self.assertEqual(self.ask(b"y"), 0)
        self.assertEqual(len(self.killed()), 1)


class PaneTests(PtyCliCase):
    def kitten(self, session=SESSION):
        document = json.loads(FIXTURE.read_text())
        windows = [w for o in document for t in o["tabs"] for w in t["windows"]]
        windows[0]["env"]["KITTY_PTY_BROKER_SESSION"] = session
        windows[1]["env"].pop("KITTY_PTY_BROKER_SESSION", None)
        self.pane_ids = (windows[0]["id"], windows[1]["id"])
        listing = self.tmp / "ls.json"
        listing.write_text(json.dumps(document))
        script = self.tmp / "kitten"
        script.write_text(f"#!/bin/sh\ncat {listing}\n")
        script.chmod(0o755)
        return script

    def test_pane_prints_the_session_of_that_pane(self):
        kitten = self.kitten()
        result = self.pty("pane", str(self.pane_ids[0]), KILIX_KITTEN=str(kitten))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), SESSION)

    def test_pane_without_a_session_or_without_a_pane_fails_plainly(self):
        kitten = self.kitten()
        none = self.pty("pane", str(self.pane_ids[1]), KILIX_KITTEN=str(kitten))
        self.assertEqual(none.returncode, 1)
        self.assertIn("no persistent session", none.stderr)
        missing = self.pty("pane", "999999", KILIX_KITTEN=str(kitten))
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no kilix pane", missing.stderr)
        self.assertEqual(self.pty("pane", "abc").returncode, 2)

    def test_status_by_pane_asks_about_the_panes_session(self):
        kitten = self.kitten()
        self.reply("status", out=status_json() + "\n")
        result = self.pty("status", "--pane", str(self.pane_ids[0]), "--json",
                          KILIX_KITTEN=str(kitten))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} status {SESSION} --json"])


class StaticTests(unittest.TestCase):
    def test_every_verb_is_dispatched_and_documented(self):
        launcher = LAUNCHER.read_text()
        usage = launcher[launcher.index("_kilix_pty_usage() {"):launcher.index("_kilix_pty_valid_id()")]
        for verb in ("list", "status", "observe", "attach", "kill", "path", "pane",
                     "reaped", "help"):
            self.assertIn(f"  {verb}", usage, verb)
        self.assertIn("pty|pty-manager|pty-tui)", launcher)
        self.assertIn("_kilix_pty_cli", launcher)

    def test_no_non_interactive_broker_call_runs_without_a_deadline(self):
        launcher = LAUNCHER.read_text()
        body = launcher[launcher.index("_kilix_pty_call() {"):launcher.index("_kilix_pty_pane_session() {")]
        self.assertIn('timeout -k 2 "$guard" "$broker"', body)
        live = launcher[launcher.index("_kilix_transcript_live_ids() {"):launcher.index("# --- kilix pty")]
        self.assertIn('timeout -k 2 "$limit" "$broker"', live)
        self.assertNotIn('ids="$("$broker"', live)


if __name__ == "__main__":
    unittest.main()
