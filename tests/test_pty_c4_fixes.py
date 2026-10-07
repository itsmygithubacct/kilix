"""Regression tests for the phase C round-4 review (reviews/C-kilix-r4/REVIEW.md).

F1: a waiter's held owner lock proves its owner gone only if it is still the operation's current lock
file; a stale inode from a forgotten generation must not turn a newer live intent into `interrupted`.
F2: a directory-fsync failure while recovering an unowned intent returns the real `interrupted` receipt,
flagged not durable, like every other completed-receipt write. Also: a failed first intent write does not
leak the owner descriptor. Each fails on fbf80515.
"""
import contextlib
import errno
import fcntl
import io
import json
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_c1_fixes import TARGET, kill_request  # noqa: E402
from test_pty_c3_fixes import ForkedCase  # noqa: E402
import kilix_pty_request as module  # noqa: E402


class StaleWaiterTests(ForkedCase):
    def test_f1_a_stale_waiter_must_not_mark_a_new_live_owner_interrupted(self):
        entered, release, opened, resume, new_entered, new_release = (
            self.tmp / name for name in ("old-entered", "old-release", "waiter-opened", "waiter-resume",
                                         "new-entered", "new-release"))
        store = module.Store(str(self.state))

        def dispatch(broker, request, state):
            entered.touch()
            self.wait_file(release)
            return 0, broker.envelope(result="verified_absent", request_sent=True, id=TARGET)

        owner = os.fork()
        if owner == 0:
            self.direct("race", dispatch)
            os._exit(0)
        waiter = newer = None
        try:
            self.wait_file(entered)
            waiter = os.fork()
            if waiter == 0:
                # Stop after the wait loop's deadline check and before its flock: a stall long enough
                # for the record to be forgotten and admitted again still ends in that flock.
                original, original_flock = module.Store.owner_lock, fcntl.flock
                state = {"opens": 0, "fd": None, "paused": False}

                def tracked(instance, operation_id):
                    descriptor = original(instance, operation_id)
                    state["opens"] += 1
                    if state["opens"] == 2:
                        state["fd"] = descriptor
                    return descriptor

                def paused_flock(descriptor, mode):
                    if descriptor == state["fd"] and not state["paused"] and mode & fcntl.LOCK_NB:
                        state["paused"] = True
                        opened.write_text(str(os.fstat(descriptor).st_ino))
                        self.wait_file(resume)
                    return original_flock(descriptor, mode)
                with mock.patch.object(module.Store, "owner_lock", tracked), \
                        mock.patch.object(fcntl, "flock", paused_flock), \
                        mock.patch.object(module, "WAIT_FOR_OWNER", 0.7):
                    result = self.direct("race", dispatch)
                (self.tmp / "stale-result").write_text(json.dumps(result))
                os._exit(0)
            self.wait_file(opened)
            release.touch()
            _, status = os.waitpid(owner, 0)
            owner = None
            self.assertEqual(status, 0)
            path = Path(store.path("race"))
            old = os.stat(path).st_mtime - module.RECENT_RECEIPT - 1
            os.utime(path, (old, old))
            self.fill(module.STORE_LIMIT - 1, phase="intent")
            self.direct("admission", self.finished([]))
            self.assertFalse(path.exists(), "the old receipt made room")
            os.utime(Path(store.path("admission")), (old, old))
            newer = os.fork()
            if newer == 0:
                def new_dispatch(broker, request, state):
                    new_entered.touch()
                    self.wait_file(new_release)
                    return 0, broker.envelope(result="verified_absent", request_sent=True, id=TARGET)
                self.direct("race", new_dispatch)
                os._exit(0)
            self.wait_file(new_entered)
            current_inode = path.with_suffix(".lock").stat().st_ino
            before = json.loads(path.read_text())
            resume.touch()
            _, status = os.waitpid(waiter, 0)
            waiter = None
            self.assertEqual(status, 0)
            after = json.loads(path.read_text())
            stale = json.loads((self.tmp / "stale-result").read_text())
            new_release.touch()
            os.waitpid(newer, 0)
            newer = None
            self.assertNotEqual(int(opened.read_text()), current_inode, "the schedule did not replace the lock")
            self.assertEqual((before["phase"], after["phase"]), ("intent", "intent"),
                             "a stale waiter converted a live new intent")
            self.assertEqual(stale[1]["reason"], "in_progress")
            self.assertEqual(json.loads(path.read_text())["phase"], "done")
        finally:
            release.touch()
            resume.touch()
            new_release.touch()
            self.reap(owner, waiter, newer)

    def test_f1_the_current_lock_inode_is_recognised_and_a_replaced_one_is_not(self):
        store = module.Store(str(self.state))
        os.close(store.open())        # creates the private directory
        first = store.owner_lock("inode")
        try:
            self.assertTrue(store.lock_is_current("inode", first))
            os.unlink(store.lock_path("inode"))
            self.assertFalse(store.lock_is_current("inode", first), "an unlinked lock is not current")
            second = store.owner_lock("inode")
            try:
                self.assertFalse(store.lock_is_current("inode", first), "a replaced lock is not current")
                self.assertTrue(store.lock_is_current("inode", second))
            finally:
                os.close(second)
        finally:
            os.close(first)


