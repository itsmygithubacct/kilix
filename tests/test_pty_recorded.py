"""Recorded spawn facts stay intact in both JSON wrappers and the pinned stack."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pty_cli import PtyCliCase, SESSION, status_json  # noqa: E402
from test_pty_identity import RealBrokerCase  # noqa: E402
from test_pty_a2r7_docs import CONTRACT, agents_section, help_output, normalized, text  # noqa: E402

RECORDED_RULE = (
    "For an unreachable session, recorded shows the command it was started with; if the recorded "
    "command does not match the user's description it is not a match; if it is null or matches, "
    "the session is ambiguous: ask."
)


class RecordedForwardingTests(PtyCliCase):
    def test_list_preserves_recorded_fields_nulls_truncation_and_future_fields(self):
        for recorded in (
            {"argv": ["sh", "-c", "a 'quoted'\ncommand\u0001"], "cwd": "/with spaces", "started_millis": 0,
             "truncated": False, "future": {"value": [1, None]}},
            {"argv": ["prefix"], "cwd": "/shortened", "started_millis": 42, "truncated": True},
            {"argv": None, "cwd": None, "started_millis": None, "truncated": False},
            None,
        ):
            with self.subTest(recorded=recorded):
                row = {"id": SESSION, "reachable": False, "error": "timeout", "recorded": recorded}
                self.reply("list", out=json.dumps([row]))
                result = self.pty("list", "--json")
                self.assertEqual(result.returncode, 0, result.stderr)
                document = json.loads(result.stdout)
                self.assertEqual(document["sessions"], [])
                self.assertEqual(document["unreachable"], [row])

    def test_list_does_not_invent_recorded_for_an_old_broker(self):
        row = {"id": SESSION, "reachable": False, "error": "timeout"}
        self.reply("list", out=json.dumps([row]))
        result = self.pty("list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["unreachable"], [row])

    def test_status_preserves_any_returned_recorded_object_unchanged(self):
        for recorded in ({"argv": ["sh"], "cwd": None, "started_millis": None, "truncated": True}, None):
            row = {"id": SESSION, "reachable": False, "error": "timeout", "recorded": recorded}
            self.reply("status", out=json.dumps(row))
            result = self.pty("status", SESSION, "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["session"], row)

    def test_live_status_does_not_gain_recorded(self):
        row = json.loads(status_json())
        self.reply("status", out=json.dumps(row))
        self.assertEqual(json.loads(self.pty("status", SESSION, "--json").stdout)["session"], row)


class RecordedRealBrokerTests(RealBrokerCase):
    def run_broker(self, *args):
        return subprocess.run([self.broker, "--runtime-dir", str(self.rt), *args], env=self.env(),
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)

    def pty(self, *args):
        return subprocess.run(["bash", str(Path(__file__).resolve().parents[1] / "kilix"), "pty", *args],
                              env=self.env(), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)

    def test_real_pin_list_matches_the_brokers_recorded_object_and_live_status(self):
        cwd = self.root / "space ' \"\n café"
        cwd.mkdir()
        argv = ["/bin/sh", "-c", "exec /bin/sleep 90", "", "sp ace", "'quotes\"\\", "\n\001", "café"]
        started = subprocess.run([self.broker, "--runtime-dir", str(self.rt), "run", "--id", "target", "--", *argv],
                                 env=self.env(), cwd=cwd, capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL, timeout=30)
        self.assertEqual(started.returncode, 0, started.stderr)
        live = json.loads(self.run_broker("status", "target", "--json").stdout)
        self.pids.extend((live["broker_pid"], live["child_pid"]))
        document = json.loads(self.pty("status", "target", "--json").stdout)
        self.assertEqual(document["session"], live)
        self.assertNotIn("recorded", document["session"])
        other = self.spawn("healthy")
        os.kill(live["broker_pid"], signal.SIGSTOP)
        raw = self.run_broker("--timeout", "0.1", "list", "--json", "--all")
        raw_rows = json.loads(raw.stdout)
        row = next(row for row in raw_rows if row["id"] == "target")
        self.assertEqual(row["recorded"], {"argv": argv, "cwd": str(cwd),
                                         "started_millis": live["started_millis"], "truncated": False})
        result = self.pty("--timeout", "0.1", "list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["unreachable"], [row])
        self.assertEqual(document["sessions"], [next(row for row in raw_rows if row["id"] == other["id"])])
        status = self.pty("--timeout", "0.1", "status", "target", "--json")
        self.assertNotEqual(status.returncode, 0)
        self.assertEqual(status.stdout, "")
        self.assertIn("timed out", status.stderr)
        os.kill(live["broker_pid"], signal.SIGCONT)
        self.assertEqual(json.loads(self.pty("status", "target", "--json").stdout)["session"], live)

    def test_real_pin_old_metadata_produces_nulls_without_losing_its_timestamp(self):
        live = self.spawn()
        metadata = self.rt / "sessions" / "target" / "metadata"
        metadata.write_text("\n".join(line for line in metadata.read_text().splitlines()
                                     if not line.startswith(("argv_json=", "cwd_json=", "argv_truncated="))) + "\n")
        os.kill(live["broker_pid"], signal.SIGSTOP)
        result = self.pty("--timeout", "0.1", "list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["unreachable"][0]["recorded"],
                         {"argv": None, "cwd": None, "started_millis": live["started_millis"], "truncated": False})

    def test_real_pin_truncation_is_preserved(self):
        argv = ["/bin/sh", "-c", "exec /bin/sleep 90", "x" * 1500, "tail"]
        started = self.run_broker("run", "--id", "target", "--", *argv)
        self.assertEqual(started.returncode, 0, started.stderr)
        live = json.loads(self.run_broker("status", "target", "--json").stdout)
        self.pids.extend((live["broker_pid"], live["child_pid"]))
        os.kill(live["broker_pid"], signal.SIGSTOP)
        raw_row = json.loads(self.run_broker("--timeout", "0.1", "list", "--json", "--all").stdout)[0]
        self.assertEqual(raw_row["recorded"]["argv"], argv[:3])
        self.assertTrue(raw_row["recorded"]["truncated"])
        self.assertEqual(json.loads(self.pty("--timeout", "0.1", "list", "--json").stdout)["unreachable"], [raw_row])


class RecordedDocsTests(unittest.TestCase):
    def test_every_agent_entry_states_the_recorded_matching_rule(self):
        surfaces = {"AGENTS": agents_section(), "skill": text("skills", "kilix-pty", "SKILL.md"),
                    "README": text("README.md"), "persistence": text("docs", "help", "operations", "persistence.md"),
                    "help": help_output()}
        if CONTRACT:
            surfaces["CONTRACT"] = Path(CONTRACT).read_text()
        for name, surface in surfaces.items():
            with self.subTest(surface=name):
                self.assertIn(RECORDED_RULE, normalized(surface))

    def test_byte_budgets_and_r2_extraction_remain_valid(self):
        self.assertLessEqual(len(("## Persistent pane sessions\n" + agents_section()).encode()), 1200)
        self.assertLessEqual(len(text("skills", "kilix-pty", "SKILL.md").encode()), 3000)


if __name__ == "__main__":
    unittest.main()
