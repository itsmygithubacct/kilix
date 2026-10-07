"""Regression tests for the A2 review's findings (reviews/A2/REVIEW.md, F2-F8).

Each one fails on 1ade459 / a27cd9b and passes after the fix; the first six are
the reviewer's own scenarios restated against the fake broker (and real zstd)
so they run in the ordinary suite. Real-broker coverage of F1 lives in
tests/test_pty_identity.py.
"""

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pty_cli import (HAVE_ZSTD, LAUNCHER, SESSION, PtyCliCase, gone_hook,  # noqa: E402
                          reaped_pair, status_json)

try:
    import zstandard  # noqa: F401
except ImportError:
    zstandard = None


def decompress(path):
    return subprocess.run(["zstd", "-dcq", "--long=27", str(path)], capture_output=True, check=True).stdout


@unittest.skipUnless(HAVE_ZSTD, "the journal archive needs zstd and flock")
class ArchiveCollisionTests(PtyCliCase):
    """F2: a different journal under a taken name is kept, an identical one is a retry."""

    def dest(self):
        return self.state / "pty-journals"

    def test_a_different_journal_under_the_same_name_is_kept_beside_the_first(self):
        reaped_pair(self.runtime, "arc", 100, b"FIRST")
        self.assertEqual(self.pty("reap").returncode, 0)
        reaped_pair(self.runtime, "arc", 100, b"SECOND")
        result = self.pty("reap")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 kept under a distinct name", result.stdout)
        self.assertEqual(decompress(self.dest() / "arc.100.journal.zst"), b"FIRST")
        variants = sorted(p.name for p in self.dest().glob("arc.100+*.journal.zst"))
        self.assertEqual(len(variants), 1, variants)
        self.assertEqual(decompress(self.dest() / variants[0]), b"SECOND")
        self.assertEqual(list((self.runtime / "reaped").iterdir()), [])
        listed = json.loads(self.pty("journals", "--json").stdout)["journals"]
        self.assertEqual(sorted(e["variant"] is None for e in listed), [False, True])
        self.assertEqual({e["id"] for e in listed}, {"arc"})

    def test_an_identical_journal_is_a_retry_and_rewrites_nothing(self):
        reaped_pair(self.runtime, "arc", 100, b"SAME BYTES\n" * 50)
        self.assertEqual(self.pty("reap").returncode, 0)
        archive = self.dest() / "arc.100.journal.zst"
        before = (archive.stat().st_mtime_ns, archive.stat().st_ino)
        reaped_pair(self.runtime, "arc", 100, b"SAME BYTES\n" * 50)
        result = self.pty("reap")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 already archived", result.stdout)
        self.assertNotIn("distinct", result.stdout)
        self.assertEqual((archive.stat().st_mtime_ns, archive.stat().st_ino), before)
        self.assertEqual([p.name for p in self.dest().glob("*.journal.zst")], ["arc.100.journal.zst"])
        self.assertEqual(list((self.runtime / "reaped").iterdir()), [])

    def test_an_interrupted_pair_is_finished_by_the_retry(self):
        # The journal is archived, the .meta never landed, the original is still there.
        reaped_pair(self.runtime, "arc", 100, b"BODY\n" * 20)
        self.assertEqual(self.pty("reap").returncode, 0)
        (self.dest() / "arc.100.meta").unlink()
        reaped_pair(self.runtime, "arc", 100, b"BODY\n" * 20)
        self.assertEqual(self.pty("reap").returncode, 0)
        self.assertTrue((self.dest() / "arc.100.meta").is_file())
        self.assertEqual(list((self.runtime / "reaped").iterdir()), [])