class RecoveryTests(ForkedCase):
    def intent(self, operation_id):
        store = module.Store(str(self.state))
        with store.locked():
            store.write(operation_id, {"operation_id": operation_id, "phase": "intent", "receipt": None,
                                       "fingerprint": module.fingerprint(self.payload(operation_id))})
        return Path(store.path(operation_id))

    def command(self, operation_id, dispatch, fail_directory=None):
        original, count = os.fsync, 0

        def failing(descriptor):
            nonlocal count
            if fail_directory and stat.S_ISDIR(os.fstat(descriptor).st_mode):
                count += 1
                if count == fail_directory:
                    raise OSError(errno.ENOSPC, "injected directory sync failure")
            return original(descriptor)
        import kilix_pty
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.env(KILIX_STATE_DIRECTORY=str(self.state))), \
                mock.patch.object(module, "read_request", return_value=kill_request(operation_id).encode()), \
                mock.patch.object(module, "run_kill", dispatch), mock.patch.object(os, "fsync", failing), \
                contextlib.redirect_stdout(out):
            code = module.cmd_request(kilix_pty.Broker(str(self.broker), str(self.runtime), None, 10),
                                      ["--yes", "--request-json", "-"])
        return code, json.loads(out.getvalue())

    def test_f2_crash_recovery_directory_fault_returns_real_interrupted_flagged(self):
        path = self.intent("crashed")
        calls = []
        # Directory syncs: the store lock's own read opens none; the recovery write is the first.
        code, first = self.command("crashed", self.finished(calls), fail_directory=1)
        self.assertEqual(json.loads(path.read_text())["phase"], "done")
        self.assertEqual(calls, [], "recovery must never dispatch")
        self.assertEqual((code, first["result"], first["reason"]), (1, "uncertain", "interrupted"))
        self.assertIs(first["receipt_durable"], False)
        self.assertTrue(first["duplicate"])
        self.assertEqual(first["operation_id"], "crashed")
        again = self.direct("crashed", self.finished(calls))[1]
        for key in ("result", "reason", "id", "request_sent", "operation_id"):
            self.assertEqual(first[key], again[key])
        self.assertNotIn("receipt_durable", again, "the flag is on the first returned document only")
        self.assertEqual(calls, [])

    def test_f2_a_durable_recovery_carries_no_flag(self):
        self.intent("crashed")
        code, first = self.command("crashed", self.finished([]))
        self.assertEqual((code, first["reason"]), (1, "interrupted"))
        self.assertNotIn("receipt_durable", first)

    def test_f2_an_initial_intent_sync_failure_stays_an_unsent_refusal(self):
        calls = []
        code, first = self.command("initial", self.finished(calls), fail_directory=1)
        self.assertEqual((code, first["reason"]), (3, "store_unavailable"))
        self.assertEqual(calls, [])
        self.assertNotIn("receipt_durable", first)

    def test_f1_a_failed_first_intent_write_does_not_leak_the_owner_descriptor(self):
        def failing(instance, operation_id, record):
            raise OSError(errno.ENOSPC, "injected")
        before = len(os.listdir("/proc/self/fd"))
        calls = []
        with mock.patch.object(module.Store, "write", failing):
            code, first = self.command("leak", self.finished(calls))
        self.assertEqual((code, first["reason"], calls), (3, "store_unavailable", []))
        self.assertEqual(len(os.listdir("/proc/self/fd")), before, "the owner lock descriptor leaked")
        # Nothing is holding the lock any more, so the same process can retry normally.
        code, again = self.command("leak", self.finished(calls))
        self.assertEqual((code, again["result"], calls), (0, "verified_absent", ["leak"]))


if __name__ == "__main__":
    unittest.main()
