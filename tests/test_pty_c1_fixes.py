"""Regression tests for the phase C review (reviews/C-kilix/REVIEW.md): P1 x3, P2 x3, and the config race.

Each fails on a27cd9b and passes after the fix.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_cli import LAUNCHER, PtyCliCase, gone_hook, status_json  # noqa: E402
import kilix_pty_request as module  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "kilix.pty.request/v1"
TARGET = "bbbbbbbbbbbbbbbb"
ME = "aaaaaaaaaaaaaaaa"
STARTED = 1700000000000


def kill_request(operation_id="op-1", expect=STARTED, ident=TARGET):
    args = {"id": ident}
    if expect is not None:
        args["expect_started_millis"] = expect
    return json.dumps({"schema": SCHEMA, "verb": "kill", "operation_id": operation_id, "args": args})


class C1Case(PtyCliCase):
    def setUp(self):
        super().setUp()
        self.reply("status", out=status_json(TARGET) + "\n")
        self.reply("list", out=json.dumps([json.loads(status_json(TARGET))]))
        (self.fake / "kill.hook").write_text(gone_hook(self.fake))

    def rearm(self):
        """Back to a healthy target, whatever an earlier kill in the same test did to the fake."""
        self.reply("status", out=status_json(TARGET) + "\n")
        for leftover in ("status.rc", "status.err", "kill.rc", "kill.err"):
            (self.fake / leftover).unlink(missing_ok=True)
        self.reply("list", out=json.dumps([json.loads(status_json(TARGET))]))

    def request(self, payload, *flags, **env):
        env.setdefault("KITTY_PTY_BROKER_SESSION", ME)
        return subprocess.run(["bash", str(LAUNCHER), "pty", "request", *flags, "--request-json", "-"],
                              env=self.env(**env), input=payload, capture_output=True, text=True, timeout=60)

    def kills(self):
        return [call for call in self.calls() if " kill " in call]

    def records(self):
        directory = self.state / "pty-operations"
        return sorted(directory.glob("op-*.json")) if directory.exists() else []


class DurableIntentTests(C1Case):
    """P1: the operation is reserved on disk before anything is done."""

    def crashing_dispatch(self, marker):
        """A child process that reserves the operation, 'dispatches', then dies before any receipt."""
        program = textwrap.dedent(f'''
            import os, sys
            sys.path.insert(0, {str(ROOT / "config")!r})
            import kilix_pty_request as m, kilix_pty as p
            def crash(broker, request, state):
                open({str(marker)!r}, "w").write("dispatched")
                os._exit(97)
            m.run_kill = crash
            broker = p.Broker({str(self.broker)!r}, {str(self.runtime)!r}, None, 10)
            raise SystemExit(m.cmd_request(broker, ["--yes", "--request-json", "-"]))
        ''')
        return subprocess.run([sys.executable, "-c", program], input=kill_request(), text=True,
                              capture_output=True, timeout=60,
                              env=self.env(KILIX_STATE_DIRECTORY=str(self.state)))

    def test_a_real_crash_after_dispatch_leaves_an_intent_that_replays_as_uncertain(self):
        marker = self.tmp / "dispatched"
        crashed = self.crashing_dispatch(marker)
        self.assertEqual(crashed.returncode, 97, crashed.stderr)
        self.assertTrue(marker.exists())
        records = self.records()
        self.assertEqual(len(records), 1, "the intent must be on disk before the dispatch")
        self.assertEqual(json.loads(records[0].read_text())["phase"], "intent")
        result = self.request(kill_request(), "--yes")
        receipt = json.loads(result.stdout)
        self.assertEqual((result.returncode, receipt["result"], receipt["reason"], receipt["duplicate"]),
                         (1, "uncertain", "interrupted", True))
        self.assertTrue(receipt["request_sent"])
        self.assertEqual(self.kills(), [], "a replay must not dispatch again")
        again = json.loads(self.request(kill_request(), "--yes").stdout)
        self.assertEqual((again["result"], again["reason"]), ("uncertain", "interrupted"))
        self.assertEqual(self.kills(), [])

    def test_conflicting_reuse_after_a_crash_is_refused_before_any_dispatch(self):
        self.crashing_dispatch(self.tmp / "dispatched")
        result = self.request(kill_request(expect=STARTED + 1), "--yes")
        self.assertEqual((result.returncode, json.loads(result.stdout)["reason"]), (3, "operation_id_reused"))
        self.assertEqual(self.kills(), [])

    def test_concurrent_callers_of_one_operation_dispatch_exactly_once(self):
        (self.fake / "kill.hook").write_text("sleep 1\n" + gone_hook(self.fake))
        env = self.env(KITTY_PTY_BROKER_SESSION=ME)
        command = ["bash", str(LAUNCHER), "pty", "request", "--yes", "--request-json", "-"]
        procs = [subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(3)]
        results = []
        for process in procs:
            out, err = process.communicate(kill_request(), timeout=60)
            results.append((process.returncode, json.loads(out)))
        self.assertEqual(len(self.kills()), 1, self.calls())
        self.assertEqual([code for code, _ in results], [0, 0, 0], results)
        self.assertEqual({doc["result"] for _, doc in results}, {"verified_absent"})
        self.assertEqual(sorted(doc["duplicate"] for _, doc in results), [False, True, True])

    def test_a_refusal_that_sent_nothing_releases_the_reservation(self):
        refused = self.request(kill_request(expect=STARTED + 5), "--yes")
        self.assertEqual(json.loads(refused.stdout)["reason"], "started_mismatch")
        self.assertEqual(self.records(), [], "an unsent operation leaves nothing reserved")
        self.assertEqual(json.loads(self.request(kill_request(), "--yes").stdout)["result"], "verified_absent")

    def test_the_record_and_its_directory_are_fsynced(self):
        calls = []
        original = os.fsync
        store = module.Store(str(self.state))
        store.open()
        os.fsync = lambda fd: (calls.append(os.fstat(fd).st_mode), original(fd))[1]
        try:
            store.write("x", {"operation_id": "x", "phase": "intent"})
        finally:
            os.fsync = original
        import stat
        self.assertTrue(any(stat.S_ISREG(mode) for mode in calls), "the file")
        self.assertTrue(any(stat.S_ISDIR(mode) for mode in calls), "and the directory")


class StructuredKillTests(C1Case):
    def test_an_omitted_expectation_is_rejected_before_any_broker_call(self):
        result = self.request(kill_request(expect=None), "--yes")
        document = json.loads(result.stdout)
        self.assertEqual((result.returncode, document["result"], document["reason"]), (2, "refused", "missing_field"))
        self.assertIn("expect_started_millis", document["message"])
        self.assertIn("started_millis", document["hint"])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.records(), [])

    def test_capabilities_publish_the_requirement_and_every_numeric_range(self):
        document = json.loads(self.pty("capabilities").stdout)
        verbs = {entry["verb"]: {arg["name"]: arg for arg in entry["args"]} for entry in document["verbs"]}
        expect = verbs["kill"]["expect_started_millis"]
        self.assertIs(expect["required"], True)
        self.assertEqual((expect["min"], expect["max"]), (0, 9223372036854775807))
        for verb in ("status", "pane"):
            self.assertEqual((verbs[verb]["pane_id"]["min"], verbs[verb]["pane_id"]["max"]), (0, 2147483647))
        timeout = document["top_level"]["timeout_seconds"]
        self.assertEqual((timeout["default"], timeout["default_list"]), (2, 1))
        self.assertIn("--lines", document["names"])
        self.assertLessEqual(len(self.pty("capabilities").stdout.encode()), 3900)


class PlainRouteCallerTests(C1Case):
    def kill(self, *flags, **env):
        result = self.pty("kill", TARGET, "--yes", "--expect-started", str(STARTED), "--json", *flags, **env)
        return result, json.loads(result.stdout)

    def test_off_a_terminal_an_unidentified_caller_is_refused(self):
        for value in ("", "../x", "a b"):
            result, receipt = self.kill(KITTY_PTY_BROKER_SESSION=value)
            self.assertEqual((result.returncode, receipt["result"], receipt["reason"]),
                             (3, "refused", "caller_unidentified"), value)
            self.assertFalse(receipt["request_sent"])
            self.assertIn("stop and report", receipt["hint"])
        self.assertEqual(self.calls(), [])
        env = self.env()
        env.pop("KITTY_PTY_BROKER_SESSION")
        unset = subprocess.run(["bash", str(LAUNCHER), "pty", "kill", TARGET, "--yes", "--json"], env=env,
                               capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual((unset.returncode, json.loads(unset.stdout)["reason"]), (3, "caller_unidentified"))
        self.assertEqual(self.calls(), [])

    def test_a_person_can_say_so_explicitly(self):
        result, receipt = self.kill("--no-caller-check", KITTY_PTY_BROKER_SESSION="")
        self.assertEqual((result.returncode, receipt["result"]), (0, "verified_absent"))

    def test_a_person_at_a_terminal_is_asked_not_refused(self):
        code, _, seen = self.pty_tty("kill", TARGET, answer=b"y", KITTY_PTY_BROKER_SESSION="")
        self.assertEqual(code, 0, seen)
        self.assertIn("[y/N]", seen)

    def test_an_identified_caller_is_unaffected(self):
        result, receipt = self.kill()
        self.assertEqual((result.returncode, receipt["result"]), (0, "verified_absent"))


class RefusalHintTests(C1Case):
    def test_result_level_refusals_carry_an_accepted_form(self):
        own = json.loads(self.pty("kill", ME, "--yes", "--json", KITTY_PTY_BROKER_SESSION=ME).stdout)
        self.assertEqual(own["reason"], "own_session")
        self.assertIn("another pane", own["hint"])
        stale = json.loads(self.pty("kill", TARGET, "--yes", "--expect-started", "5", "--json").stdout)
        self.assertEqual(stale["reason"], "started_mismatch")
        self.assertIn(f"kilix pty status {TARGET} --json", stale["hint"])
        broker_says = self.pty("kill", TARGET, "--yes", "--expect-started", str(STARTED), "--json")
        self.assertEqual(json.loads(broker_says.stdout)["result"], "verified_absent")
        self.rearm()
        (self.fake / "kill.hook").unlink()
        self.reply("kill", err="x\n", rc=5)
        bind = json.loads(self.pty("kill", TARGET, "--yes", "--expect-started", str(STARTED), "--json").stdout)
        self.assertEqual(bind["reason"], "cannot_bind")
        self.assertIn(f"kilix pty kill {TARGET} --yes", bind["hint"])
        self.rearm()
        self.reply("kill", err="x\n", rc=3)
        mismatch = json.loads(self.pty("kill", TARGET, "--yes", "--expect-started", str(STARTED), "--json").stdout)
        self.assertEqual(mismatch["reason"], "started_mismatch")
        self.assertIn("status", mismatch["hint"])

    def test_the_declined_prompt_has_a_hint_too(self):
        code, _, seen = self.pty_tty("kill", TARGET, answer=b"n")
        self.assertEqual(code, 3, seen)
        self.assertIn("hint:", seen)

    def test_every_refusal_reason_in_the_request_route_has_a_hint(self):
        for payload, flags in ((kill_request(expect=None), ("--yes",)), (kill_request(), ()),
                               (json.dumps({"schema": SCHEMA, "verb": "nope"}), ())):
            document = json.loads(self.request(payload, *flags).stdout)
            self.assertEqual(document["result"], "refused")
            self.assertTrue(document["hint"], document)


class VerbTypeTests(C1Case):
    def test_a_non_string_verb_is_one_json_refusal_not_a_traceback(self):
        for verb in ("[]", "{}", '["kill"]', "5", "null", "true", '{"a":1}'):
            for schema in (SCHEMA, "wrong"):
                payload = '{"schema":"%s","verb":%s}' % (schema, verb)
                result = self.request(payload)
                self.assertEqual(result.returncode, 2, (payload, result.stderr))
                self.assertNotIn("Traceback", result.stderr)
                document = json.loads(result.stdout)
                self.assertEqual(document["result"], "refused", payload)
                self.assertIn("kilix.pty.request/v1", document["hint"])
        self.assertEqual(self.calls(), [])


class FirstLaunchRaceTests(unittest.TestCase):
    def test_losing_the_race_to_create_the_defaults_link_is_not_an_error(self):
        source = (ROOT / "kilix").read_text()
        functions = source[source.index("_kilix_refresh_managed_link() {"):
                           source.index("_kilix_refresh_generated_include() {")]
        with tempfile.TemporaryDirectory() as scratch:
            scratch = Path(scratch)
            bin_dir = scratch / "bin"
            bin_dir.mkdir()
            target = scratch / "config" / ".kilix-defaults.conf"
            target.parent.mkdir()
            real = subprocess.run(["which", "ln"], capture_output=True, text=True).stdout.strip()
            # The other launcher gets there between the test for the link and the ln.
            (bin_dir / "ln").write_text(
                f'#!/bin/sh\n{real} -s /other/launcher/kitty.conf "{target}"\nexec {real} "$@"\n')
            (bin_dir / "ln").chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", "set -euo pipefail\n" + functions
                 + f'\n_kilix_refresh_managed_link "{target}" "/srv/kilix/config/kitty.conf"\necho done'],
                env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, capture_output=True, text=True, timeout=30)
            self.assertEqual((result.returncode, result.stdout.strip()), (0, "done"), result.stderr)
            self.assertTrue(target.is_symlink())

    def test_an_unrelated_failure_to_create_it_still_fails(self):
        source = (ROOT / "kilix").read_text()
        functions = source[source.index("_kilix_refresh_managed_link() {"):
                           source.index("_kilix_refresh_generated_include() {")]
        with tempfile.TemporaryDirectory() as scratch:
            missing = Path(scratch) / "no-such-directory" / ".kilix-defaults.conf"
            result = subprocess.run(
                ["bash", "-c", "set -euo pipefail\n" + functions
                 + f'\n_kilix_refresh_managed_link "{missing}" "/srv/x"\necho done'],
                env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("done", result.stdout)
            time.sleep(0)


if __name__ == "__main__":
    unittest.main()
