"""Regression tests for the phase C round-2 review (reviews/C-kilix-r2/REVIEW.md).

P1: capacity pruning must not evict an unresolved intent, a live owner's lock or a sent-uncertain
receipt; if no safe slot is left, new work is refused before dispatch. P2: a storage failure after
dispatch is a bounded JSON receipt and keeps the safe intent. Each fails on aa3c9265.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_c1_fixes import C1Case, ME, STARTED, TARGET, kill_request  # noqa: E402
from test_pty_cli import LAUNCHER, gone_hook  # noqa: E402
import kilix_pty_request as module  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DAY = 24 * 3600


class StoreCase(C1Case):
    def directory(self):
        path = self.state / "pty-operations"
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
        return path

    def name(self, operation_id):
        return "op-" + hashlib.sha256(operation_id.encode()).hexdigest()

    def put(self, operation_id, phase="done", result="verified_absent", age=0, lock=False):
        """A record as the store writes it, `age` seconds old; optionally its lock file too."""
        receipt = None if phase == "intent" else {"result": result, "request_sent": True, "id": TARGET}
        path = self.directory() / (self.name(operation_id) + ".json")
        path.write_text(json.dumps({"fingerprint": "x", "phase": phase, "operation_id": operation_id,
                                    "receipt": receipt}))
        old = time.time() - age
        os.utime(path, (old, old))
        if lock:
            (self.directory() / (self.name(operation_id) + ".lock")).touch(mode=0o600)
        return path

    def fill(self, count, prefix="filler", **kwargs):
        for index in range(count):
            self.put(f"{prefix}-{index}", **kwargs)

    def names(self):
        return {p.name for p in self.directory().iterdir()}

    def run_kill_request(self, operation_id, **env):
        return self.request(kill_request(operation_id), "--yes", **env)


class CapacityTests(StoreCase):
    def test_a_full_store_prunes_finished_receipts_oldest_first_and_admits_the_new_operation(self):
        self.fill(module.STORE_LIMIT, age=3600)
        oldest = self.put("oldest", age=10 * DAY)
        self.assertEqual(len(list(self.directory().glob("op-*.json"))), module.STORE_LIMIT + 1)
        receipt = json.loads(self.run_kill_request("new-op").stdout)
        self.assertEqual(receipt["result"], "verified_absent", receipt)
        self.assertFalse(oldest.exists(), "the oldest finished receipt made room")
        self.assertLessEqual(len(list(self.directory().glob("op-*.json"))), module.STORE_LIMIT)

    def test_an_unresolved_intent_with_no_owner_is_never_pruned_and_replays_interrupted(self):
        stuck = self.put("stuck", phase="intent", age=400 * DAY, lock=True)
        self.fill(module.STORE_LIMIT, age=3600)
        self.assertEqual(json.loads(self.run_kill_request("new-op").stdout)["result"], "verified_absent")
        self.assertTrue(stuck.exists(), "an unresolved intent must survive any amount of capacity pressure")
        self.assertTrue(stuck.with_suffix(".lock").exists())
        self.assertEqual(self.kills(), [f"--runtime-dir {self.runtime} kill {TARGET} --expect-started {STARTED}"])

    def test_a_recent_uncertain_receipt_is_kept_and_still_guards_against_a_second_dispatch(self):
        kept = self.put("unsure", result="uncertain", age=2 * DAY)
        fingerprint = module.fingerprint({"verb": "kill", "args": {"id": TARGET, "expect_started_millis": STARTED}})
        record = json.loads(kept.read_text())
        record["fingerprint"] = fingerprint
        record["receipt"].update(result="uncertain", reason="terminate_timed_out", operation_id="unsure",
                                 schema="kilix.pty/v1", runtime=str(self.runtime), timeout_seconds=2.0)
        kept.write_text(json.dumps(record))
        os.utime(kept, (time.time() - 2 * DAY,) * 2)
        self.fill(module.STORE_LIMIT, age=3600)
        self.assertEqual(json.loads(self.run_kill_request("new-op").stdout)["result"], "verified_absent")
        self.assertTrue(kept.exists())
        (self.fake / "calls").unlink()
        replay = self.request(kill_request("unsure"), "--yes")
        document = json.loads(replay.stdout)
        self.assertEqual((replay.returncode, document["result"], document["duplicate"]), (1, "uncertain", True))
        self.assertEqual(self.kills(), [], "an operation whose effect is unknown is never re-sent")

    def test_an_uncertain_receipt_older_than_the_horizon_may_make_room(self):
        old = self.put("ancient", result="uncertain", age=module.UNCERTAIN_TTL + DAY)
        self.fill(module.STORE_LIMIT - 1, phase="intent", age=60)       # the rest cannot be pruned
        self.assertEqual(json.loads(self.run_kill_request("new-op").stdout)["result"], "verified_absent")
        self.assertFalse(old.exists())

    def test_with_no_safe_slot_new_work_is_refused_before_anything_is_sent(self):
        self.fill(module.STORE_LIMIT, phase="intent", age=60)
        result = self.run_kill_request("one-too-many")
        document = json.loads(result.stdout)
        self.assertEqual((result.returncode, document["result"], document["reason"]), (3, "refused", "store_full"))
        self.assertTrue(document["hint"])
        self.assertEqual(self.calls(), [], "nothing is dispatched, nothing is even looked up")
        self.assertEqual(len(list(self.directory().glob("op-*.json"))), module.STORE_LIMIT)
        self.assertFalse((self.directory() / (self.name("one-too-many") + ".json")).exists())

    def test_the_same_pressure_does_not_lose_an_uncertain_receipt_either(self):
        self.fill(module.STORE_LIMIT, result="uncertain", age=3600)      # sent-uncertain, young
        document = json.loads(self.run_kill_request("new-op").stdout)
        self.assertEqual(document["reason"], "store_full")
        self.assertEqual(len(list(self.directory().glob("op-*.json"))), module.STORE_LIMIT)

    def test_unowned_lock_files_without_a_record_are_swept_but_a_held_one_is_not(self):
        orphan = self.directory() / "op-orphan.lock"
        held = self.directory() / "op-held.lock"
        orphan.touch(mode=0o600)
        held.touch(mode=0o600)
        with held.open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self.assertEqual(json.loads(self.run_kill_request("new-op").stdout)["result"], "verified_absent")
        self.assertFalse(orphan.exists())
        self.assertTrue(held.exists())

    def test_a_finished_record_whose_lock_is_still_held_is_left_alone(self):
        # The dispatcher has written the receipt but not yet let go of its lock: the pair stays together.
        held = self.put("busy", age=30 * DAY, lock=True)
        self.fill(module.STORE_LIMIT - 1, age=3600)
        with held.with_suffix(".lock").open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self.assertEqual(json.loads(self.run_kill_request("new-op").stdout)["result"], "verified_absent")
        self.assertTrue(held.exists() and held.with_suffix(".lock").exists())
        self.assertLessEqual(len(list(self.directory().glob("op-*.json"))), module.STORE_LIMIT)

    def test_a_damaged_record_is_a_bounded_refusal_not_a_traceback(self):
        (self.directory() / (self.name("damaged") + ".json")).write_text("not json {")
        result = self.run_kill_request("damaged")
        self.assertNotIn("Traceback", result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual((result.returncode, document["reason"]), (3, "store_unavailable"))
        self.assertEqual(self.calls(), [])


class LiveOwnerUnderPressureTests(StoreCase):
    def test_a_live_dispatch_survives_a_full_store_and_a_retry_does_not_send_twice(self):
        (self.fake / "kill.hook").write_text("sleep 3\n" + gone_hook(self.fake))
        env = self.env(KITTY_PTY_BROKER_SESSION=ME)
        command = ["bash", str(LAUNCHER), "pty", "request", "--yes", "--request-json", "-"]
        first = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        first.stdin.write(kill_request("slow"))
        first.stdin.close()
        try:
            deadline = time.monotonic() + 30
            while not self.kills() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(len(self.kills()), 1, "the first dispatch is under way")
            intent = self.directory() / (self.name("slow") + ".json")
            self.assertEqual(json.loads(intent.read_text())["phase"], "intent")
            self.fill(module.STORE_LIMIT, age=-60)           # newer than everything: the worst case for pruning
            retry = subprocess.run(command, env=env, input=kill_request("slow"), capture_output=True,
                                   text=True, timeout=60)
            first_out = first.stdout.read()
            first.wait(timeout=60)
        finally:
            if first.poll() is None:
                first.kill()
                first.wait()
            first.stdout.close()
            first.stderr.close()
        self.assertEqual(len(self.kills()), 1, self.calls())
        self.assertEqual(json.loads(first_out)["duplicate"], False)
        self.assertEqual((retry.returncode, json.loads(retry.stdout)["duplicate"]), (0, True))


class FinalReceiptFailureTests(StoreCase):
    def program(self, body):
        return textwrap.dedent(f'''
            import errno, os, stat, sys
            sys.path[:0] = [{str(ROOT / "config")!r}]
            import kilix_pty_request as m, kilix_pty as p
            {textwrap.indent(textwrap.dedent(body), "            ").strip()}
            broker = p.Broker({str(self.broker)!r}, {str(self.runtime)!r}, None, 10)
            raise SystemExit(m.cmd_request(broker, ["--yes", "--request-json", "-"]))
        ''')

    def run_program(self, body, payload=None):
        return subprocess.run([sys.executable, "-B", "-c", self.program(body)], input=payload or kill_request("full-1"),
                              env=self.env(KILIX_STATE_DIRECTORY=str(self.state)), capture_output=True, text=True,
                              timeout=60)

    FSYNC_FAULT = """
        original = os.fsync
        count = 0
        def full(fd):
            global count
            if stat.S_ISREG(os.fstat(fd).st_mode):
                count += 1
                if count == 2:
                    raise OSError(errno.ENOSPC, "injected: no space left on device")
            return original(fd)
        os.fsync = full
    """

    def test_a_full_disk_while_saving_the_receipt_is_a_bounded_uncertain_json_not_a_traceback(self):
        result = self.run_program(self.FSYNC_FAULT)
        self.assertNotIn("Traceback", result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual((result.returncode, document["result"], document["reason"], document["request_sent"]),
                         (1, "uncertain", "receipt_not_saved", True))
        self.assertEqual(document["observed_result"], "verified_absent")
        self.assertIn("replays as interrupted", document["message"])
        self.assertEqual(len(self.kills()), 1)
        names = self.names()
        self.assertEqual(sorted(n for n in names if n.endswith(".tmp")), [], "no half-written file is left")
        record = json.loads((self.directory() / (self.name("full-1") + ".json")).read_text())
        self.assertEqual(record["phase"], "intent", "the safe intent is kept")
        # The owner lock was released, and a retry replays interrupted without sending again.
        with (self.directory() / (self.name("full-1") + ".lock")).open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (self.fake / "calls").unlink()
        retry = self.request(kill_request("full-1"), "--yes")
        self.assertEqual((retry.returncode, json.loads(retry.stdout)["reason"]), (1, "interrupted"))
        self.assertEqual(self.kills(), [])

    def test_any_failure_after_the_dispatch_has_the_same_shape(self):
        result = self.run_program("""
            def broken(broker, request, state):
                raise RuntimeError("boom inside the dispatch")
            m.run_kill = broken
        """)
        self.assertNotIn("Traceback", result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual((result.returncode, document["reason"], document["request_sent"]), (1, "dispatch_failed", True))
        retry = self.request(kill_request("full-1"), "--yes")
        self.assertEqual(json.loads(retry.stdout)["reason"], "interrupted")

    def test_a_failure_before_the_dispatch_is_still_a_clean_unsent_refusal(self):
        result = self.run_program("""
            def full(fd):
                raise OSError(errno.ENOSPC, "injected")
            os.fsync = full
        """)
        document = json.loads(result.stdout)
        self.assertEqual((result.returncode, document["reason"]), (3, "store_unavailable"))
        self.assertEqual(self.kills(), [])


class DocumentationTests(unittest.TestCase):
    def test_the_retention_rule_and_the_flag_wording_are_stated(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("never pruned", readme)
        self.assertIn("store_full", readme)
        self.assertIn("30 days", readme)
        self.assertIn("identifiable own session is still refused", readme)


if __name__ == "__main__":
    unittest.main()
