"""`kilix pty` subcommands, run through the real launcher against a fake broker.

The fake is a shell script that records its argv and prints a canned reply, so
these pin what the launcher sends and what it makes of the answer, not what a
real broker would do. The archive tests use real zstd. Everything runs against
scratch storage and runtime directories; nothing here may reach a live broker.
"""

import base64
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

    def pty_tty(self, *args, answer=None, timeout=60, **env):
        """Run `kilix pty ARGS` with a terminal for stdin and stderr."""
        master, slave = pty.openpty()
        process = subprocess.Popen(
            ["bash", str(LAUNCHER), "pty", *args], env=self.env(**env),
            stdin=slave, stdout=subprocess.PIPE, stderr=slave, text=True)
        os.close(slave)
        try:
            seen = b""
            if answer is not None:
                deadline = time.monotonic() + 30
                while b"[y/N]" not in seen and time.monotonic() < deadline \
                        and process.poll() is None:
                    if select.select([master], [], [], 0.2)[0]:
                        seen += os.read(master, 4096)
                os.write(master, answer + b"\n")
            out, _ = process.communicate(timeout=timeout)
            while select.select([master], [], [], 0.2)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                seen += chunk
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
        return process.returncode, out, seen.decode(errors="replace")

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
    def test_list_forwards_flags_and_prints_the_reply_untouched(self):
        body = f"{SESSION}\tdetached\tpid=12\tbash\n"
        self.reply("list", out=body)
        result = self.pty("list", "--all")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, body)
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} list --all"])

    def test_list_json_is_an_envelope_that_separates_unreachable_sessions(self):
        sessions = json.loads(f"[{status_json()}]")
        sessions[0]["reachable"] = True
        wedged = {"id": "feedfeedfeedfeed", "reachable": False, "error": "timeout"}
        self.reply("list", out=json.dumps(sessions + [wedged]))
        result = self.pty("list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(list(document)[:3], ["schema", "runtime", "timeout_seconds"])
        self.assertEqual(document["schema"], "kilix.pty/v1")
        self.assertEqual(document["runtime"], str(self.runtime))
        self.assertEqual(document["timeout_seconds"], 2.0)
        self.assertEqual([s["id"] for s in document["sessions"]], [SESSION])
        self.assertEqual(document["unreachable"], [wedged])
        # Unreachable sessions are always asked for: a caller must be able to
        # tell "did not answer" from "not there".
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} list --json --all"])

    def test_json_envelopes_carry_the_timeout_in_force(self):
        self.reply("list", out="[]")
        document = json.loads(self.pty("--timeout", "3.5", "list", "--json").stdout)
        self.assertEqual(document["timeout_seconds"], 3.5)
        self.assertEqual((document["sessions"], document["unreachable"]), ([], []))
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} --timeout 3.5 list --json --all"])

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

    def test_status_json_wraps_the_session(self):
        self.reply("status", out=status_json() + "\n")
        result = self.pty("status", SESSION, "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["schema"], "kilix.pty/v1")
        self.assertEqual(document["session"]["id"], SESSION)
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} status {SESSION} --json"])

    def test_status_text_is_the_brokers_line(self):
        self.reply("status", out=f"{SESSION}\tdetached\tpid=12\tbash\n")
        result = self.pty("status", SESSION)
        self.assertEqual(result.stdout, f"{SESSION}\tdetached\tpid=12\tbash\n")
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} status {SESSION}"])

    def test_status_of_a_missing_session_is_exit_4_with_an_envelope(self):
        self.reply("status", err="kitty-pty-broker: query session: session not found\n", rc=1)
        result = self.pty("status", SESSION, "--json")
        self.assertEqual(result.returncode, 4)
        document = json.loads(result.stdout)
        self.assertEqual((document["result"], document["id"]), ("not_found", SESSION))

    def test_a_bad_id_never_reaches_the_broker(self):
        for bad in ("../x", "a b", "", "x" * 65, ".", ".."):
            self.assertEqual(self.pty("status", bad).returncode, 2, bad)
        self.assertEqual(self.calls(), [])

    def test_a_broker_failure_is_passed_on(self):
        self.reply("status", err="kitty-pty-broker: query session: not found\n", rc=1)
        result = self.pty("status", SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)

    def test_unknown_options_never_reach_the_broker(self):
        for args in (["list", "--bogus"], ["frobnicate"], ["status"], ["observe"],
                     ["path", "x"], ["reaped", "path"], ["observe", SESSION, "--lines", "5"]):
            result = self.pty(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn("usage:", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_help_is_stdout_and_exit_zero(self):
        for flag in ("help", "--help", "-h"):
            result = self.pty(flag)
            self.assertEqual(result.returncode, 0)
            self.assertIn("kill ID --yes", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_reaped_passes_through_and_json_is_an_envelope(self):
        self.reply("reaped", out="[]\n")
        self.assertEqual(self.pty("reaped").stdout, "[]\n")
        document = json.loads(self.pty("reaped", "--json").stdout)
        self.assertEqual((document["schema"], document["reaped"]), ("kilix.pty/v1", []))
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
        code, _, seen = self.pty_tty("attach", SESSION, "--resume", "3:4")
        self.assertEqual(code, 0, seen)
        self.assertEqual(self.calls(), [
            f"--runtime-dir {self.runtime} observe {SESSION} --from 1:2",
            f"--runtime-dir {self.runtime} status {SESSION} --json",
            f"--runtime-dir {self.runtime} attach {SESSION} --resume 3:4"])

    def test_attach_refuses_a_session_that_is_attached(self):
        self.reply("status", out=status_json(attached=True) + "\n")
        code, _, seen = self.pty_tty("attach", SESSION)
        self.assertEqual(code, 1)
        self.assertIn("is attached to a pane", seen)
        self.assertIn(f"pty observe {SESSION}", seen)
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} status {SESSION} --json"])

    def test_attach_is_for_a_terminal_only(self):
        result = self.pty("attach", SESSION)
        self.assertEqual(result.returncode, 2)
        self.assertIn("attach needs a terminal", result.stderr)
        self.assertIn(f"observe {SESSION} --once", result.stderr)
        self.assertEqual(self.calls(), [])


class KillTests(PtyCliCase):
    def setUp(self):
        super().setUp()
        self.reply("status", out=status_json() + "\n")
        self.alive = json.dumps([json.loads(status_json())])
        self.reply("list", out=self.alive)

    def vanishes(self):
        """After a kill request the session leaves the listing."""
        (self.fake / "kill.hook").write_text(f'echo "[]" > "{self.fake}/list.out"\n')

    def killed(self):
        return [call for call in self.calls() if " kill " in call]

    def kill(self, *args, **env):
        result = self.pty("kill", *args, **env)
        receipt = json.loads(result.stdout) if "--json" in args and result.stdout else None
        return result, receipt

    def test_non_interactive_kill_needs_yes(self):
        result = self.pty("kill", SESSION)
        self.assertEqual(result.returncode, 2)
        self.assertIn("without --yes", result.stderr)
        self.assertEqual(self.killed(), [])

    def test_a_verified_kill_is_exit_0_with_a_receipt(self):
        self.vanishes()
        result, receipt = self.kill(SESSION, "--yes", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(receipt["schema"], "kilix.pty/v1")
        self.assertEqual((receipt["result"], receipt["id"], receipt["request_sent"]),
                         ("verified_absent", SESSION, True))
        self.assertEqual(receipt["started_millis"], 1700000000000)
        self.assertIsNone(receipt["reason"])
        self.assertEqual(len(self.killed()), 1)
        # It verified by listing, after asking.
        self.assertEqual(self.calls()[-1], f"--runtime-dir {self.runtime} list --json --all")

    def test_yes_works_in_either_position_and_text_says_the_verdict(self):
        self.vanishes()
        result = self.pty("kill", "--yes", SESSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"kilix pty kill {SESSION}: verified_absent", result.stdout)

    def test_a_session_still_listed_is_uncertain_not_success(self):
        started = time.monotonic()
        result, receipt = self.kill(SESSION, "--yes", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual((receipt["result"], receipt["reason"], receipt["request_sent"]),
                         ("uncertain", "still_listed", True))
        self.assertIn("re-list before retrying", receipt["message"])
        # The broker's 1.5 s grace plus 2 s, no more.
        self.assertGreaterEqual(time.monotonic() - started, 3.0)
        self.assertLess(time.monotonic() - started, 15)

    def test_a_kill_the_broker_never_answered_is_still_uncertain(self):
        (self.fake / "kill.hook").write_text("exec sleep 60\n")
        # kill hangs, so the request may or may not have landed.
        result, receipt = self.kill(SESSION, "--yes", "--json", KILIX_PTY_GUARD="1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt["result"], "uncertain")
        self.assertTrue(receipt["request_sent"])

    def test_the_callers_own_session_is_refused_before_anything_is_asked(self):
        result, receipt = self.kill(SESSION, "--yes", "--json",
                                    KITTY_PTY_BROKER_SESSION=SESSION)
        self.assertEqual(result.returncode, 3)
        self.assertEqual((receipt["result"], receipt["reason"], receipt["request_sent"]),
                         ("refused", "own_session", False))
        self.assertEqual(self.calls(), [])

    def test_expect_started_binds_the_kill_to_the_session_that_was_seen(self):
        result, receipt = self.kill(SESSION, "--yes", "--json", "--expect-started", "1699999999999")
        self.assertEqual(result.returncode, 3)
        self.assertEqual((receipt["result"], receipt["reason"]), ("refused", "started_mismatch"))
        self.assertEqual((receipt["started_millis"], receipt["expected_started_millis"]),
                         (1700000000000, 1699999999999))
        self.assertEqual(self.killed(), [])
        self.vanishes()
        result, receipt = self.kill(SESSION, "--yes", "--json", "--expect-started", "1700000000000")
        self.assertEqual((result.returncode, receipt["result"]), (0, "verified_absent"))

    def test_a_missing_session_is_exit_4_and_nothing_is_sent(self):
        self.reply("status", err="kitty-pty-broker: query session: session not found\n", rc=1)
        result, receipt = self.kill(SESSION, "--yes", "--json")
        self.assertEqual(result.returncode, 4)
        self.assertEqual((receipt["result"], receipt["request_sent"]), ("not_found", False))
        self.assertEqual(self.killed(), [])

    def test_a_lookup_that_fails_sends_nothing_and_says_so(self):
        self.reply("status", err="kitty-pty-broker: query session: something odd\n", rc=1)
        result, receipt = self.kill(SESSION, "--yes", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual((receipt["result"], receipt["reason"], receipt["request_sent"]),
                         ("uncertain", "status_failed", False))
        self.assertEqual(self.killed(), [])

    def test_bad_arguments_are_usage_errors(self):
        for args in (("--yes",), (SESSION, "--expect-started", "soon"), (SESSION, "--bogus"),
                     (SESSION, "other0000000000000"), ("../x", "--yes")):
            self.assertEqual(self.pty("kill", *args).returncode, 2, args)
        self.assertEqual(self.calls(), [])

    def test_a_terminal_is_asked_and_anything_but_yes_declines(self):
        self.vanishes()
        code, _, seen = self.pty_tty("kill", SESSION, answer=b"n")
        self.assertEqual(code, 3, seen)
        self.assertIn("not ended", seen)
        self.assertEqual(self.killed(), [])
        self.assertEqual(self.pty_tty("kill", SESSION, answer=b"")[0], 3)
        self.assertEqual(self.killed(), [])
        code, _, seen = self.pty_tty("kill", SESSION, answer=b"y")
        self.assertEqual(code, 0, seen)
        self.assertEqual(len(self.killed()), 1)


class ObserveOnceTests(PtyCliCase):
    def setUp(self):
        super().setUp()
        self.lines = "".join(f"line {n}\n" for n in range(1, 501))
        self.reply("observe", out=self.lines, err="kitty-pty-broker: cursor=7:4096\n")

    def observe(self, *args):
        return self.pty("observe", SESSION, "--once", *args)

    def test_json_default_is_the_last_200_lines_with_a_cursor(self):
        result = self.observe("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["schema"], "kilix.pty/v1")
        self.assertEqual((document["id"], document["journal_epoch"], document["cursor"]),
                         (SESSION, 7, "7:4096"))
        self.assertEqual(document["total_bytes"], len(self.lines))
        self.assertTrue(document["truncated"])
        self.assertTrue(document["untrusted"])
        body = base64.b64decode(document["bytes_b64"]).decode()
        self.assertEqual(body.splitlines()[0], "line 301")
        self.assertEqual(len(body.splitlines()), 200)
        self.assertNotIn("text", document)
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} observe {SESSION}"])

    def test_bytes_bound_keeps_the_tail_and_lines_bound_is_exact(self):
        document = json.loads(self.observe("--bytes", "20", "--json").stdout)
        self.assertEqual(len(base64.b64decode(document["bytes_b64"])), 20)
        self.assertTrue(base64.b64decode(document["bytes_b64"]).endswith(b"line 500\n"))
        document = json.loads(self.observe("--lines", "3", "--json").stdout)
        self.assertEqual(base64.b64decode(document["bytes_b64"]).decode(),
                         "line 498\nline 499\nline 500\n")
        small = json.loads(self.observe("--lines", "1000", "--json").stdout)
        self.assertFalse(small["truncated"])

    def test_the_default_byte_cap_applies_to_long_lines(self):
        self.reply("observe", out="x" * 100 + "\n" + "y" * 200000 + "\n")
        document = json.loads(self.observe("--json").stdout)
        self.assertEqual(len(base64.b64decode(document["bytes_b64"])), 65536)
        self.assertTrue(document["truncated"])

    def test_raw_output_goes_to_stdout_without_a_terminal(self):
        result = self.observe("--lines", "2")
        self.assertEqual(result.stdout, "line 499\nline 500\n")

    def test_resume_cursor_is_passed_on(self):
        self.observe("--from", "7:100", "--json")
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} observe {SESSION} --from 7:100"])

    def test_both_bounds_together_are_a_usage_error(self):
        result = self.observe("--lines", "2", "--bytes", "9")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.pty("observe", SESSION, "--text").returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_text_goes_through_the_transcript_cleaner(self):
        rendered = "".join(f"\x1b[3{n % 8}mrow {n}\x1b[0m\r\n" for n in range(1, 40))
        self.reply("observe", out=rendered, err="kitty-pty-broker: cursor=1:5\n")
        result = self.observe("--text", "--lines", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["row 37", "row 38", "row 39"])
        document = json.loads(self.observe("--text", "--json").stdout)
        self.assertNotIn("\x1b", document["text"])
        self.assertIn("row 39", document["text"])
        self.assertNotIn("bytes_b64", document)

    def test_a_broker_refusal_is_reported(self):
        self.reply("observe", out="", err="kitty-pty-broker: observe X: too many observers\n", rc=1)
        result = self.observe("--json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("too many observers", result.stderr)
        self.assertEqual(result.stdout, "")


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
