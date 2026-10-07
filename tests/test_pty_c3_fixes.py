"""Regression tests for the phase C round-3 review (reviews/C-kilix-r3/REVIEW.md).

F1: a caller that saw an operation in progress never dispatches it itself, and its claim to the receipt
survives capacity pruning from its first look to its last. F2: a directory-fsync failure after the record
was replaced is reported as what it is (the real receipt, not durable), not as an `interrupted` replay
that will not happen. Each fails on 1dce58c0.
"""
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import signal
import stat
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_c1_fixes import ME, TARGET, kill_request  # noqa: E402
from test_pty_c2_fixes import StoreCase  # noqa: E402
import kilix_pty as pty  # noqa: E402
import kilix_pty_request as module  # noqa: E402


class ForkedCase(StoreCase):
    """Run the real `execute()` in this process or a forked one, with the dispatch replaced."""

    def payload(self, operation_id):
        return module.validate(kill_request(operation_id).encode())

    def direct(self, operation_id, dispatch):
        broker = pty.Broker(str(self.broker), str(self.runtime), None, 10)
        with mock.patch.dict(os.environ, self.env(KITTY_PTY_BROKER_SESSION=ME)), \
                mock.patch.object(module, "run_kill", dispatch):
            return module.execute(broker, self.payload(operation_id), True, str(self.state))

    def finished(self, calls):
        def dispatch(broker, request, state):
            calls.append(request["operation_id"])
            return 0, broker.envelope(result="verified_absent", request_sent=True, id=TARGET)
        return dispatch

    def wait_file(self, path):
        deadline = time.monotonic() + 10
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(path.exists(), str(path))

    def reap(self, *children):
        for child in children:
            if child is not None:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(child, 0)

    def race(self, pause, age_after_finish=0):
        """An owner dispatches `race`; a waiter pauses at `pause`; the owner finishes; the store is
        pressed by an admission with 255 unresolved intents; the waiter resumes.

        Returns (record_survived_admission, admission outcome, waiter outcome, dispatching pids).
        """
        entered, release, ready, resume = (self.tmp / name for name in ("entered", "release", "ready", "resume"))
        marker = self.tmp / "dispatches"
        store = module.Store(str(self.state))
        self.fill(module.STORE_LIMIT - 1, phase="intent")

        def dispatch(broker, request, state):
            with marker.open("a") as stream:
                stream.write(str(os.getpid()) + "\n")
            if not entered.exists():
                entered.touch()
                self.wait_file(release)
            return 0, broker.envelope(result="verified_absent", request_sent=True, id=TARGET)

        owner = os.fork()
        if owner == 0:
            self.direct("race", dispatch)
            os._exit(0)
        waiter = None
        try:
            self.wait_file(entered)
            waiter = os.fork()
            if waiter == 0:
                pause(ready, resume)
                result = self.direct("race", dispatch)
                (self.tmp / "waiter-result").write_text(json.dumps(result))
                os._exit(0)
            if pause is pause_before_flock:
                self.wait_file(ready)                  # the waiter has opened the lock, not yet locked it
                release.touch()
            else:
                release.touch()                        # the waiter can only hold the lock once the owner drops it
                self.wait_file(ready)
            _, status = os.waitpid(owner, 0)
            owner = None
            self.assertEqual(status, 0)
            path = Path(store.path("race"))
            if age_after_finish:
                old = time.time() - age_after_finish
                os.utime(path, (old, old))
            try:
                admission = self.direct("admission", self.finished([]))
            except module.Refusal as refusal:
                admission = ["refused", refusal.reason]
            survived = path.exists()
            resume.touch()
            _, status = os.waitpid(waiter, 0)
            waiter = None
            self.assertEqual(status, 0)
            outcome = json.loads((self.tmp / "waiter-result").read_text())
            return survived, admission, outcome, marker.read_text().splitlines()
        finally:
            release.touch()
            resume.touch()
            self.reap(owner, waiter)


