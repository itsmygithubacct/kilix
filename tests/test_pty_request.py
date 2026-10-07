"""`kilix pty capabilities` and `kilix pty request`, through the real launcher and a fake broker."""

import json
import os
from pathlib import Path
import stat
import sys
import subprocess
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_cli import (LAUNCHER, SESSION, PtyCliCase, status_json,  # noqa: E402
                          reaped_pair)
import kilix_pty_request as module  # noqa: E402

SCHEMA = "kilix.pty.request/v1"
OTHER = "bbbbbbbbbbbbbbbb"
ME = "aaaaaaaaaaaaaaaa"


def request(verb, args=None, **top):
    document = {"schema": SCHEMA, "verb": verb, **top}
    if args is not None:
        document["args"] = args
    return json.dumps(document)


class RequestCase(PtyCliCase):
    def req(self, payload, *flags, source="-", stdin=True, **env):
        env.setdefault("KITTY_PTY_BROKER_SESSION", ME)
        command = ["bash", str(LAUNCHER), "pty", "request", *flags, "--request-json", source]
        return subprocess.run(command, env=self.env(**env), capture_output=True, text=True,
                              timeout=60, input=payload if stdin else None,
                              stdin=None if stdin else subprocess.DEVNULL)

    def ok(self, result):
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        return json.loads(lines[0])

    def kills(self):
        return [call for call in self.calls() if " kill " in call]

    def vanishes(self):
        (self.fake / "kill.hook").write_text(f'echo "[]" > "{self.fake}/list.out"\n')
        self.reply("list", out=json.dumps([json.loads(status_json(OTHER))]))
        self.reply("status", out=status_json(OTHER) + "\n")

    def store(self):
        return self.state / "pty-operations"


class CapabilitiesTests(RequestCase):
    def test_the_document_is_small_and_names_every_verb_with_an_example(self):
        result = self.pty("capabilities", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("\n"), 1)
        document = json.loads(result.stdout)
        self.assertEqual(document["schema"], "kilix.pty/v1")
        self.assertEqual(document["request_schema"], SCHEMA)
        self.assertLessEqual(len(result.stdout.encode()), 3200)
        verbs = {entry["verb"]: entry for entry in document["verbs"]}
        self.assertEqual(set(verbs), {"list", "status", "pane", "reaped", "journals", "observe", "kill"})
        self.assertEqual(document["not_available"], ["attach", "reap"])
        self.assertEqual(verbs["kill"]["consent"], "--yes on the command line")
        self.assertEqual(verbs["kill"]["operation_id"], "required")
        self.assertNotIn("consent", verbs["observe"])
        arguments = {arg["name"]: arg for arg in verbs["observe"]["args"]}
        self.assertEqual((arguments["max_lines"]["unit"], arguments["max_lines"]["min"],
                          arguments["max_lines"]["max"]), ("lines", 1, 10000))
        self.assertEqual(arguments["max_bytes"]["unit"], "bytes")
        self.assertEqual(document["top_level"]["timeout_seconds"]["unit"], "seconds")
        self.assertEqual(self.calls(), [], "capabilities must not ask a broker anything")

    def test_every_example_is_a_request_that_validates(self):
        document = json.loads(self.pty("capabilities").stdout)
        for entry in document["verbs"]:
            raw = json.dumps(entry["example"]).encode()
            self.assertEqual(module.validate(raw)["verb"], entry["verb"])

    def test_the_read_examples_run_and_answer_in_one_line(self):
        self.reply("list", out="[]")
        self.reply("status", out=status_json() + "\n")
        self.reply("reaped", out="[]")
        self.reply("observe", out="hello\n", err="kitty-pty-broker: cursor=1:6\n")
        for verb in ("list", "status", "reaped", "observe"):
            example = module.EXAMPLES[verb]
            document = self.ok(self.req(json.dumps(example)))
            self.assertEqual(document["schema"], "kilix.pty/v1", verb)