@unittest.skipUnless(HAVE_ZSTD, "the journal archive needs zstd and flock")
class ArchiveLockAndBudgetTests(PtyCliCase):
    def dest(self):
        return self.state / "pty-journals"

    def test_a_symlinked_lock_is_refused_and_its_target_is_not_truncated(self):
        reaped_pair(self.runtime, "arc", 100, b"journal\n")
        victim = self.tmp / "victim"
        victim.write_bytes(b"KEEP THIS FILE")
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "pty-journals.lock").symlink_to(victim)
        result = self.pty("reap")
        self.assertEqual(victim.read_bytes(), b"KEEP THIS FILE")
        self.assertIn("refusing the PTY journal lock", result.stderr + result.stdout)
        self.assertTrue((self.runtime / "reaped" / "arc.100.journal").exists(), "nothing was archived")

    def test_a_dangling_symlinked_lock_is_not_created_through(self):
        reaped_pair(self.runtime, "arc", 100, b"journal\n")
        target = self.tmp / "must-not-exist"
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "pty-journals.lock").symlink_to(target)
        self.pty("reap")
        self.assertFalse(target.exists())

    def test_the_lock_is_a_private_regular_file_and_a_second_pass_waits_out(self):
        reaped_pair(self.runtime, "arc", 100, b"journal\n")
        self.assertEqual(self.pty("reap").returncode, 0)
        lock = self.state / "pty-journals.lock"
        self.assertTrue(lock.is_file() and not lock.is_symlink())
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        with lock.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            reaped_pair(self.runtime, "two", 200, b"two\n")
            result = self.pty("reap")
        self.assertIn("another PTY journal pass is already running", result.stdout)
        self.assertTrue((self.runtime / "reaped" / "two.200.journal").exists())

    def test_the_budget_never_touches_files_the_reader_would_not_list(self):
        self.dest().mkdir(parents=True, mode=0o700)
        stray = self.dest() / "notes.journal.zst"
        stray.write_bytes(b"USER DATA" * 100)
        os.utime(stray, (1, 1))
        other = self.dest() / "notes.meta"
        other.write_text("not ours\n")
        os.utime(other, (1, 1))
        reaped_pair(self.runtime, "arc", 100, b"journal\n")
        result = self.pty("reap", KILIX_PTY_JOURNAL_BUDGET="400")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(stray.exists() and other.exists())

    def test_the_budget_evicts_the_oldest_session_not_the_oldest_file(self):
        old, _ = reaped_pair(self.runtime, "old", 100, b"OLD DATA")
        new, _ = reaped_pair(self.runtime, "new", 200, b"NEW DATA")
        os.utime(old, (200, 200))   # the older session wrote more recently
        os.utime(new, (100, 100))
        result = self.pty("reap", KILIX_PTY_JOURNAL_BUDGET="200")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("evicted 1", result.stdout)
        self.assertFalse((self.dest() / "old.100.journal.zst").exists())
        self.assertFalse((self.dest() / "old.100.meta").exists())
        self.assertTrue((self.dest() / "new.200.journal.zst").exists())

    def test_an_old_orphan_meta_is_swept_and_a_fresh_one_is_left(self):
        self.dest().mkdir(parents=True, mode=0o700)
        stale = self.dest() / "gone.50.meta"
        fresh = self.dest() / "mid.60.meta"
        for path in (stale, fresh):
            path.write_text("id=x\n")
        os.utime(stale, (time.time() - 7200,) * 2)
        reaped_pair(self.runtime, "arc", 100, b"journal\n")
        self.assertEqual(self.pty("reap").returncode, 0)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_a_symlinked_archive_directory_is_not_traversed(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        victim = outside / "safe.1.journal.zst"
        victim.write_bytes(b"KEEP" * 100)
        self.state.mkdir(parents=True, exist_ok=True)
        self.dest().symlink_to(outside, target_is_directory=True)
        self.pty("reap", KILIX_PTY_JOURNAL_BUDGET="1")
        self.assertTrue(victim.exists())


@unittest.skipUnless(HAVE_ZSTD, "the journal archive needs zstd and flock")
class ArchiveBoundTests(PtyCliCase):
    """F3: compression cannot keep transcript-reaper.lock, and a hung zstd is cut off."""

    def fake_zstd(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        pidfile = self.tmp / "zstd.pid"
        (bin_dir / "zstd").write_text(
            "#!/bin/sh\n" f'echo $$ > "{pidfile}"\nexec sleep 90\n')
        (bin_dir / "zstd").chmod(0o755)
        return bin_dir, pidfile

    def test_a_hung_compressor_neither_holds_the_transcript_lock_nor_outlasts_its_bound(self):
        reaped_pair(self.runtime, "arc", 100, b"journal\n")
        bin_dir, pidfile = self.fake_zstd()
        transcripts = self.tmp / "transcripts"
        transcripts.mkdir()
        env = self.env(PATH=f"{bin_dir}:/usr/bin:/bin", KILIX_TRANSCRIPT_DIR=str(transcripts),
                       KILIX_PTY_ZSTD_TIMEOUT="3")
        process = subprocess.Popen(["bash", str(LAUNCHER), "transcript", "prune"], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(pidfile.exists(), "the compressor never started")
            lock = self.state / "transcript-reaper.lock"
            with lock.open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)   # raises if the pass holds it
            out, err = process.communicate(timeout=30)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, 9)
                process.wait()
        self.assertEqual(process.returncode, 0, err)
        self.assertIn("errors 1", out)
        self.assertTrue((self.runtime / "reaped" / "arc.100.journal").exists(), "the original is kept")
        self.assertEqual([p.name for p in (self.state / "pty-journals").iterdir() if p.name.startswith(".")], [])

    def test_a_held_broker_build_lock_costs_the_guard_not_a_hang(self):
        build = self.storage / "build" / "libraries" / "kitty-pty-broker"
        build.mkdir(parents=True, mode=0o700)
        env = {k: v for k, v in self.env(KILIX_PTY_GUARD="1").items() if k != "KITTY_PTY_BROKER_EXECUTABLE"}
        with (build / ".build.lock").open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            started = time.monotonic()
            result = subprocess.run(["bash", str(LAUNCHER), "pty", "list", "--json"], env=env,
                                    capture_output=True, text=True, timeout=30,
                                    stdin=subprocess.DEVNULL)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another build holds the lock", result.stderr)


class KillVerificationTests(PtyCliCase):
    """F6 and F7: a timed-out terminate is never success; verification fits its own budget."""

    def setUp(self):
        super().setUp()
        self.reply("status", out=status_json() + "\n")
        self.reply("list", out="[%s]" % status_json())

    def kill(self, *global_args, **env):
        result = self.pty(*global_args, "kill", SESSION, "--yes", "--json",
                          KITTY_PTY_BROKER_SESSION="aaaaaaaaaaaaaaaa", **env)
        return result, json.loads(result.stdout)

    def test_a_terminate_that_never_answered_is_uncertain_even_if_the_session_is_later_absent(self):
        # The request is applied (the session is gone) but its reply is lost to the guard.
        (self.fake / "kill.hook").write_text(gone_hook(self.fake) + "exec sleep 60\n")
        result, receipt = self.kill(KILIX_PTY_GUARD="1")
        self.assertEqual((result.returncode, receipt["result"], receipt["reason"]),
                         (1, "uncertain", "terminate_timed_out"))
        self.assertIn("later seen absent", receipt["message"])

    def test_the_brokers_own_timeout_message_is_uncertain_too(self):
        (self.fake / "kill.hook").write_text(
            gone_hook(self.fake)
            + "echo 'kitty-pty-broker: kill session: timed out; the broker may still act on the request' >&2; exit 1\n")
        result, receipt = self.kill()
        self.assertEqual((result.returncode, receipt["result"], receipt["reason"]),
                         (1, "uncertain", "terminate_timed_out"))
        self.assertIn("timed out", receipt["message"])
        self.assertIn("later seen absent", receipt["message"])
        self.assertTrue(receipt["request_sent"])

    def test_a_terminate_that_failed_is_not_a_success_either(self):
        (self.fake / "kill.hook").write_text(
            gone_hook(self.fake) + "echo 'kitty-pty-broker: kill session: system error' >&2; exit 1\n")
        result, receipt = self.kill()
        self.assertEqual((result.returncode, receipt["reason"]), (1, "terminate_failed"))

    def test_verification_asks_only_about_the_target_and_fits_grace_plus_two_seconds(self):
        # After the terminate the target's status hangs; nothing else may stretch the wait.
        (self.fake / "kill.hook").write_text("touch \"$FAKE_DIR/status.hang\"\n")
        (self.fake / "status.out").write_text(status_json() + "\n")
        started = time.monotonic()
        result, receipt = self.kill("--timeout", "5")
        elapsed = time.monotonic() - started
        self.assertEqual((result.returncode, receipt["result"]), (1, "uncertain"))
        self.assertEqual(receipt["reason"], "verify_failed")
        self.assertLessEqual(receipt["waited_ms"], 4200, receipt)
        self.assertLess(elapsed, 8)
        calls = self.calls()
        after = calls[calls.index(next(c for c in calls if " kill " in c)) + 1:]
        self.assertTrue(after and all(" status " in c for c in after), after)


class KillIdentityTests(PtyCliCase):
    """F1: --expect-started is handed to the broker, which compares its own start time."""

    def setUp(self):
        super().setUp()
        self.reply("status", out=status_json() + "\n")
        self.reply("list", out="[%s]" % status_json())

    def kill(self, *flags):
        result = self.pty("kill", SESSION, "--yes", "--json", *flags,
                          KITTY_PTY_BROKER_SESSION="aaaaaaaaaaaaaaaa")
        return result, json.loads(result.stdout)

    def kill_calls(self):
        return [c for c in self.calls() if " kill " in c]

    def test_the_expectation_travels_with_the_terminate_request(self):
        (self.fake / "kill.hook").write_text(gone_hook(self.fake))
        result, receipt = self.kill("--expect-started", "1700000000000")
        self.assertEqual((result.returncode, receipt["result"]), (0, "verified_absent"))
        self.assertEqual(self.kill_calls(),
                         [f"--runtime-dir {self.runtime} kill {SESSION} --expect-started 1700000000000"])

    def test_a_plain_kill_stays_a_plain_terminate(self):
        (self.fake / "kill.hook").write_text(gone_hook(self.fake))
        result, receipt = self.kill()
        self.assertEqual(receipt["result"], "verified_absent")
        self.assertEqual(self.kill_calls(), [f"--runtime-dir {self.runtime} kill {SESSION}"])

    def test_a_broker_that_finds_a_replacement_refuses_and_nothing_is_verified_or_sent(self):
        self.reply("kill", err="kitty-pty-broker: kill session: refused: x is not the session that started "
                               "at 1700000000000 (it was replaced); nothing was done\n", rc=3)
        result, receipt = self.kill("--expect-started", "1700000000000")
        self.assertEqual((result.returncode, receipt["result"], receipt["reason"]), (3, "refused", "started_mismatch"))
        self.assertFalse(receipt["request_sent"])
        self.assertEqual(self.calls()[-1], self.kill_calls()[0], "no verification after a refusal")

    def test_a_broker_too_old_to_bind_is_never_killed_blind(self):
        self.reply("kill", err="kitty-pty-broker: kill session: this broker predates identity-checked kill\n", rc=5)
        result, receipt = self.kill("--expect-started", "1700000000000")
        self.assertEqual((result.returncode, receipt["result"], receipt["reason"]), (3, "refused", "cannot_bind"))
        self.assertFalse(receipt["request_sent"])
        self.assertIn("without --expect-started", receipt["message"])
        self.assertIn(f"kilix pty kill {SESSION} --yes", receipt["message"])
        self.assertEqual(len(self.kill_calls()), 1, "no unconditional kill follows")

    def test_a_refusal_by_the_broker_is_not_remembered_by_the_request_route(self):
        self.reply("kill", err="x\n", rc=3)
        payload = json.dumps({"schema": "kilix.pty.request/v1", "verb": "kill", "operation_id": "e-1",
                              "args": {"id": SESSION, "expect_started_millis": 1700000000000}})
        result = subprocess.run(["bash", str(LAUNCHER), "pty", "request", "--yes", "--request-json", "-"],
                                env=self.env(KITTY_PTY_BROKER_SESSION="aaaaaaaaaaaaaaaa"), input=payload,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual((result.returncode, json.loads(result.stdout)["reason"]), (3, "started_mismatch"))
        self.assertFalse((self.state / "pty-operations").exists()
                         and list((self.state / "pty-operations").glob("op-*.json")))


class ContractTests(PtyCliCase):
    """F8: the exit table, the --timeout range and one-line errors hold for every verb."""

    def test_a_missing_session_in_text_mode_is_exit_4(self):
        self.reply("status", err="kitty-pty-broker: query session: session not found\n", rc=1)
        result = self.pty("status", SESSION)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stderr.strip(), "kilix pty: query session: session not found")

    def test_broker_errors_are_kilix_pty_lines(self):
        self.reply("status", err="kitty-pty-broker: query session: timed out\n", rc=1)
        result = self.pty("status", SESSION)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(), "kilix pty: query session: timed out")
        self.reply("list", out="", err="kitty-pty-broker: list: aaaa: timeout\n")
        self.assertEqual(self.pty("list").stderr.strip(), "kilix pty: list: aaaa: timeout")

    def test_timeout_is_checked_for_every_verb_including_disk_only_ones(self):
        for bad in ("61", "0.05", "0", "-1", "abc", "1e1"):
            for verb in (["journals", "--json"], ["path"], ["capabilities"], ["list"], ["reaped"]):
                result = self.pty("--timeout", bad, *verb)
                self.assertEqual(result.returncode, 2, (bad, verb, result.stdout))
                self.assertIn("seconds, 0.1-60", result.stderr)
        for good in ("0.1", "60", "5"):
            self.assertEqual(self.pty("--timeout", good, "path").returncode, 0)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