def pause_before_flock(ready, resume):
    """In the waiter: stop right after it has opened its owner lock, before it locks it."""
    original = module.Store.owner_lock
    opens = 0

    def pausing(self, operation_id):
        nonlocal opens
        descriptor = original(self, operation_id)
        opens += 1
        if opens == 2:                                   # the probe was the first, the waiting lock the second
            ready.write_text("open")
            deadline = time.monotonic() + 10
            while not resume.exists() and time.monotonic() < deadline:
                time.sleep(0.005)
        return descriptor
    module.Store.owner_lock = pausing


def pause_before_reread(ready, resume):
    """In the waiter: stop once it holds the lock the owner released, before it re-reads the record."""
    original = module.Store.locked
    entries = 0
    holds = ready.parent / "waiter-holds"

    def pausing(self):
        nonlocal entries
        entries += 1
        if entries == 2:                                 # the first look is the first entry; the re-read the second
            holds.write_text("held")
            ready.write_text("holding")
            deadline = time.monotonic() + 10
            while not resume.exists() and time.monotonic() < deadline:
                time.sleep(0.005)
        return original(self)
    module.Store.locked = pausing


class WaiterTests(ForkedCase):
    def test_f1_the_reviewers_open_waiter_keeps_its_receipt_and_nothing_dispatches_twice(self):
        survived, admission, outcome, dispatchers = self.race(pause_before_flock)
        self.assertTrue(survived, "a receipt a waiter is about to read was pruned")
        self.assertEqual(admission, ["refused", "store_full"], "the finished receipt is too young to make room")
        self.assertEqual(len(dispatchers), 1)
        self.assertTrue(outcome[1]["duplicate"])
        self.assertEqual(outcome[1]["result"], "verified_absent")

    def test_f1_a_receipt_forgotten_under_a_waiter_is_never_dispatched_again(self):
        # Even if the receipt is old enough to be pruned while the waiter is stalled before its lock, the
        # waiter saw the operation, so it reports the loss instead of sending the request again.
        survived, admission, outcome, dispatchers = self.race(
            pause_before_flock, age_after_finish=module.RECENT_RECEIPT + 60)
        self.assertFalse(survived, "the old receipt made room for the new operation")
        self.assertEqual(admission[0], 0)
        self.assertEqual(len(dispatchers), 1, "the waiter dispatched an operation it had already seen")
        status, receipt = outcome
        self.assertEqual((status, receipt["result"], receipt["reason"]), (1, "uncertain", "receipt_evicted"))
        self.assertIsNone(receipt["request_sent"])
        self.assertIn("re-list", receipt["hint"])
        self.assertEqual(receipt["operation_id"], "race")

    def test_f1_the_lock_a_waiter_holds_through_its_reread_protects_the_record(self):
        # The close-before-re-read gap: the waiter has won the lock, the record is old enough to prune, and
        # admission runs before the waiter re-reads. Holding the lock until after the read keeps the pair.
        survived, admission, outcome, dispatchers = self.race(
            pause_before_reread, age_after_finish=module.RECENT_RECEIPT + 60)
        self.assertTrue(survived, "the record was pruned between the waiter's lock and its re-read")
        self.assertEqual(admission, ["refused", "store_full"])
        self.assertEqual(len(dispatchers), 1)
        self.assertTrue(outcome[1]["duplicate"])
        self.assertEqual(outcome[1]["result"], "verified_absent")

    def test_f1_a_caller_that_found_no_record_may_still_dispatch(self):
        calls = []
        self.assertEqual(self.direct("fresh", self.finished(calls))[0], 0)
        self.assertEqual(calls, ["fresh"])

    def test_f1_the_protection_window_is_derived_not_typed(self):
        self.assertEqual(module.RECENT_RECEIPT, module.WAIT_FOR_OWNER + module.TIMEOUT[1])