class ReadTests(RequestCase):
    def same(self, verb, args, plain):
        via_request = self.ok(self.req(request(verb, args)))
        direct = json.loads(self.pty(*plain).stdout)
        self.assertEqual(via_request, direct, verb)
        return via_request

    def test_reads_are_the_documents_the_plain_commands_print(self):
        self.reply("list", out="[%s]" % status_json())
        self.reply("status", out=status_json() + "\n")
        self.reply("reaped", out="[]")
        self.reply("observe", out="a\nb\nc\n", err="kitty-pty-broker: cursor=2:6\n")
        self.same("list", None, ["list", "--json"])
        self.same("status", {"id": SESSION}, ["status", SESSION, "--json"])
        self.same("reaped", None, ["reaped", "--json"])
        self.same("observe", {"id": SESSION, "max_lines": 2},
                  ["observe", SESSION, "--once", "--json", "--lines", "2"])
        self.same("journals", None, ["journals", "--json"])

    def test_reads_need_no_operation_id_and_ignore_one(self):
        self.reply("list", out="[]")
        self.assertEqual(self.ok(self.req(request("list", operation_id="whatever")))["sessions"], [])
        self.assertFalse(self.store().exists())

    def test_timeout_seconds_reaches_the_broker(self):
        self.reply("list", out="[]")
        document = self.ok(self.req(request("list", timeout_seconds=3.5)))
        self.assertEqual(document["timeout_seconds"], 3.5)
        self.assertEqual(self.calls(), [f"--runtime-dir {self.runtime} --timeout 3.5 list --json --all"])

    def test_journals_show_through_a_request(self):
        reaped_pair(self.runtime, SESSION, 1700000000000, b"one\ntwo\nthree\n")
        self.assertEqual(self.pty("reap").returncode, 0)
        document = self.ok(self.req(request("journals", {"id": SESSION, "max_lines": 1, "text": True})))
        self.assertEqual(document["text"].strip(), "three")
        self.assertTrue(document["untrusted"])


class RefusalTests(RequestCase):
    def refused(self, payload, reason, code=2, *flags):
        result = self.req(payload, *flags)
        document = self.ok(result)
        self.assertEqual((result.returncode, document["result"], document["reason"]), (code, "refused", reason), document)
        self.assertTrue(document["hint"])
        self.assertEqual(self.calls(), [], "a refused request must not reach the broker")
        return document

    def test_unknown_fields_are_rejected_at_both_levels(self):
        self.refused(request("list", bogus=1), "unknown_field")
        document = self.refused(request("status", {"id": SESSION, "color": "red"}), "unknown_field")
        self.assertIn("color", document["message"])
        self.refused(request("kill", {"id": OTHER, "force": True}, operation_id="x"), "unknown_field")
        self.refused('{"schema":"kilix.pty.request/v1","verb":"list","verb":"status"}', "not_json")
        self.refused(request("frobnicate"), "unknown_verb")
        self.refused(json.dumps({"verb": "list"}), "bad_schema")

    def test_the_size_cap_is_16_kib(self):
        padded = request("list") + " " * (module.MAX_REQUEST - len(request("list")))
        self.assertEqual(len(padded), 16384)
        self.reply("list", out="[]")
        self.assertEqual(self.ok(self.req(padded))["sessions"], [])
        (self.fake / "calls").unlink()
        document = self.refused(padded + " ", "request_too_large")
        self.assertIn("16 KiB", document["message"])

    def test_empty_stdin_is_one_bounded_line_with_the_heredoc_example(self):
        for payload, kwargs in (("", {}), ("  \n", {}), (None, {"stdin": False})):
            result = self.req(payload, **kwargs)
            document = self.ok(result)
            self.assertEqual((result.returncode, document["reason"]), (2, "empty_request"))
            self.assertIn("<<'EOF'", document["hint"])
            self.assertIn(SCHEMA, document["hint"])
            self.assertLess(len(result.stdout.encode()), 700)
        self.assertEqual(self.calls(), [])

    def test_unit_errors_name_the_unit_and_the_range(self):
        document = self.refused(request("list", timeout_seconds=30000), "out_of_range")
        self.assertIn("seconds, 0.1-60", document["message"])
        self.assertIn("30000", document["message"])
        document = self.refused(request("observe", {"id": SESSION, "max_lines": 30000}), "out_of_range")
        self.assertIn("max_lines is in lines, 1-10000", document["message"])
        document = self.refused(request("observe", {"id": SESSION, "max_bytes": 0}), "out_of_range")
        self.assertIn("max_bytes is in bytes, 1-1048576", document["message"])
        self.refused(request("list", timeout_seconds=0.05), "out_of_range")
        self.refused(request("list", timeout_seconds=True), "bad_type")
        self.refused(request("observe", {"id": SESSION, "max_lines": "5"}), "bad_type")
        self.refused(request("observe", {"id": SESSION, "max_lines": 5, "max_bytes": 5}), "conflicting_bounds")
        self.refused(request("kill", {"id": OTHER, "expect_started_millis": -1}, operation_id="x"), "out_of_range")

    def test_identity_must_be_a_full_id(self):
        for bad in ("../x", "a b", "x" * 65, "", 5):
            self.refused(request("status", {"id": bad}), "bad_id")
        self.refused(request("status", {}), "missing_field")
        self.refused(request("status", {"id": SESSION, "pane_id": 3}), "missing_field")

    def test_a_request_file_is_read_like_stdin(self):
        self.reply("list", out="[]")
        path = self.tmp / "request.json"
        path.write_text(request("list"))
        self.assertEqual(self.ok(self.req(None, source=str(path), stdin=False))["sessions"], [])
        for source in (str(self.tmp), str(self.tmp / "missing")):
            document = self.ok(self.req(None, source=source, stdin=False))
            self.assertEqual(document["result"], "refused", source)