class WindowTests(ForkedCase):
    def prunable(self, age, result="verified_absent"):
        path = self.put("window", result=result, age=age)
        return module.Store(str(self.state)).prunable(path.name, time.time())

    def test_a_finished_receipt_is_kept_while_a_waiter_may_still_want_it(self):
        self.assertFalse(self.prunable(1))
        self.assertFalse(self.prunable(module.RECENT_RECEIPT - 1))
        self.assertTrue(self.prunable(module.RECENT_RECEIPT + 1))

    def test_an_uncertain_receipt_still_waits_for_the_thirty_days(self):
        self.assertFalse(self.prunable(module.RECENT_RECEIPT + 1, result="uncertain"))
        self.assertTrue(self.prunable(module.UNCERTAIN_TTL + 1, result="uncertain"))

    def test_a_full_store_of_fresh_receipts_refuses_before_dispatch(self):
        self.fill(module.STORE_LIMIT)
        calls = []
        with self.assertRaises(module.Refusal) as refused:
            self.direct("new", self.finished(calls))
        self.assertEqual((refused.exception.reason, calls), ("store_full", []))


class DirectoryFsyncTests(ForkedCase):
    def run_request(self, operation_id, fail_on_directory):
        original = os.fsync
        directories = 0

        def failing(descriptor):
            nonlocal directories
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                directories += 1
                if directories == fail_on_directory:
                    raise OSError(errno.ENOSPC, "injected directory sync failure")
            return original(descriptor)
        broker = pty.Broker(str(self.broker), str(self.runtime), None, 10)
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.env(KILIX_STATE_DIRECTORY=str(self.state))), \
                mock.patch.object(module, "read_request", return_value=kill_request(operation_id).encode()), \
                mock.patch.object(os, "fsync", failing), contextlib.redirect_stdout(out):
            code = module.cmd_request(broker, ["--yes", "--request-json", "-"])
        return code, json.loads(out.getvalue())

    def test_f2_a_directory_sync_failure_after_the_rename_returns_the_real_receipt_marked_not_durable(self):
        code, first = self.run_request("dir-full", fail_on_directory=2)
        self.assertEqual(len(self.kills()), 1)
        saved = json.loads(Path(module.Store(str(self.state)).path("dir-full")).read_text())
        self.assertEqual(saved["phase"], "done", "the record is visible, so it is not an intent")
        self.assertEqual((code, first["result"], first["request_sent"], first["duplicate"]),
                         (0, "verified_absent", True, False))
        self.assertIs(first["receipt_durable"], False)
        self.assertIn("interrupted", first["durability_note"])
        retry = self.request(kill_request("dir-full"), "--yes")
        again = json.loads(retry.stdout)
        self.assertEqual((retry.returncode, again["result"], again["duplicate"]), (0, "verified_absent", True))
        self.assertEqual(len(self.kills()), 1, "the retry sent nothing")
        # The first receipt and every replay agree on the outcome.
        self.assertEqual({k: v for k, v in first.items() if k in ("result", "id", "request_sent", "operation_id")},
                         {k: v for k, v in again.items() if k in ("result", "id", "request_sent", "operation_id")})

    def test_f2_the_visible_record_follows_the_ordinary_retention_rules(self):
        self.run_request("dir-full", fail_on_directory=2)
        name = Path(module.Store(str(self.state)).path("dir-full")).name
        store = module.Store(str(self.state))
        self.assertFalse(store.prunable(name, time.time()))
        self.assertTrue(store.prunable(name, time.time() + module.RECENT_RECEIPT + 5))

    def test_f2_a_durable_save_carries_no_flag(self):
        broker = pty.Broker(str(self.broker), str(self.runtime), None, 10)
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.env(KILIX_STATE_DIRECTORY=str(self.state))), \
                mock.patch.object(module, "read_request", return_value=kill_request("ok").encode()), \
                contextlib.redirect_stdout(out):
            self.assertEqual(module.cmd_request(broker, ["--yes", "--request-json", "-"]), 0)
        self.assertNotIn("receipt_durable", json.loads(out.getvalue()))


if __name__ == "__main__":
    unittest.main()