class KillTests(RequestCase):
    def kill(self, args=None, operation_id="end-1", *flags, **env):
        args = {"id": OTHER} if args is None else args
        result = self.req(request("kill", args, operation_id=operation_id), *flags, **env)
        return result.returncode, self.ok(result)

    def test_kill_needs_yes_on_the_command_line_and_the_refusal_is_not_remembered(self):
        self.vanishes()
        code, document = self.kill()
        self.assertEqual((code, document["reason"]), (3, "consent_required"))
        self.assertIn("--yes", document["hint"])
        self.assertEqual(self.kills(), [])
        self.assertFalse(self.store().exists())
        code, document = self.kill(None, "end-1", "--yes")
        self.assertEqual((code, document["result"]), (0, "verified_absent"))

    def test_kill_needs_an_operation_id(self):
        payload = json.dumps({"schema": SCHEMA, "verb": "kill", "args": {"id": OTHER}})
        document = self.ok(self.req(payload, "--yes"))
        self.assertEqual(document["reason"], "operation_id_required")
        self.assertEqual(self.calls(), [])

    def test_kill_fails_closed_when_the_caller_cannot_be_identified(self):
        self.vanishes()
        result = subprocess.run(
            ["bash", str(LAUNCHER), "pty", "request", "--yes", "--request-json", "-"],
            env=self.env(), capture_output=True, text=True, timeout=60,
            input=request("kill", {"id": OTHER}, operation_id="end-1"))
        document = self.ok(result)
        self.assertEqual((result.returncode, document["reason"]), (3, "caller_unidentified"))
        self.assertEqual(self.kills(), [])

    def test_the_callers_own_session_is_refused(self):
        self.vanishes()
        code, document = self.kill({"id": ME}, "end-1", "--yes")
        self.assertEqual((code, document["result"], document["reason"]), (3, "refused", "own_session"))
        self.assertEqual(self.kills(), [])

    def test_a_kill_returns_the_plain_receipt_plus_its_operation(self):
        self.vanishes()
        code, document = self.kill({"id": OTHER, "expect_started_millis": 1700000000000}, "end-1", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual((document["result"], document["id"], document["request_sent"]),
                         ("verified_absent", OTHER, True))
        self.assertEqual((document["operation_id"], document["duplicate"]), ("end-1", False))
        self.assertEqual(document["schema"], "kilix.pty/v1")
        self.assertEqual(len(self.kills()), 1)

    def test_the_same_operation_with_the_same_arguments_returns_the_stored_receipt(self):
        self.vanishes()
        _, first = self.kill(None, "end-1", "--yes")
        (self.fake / "calls").unlink()
        code, again = self.kill(None, "end-1", "--yes")
        self.assertEqual(code, 0)
        self.assertTrue(again["duplicate"])
        self.assertEqual({**again, "duplicate": False}, first)
        self.assertEqual(self.calls(), [], "a duplicate must not reach the broker")

    def test_the_same_operation_with_other_arguments_is_refused(self):
        self.vanishes()
        self.kill(None, "end-1", "--yes")
        (self.fake / "calls").unlink()
        code, document = self.kill({"id": OTHER, "expect_started_millis": 1700000000000}, "end-1", "--yes")
        self.assertEqual((code, document["reason"]), (3, "operation_id_reused"))
        self.assertEqual(self.calls(), [])
        code, document = self.kill({"id": "cccccccccccccccc"}, "end-1", "--yes")
        self.assertEqual((code, document["reason"]), (3, "operation_id_reused"))

    def test_an_uncertain_kill_is_remembered_and_never_resent(self):
        self.reply("list", out=json.dumps([json.loads(status_json(OTHER))]))
        self.reply("status", out=status_json(OTHER) + "\n")
        code, document = self.kill(None, "end-1", "--yes")
        self.assertEqual((code, document["result"]), (1, "uncertain"))
        (self.fake / "calls").unlink()
        code, again = self.kill(None, "end-1", "--yes")
        self.assertEqual((code, again["result"], again["duplicate"]), (1, "uncertain", True))
        self.assertEqual(self.calls(), [])

    def test_a_refusal_that_sent_nothing_can_be_retried_under_the_same_id(self):
        self.vanishes()
        code, document = self.kill({"id": OTHER, "expect_started_millis": 5}, "end-1", "--yes")
        self.assertEqual((code, document["reason"]), (3, "started_mismatch"))
        self.assertFalse(self.store().exists() and list(self.store().glob("op-*.json")))
        code, document = self.kill({"id": OTHER, "expect_started_millis": 1700000000000}, "end-1", "--yes")
        self.assertEqual((code, document["result"]), (0, "verified_absent"))

    def test_an_operation_that_began_and_never_finished_is_uncertain(self):
        self.vanishes()
        self.kill(None, "end-1", "--yes")
        record = next(self.store().glob("op-*.json"))
        saved = json.loads(record.read_text())
        saved.update(phase="intent", receipt=None)
        record.write_text(json.dumps(saved))
        code, document = self.kill(None, "end-1", "--yes")
        self.assertEqual((code, document["result"], document["reason"], document["duplicate"]),
                         (1, "uncertain", "interrupted", True))

    def test_the_store_is_private_and_bounded(self):
        self.vanishes()
        self.kill(None, "end-1", "--yes")
        directory = self.store()
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        record = next(directory.glob("op-*.json"))
        self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
        for index in range(module.STORE_LIMIT + 5):
            stale = directory / f"op-{index:064d}.json"
            stale.write_text("{}")
            os.utime(stale, (1000 + index, 1000 + index))
        self.kill(None, "end-2", "--yes")
        remaining = sorted(directory.glob("op-*.json"))
        self.assertEqual(len(remaining), module.STORE_LIMIT)
        self.assertFalse((directory / f"op-{0:064d}.json").exists(), "the oldest goes first")


class StaticTests(unittest.TestCase):
    def test_attach_and_reap_are_not_request_verbs(self):
        self.assertFalse({"attach", "reap"} & set(module.VERBS))

    def test_the_examples_cover_every_verb(self):
        self.assertEqual(set(module.EXAMPLES), set(module.VERBS))


if __name__ == "__main__":
    unittest.main()
