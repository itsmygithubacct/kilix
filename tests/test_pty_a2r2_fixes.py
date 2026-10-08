"""Regression tests for the A2 round-2 review (reviews/A2-r2/REVIEW.md): R1, R2, R3 and the lock open.

Each fails on cf48c7a and passes after the fix. Real zstd throughout; a fake broker; scratch directories.
"""
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_cli import HAVE_ZSTD, LAUNCHER, PtyCliCase, reaped_pair  # noqa: E402
import kilix_pty  # noqa: E402

SECOND = b"SECOND"


def decompress(path):
    return subprocess.run(["zstd", "-dcq", "--long=27", str(path)], capture_output=True, check=True).stdout


def variant_stem(body, ident="arc", started=100):
    return f"{ident}.{started}+{hashlib.sha256(body).hexdigest()[:12]}"


@unittest.skipUnless(HAVE_ZSTD, "the journal archive needs zstd and flock")
class VariantTests(PtyCliCase):
    def dest(self):
        return self.state / "pty-journals"

    def contents(self):
        return {p.name: decompress(p) for p in self.dest().glob("*.journal.zst")}

    def archive(self, body, **env):
        reaped_pair(self.runtime, "arc", 100, body)
        result = self.pty("reap", **env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def pair(self):
        self.archive(b"FIRST")
        self.archive(SECOND)

    def test_r1_a_retry_after_the_canonical_name_was_evicted_makes_no_second_copy(self):
        self.pair()
        variant = variant_stem(SECOND)
        size = sum(p.stat().st_size for p in self.dest().glob(variant + ".*"))
        self.assertEqual(self.pty("reap", KILIX_PTY_JOURNAL_BUDGET=str(size)).returncode, 0)
        self.assertFalse((self.dest() / "arc.100.journal.zst").exists(), "the real budget evicted the canonical pair")
        before = self.contents()
        self.assertEqual(list(before.values()), [SECOND])
        result = self.archive(SECOND)
        self.assertIn("1 already archived", result.stdout)
        self.assertEqual(self.contents(), before, "a second copy was made")
        self.assertFalse((self.dest() / "arc.100.journal.zst").exists())
        self.assertEqual(list((self.runtime / "reaped").iterdir()), [])

    def test_r1_a_missing_meta_of_the_retained_variant_is_finished_not_duplicated(self):
        self.pair()
        variant = variant_stem(SECOND)
        for path in (self.dest() / "arc.100.journal.zst", self.dest() / "arc.100.meta", self.dest() / f"{variant}.meta"):
            path.unlink()
        self.archive(SECOND)
        self.assertEqual(sorted(p.name for p in self.dest().glob("*.journal.zst")), [f"{variant}.journal.zst"])
        self.assertTrue((self.dest() / f"{variant}.meta").is_file())

    def test_r2_each_selector_names_exactly_what_it_says(self):
        self.pair()
        variant = variant_stem(SECOND)
        self.assertEqual(self.pty("journals", "show", "arc.100").stdout, "FIRST")
        self.assertEqual(self.pty("journals", "show", variant).stdout, "SECOND")
        self.assertEqual(self.pty("journals", "show", "arc").stdout, "SECOND", "a bare ID is the newest archive")
        self.assertTrue(self.pty("journals", "path", "arc.100").stdout.strip().endswith("arc.100.journal.zst"))
        self.assertTrue(self.pty("journals", "path", variant).stdout.strip().endswith(f"{variant}.journal.zst"))
        self.assertEqual(self.pty("journals", "path", "arc.101").returncode, 1)
        listing = self.pty("journals").stdout
        self.assertIn(f"arc.100+{variant.split('+')[1]}", listing)

    def test_r2_the_canonical_selector_is_unaffected_when_a_variant_is_newer(self):
        self.pair()
        entries = kilix_pty.journals(str(self.dest()))
        self.assertEqual({e["variant"] is None for e in entries}, {True, False})
        chosen = kilix_pty.select_journal(entries, "arc.100")
        self.assertIsNone(chosen["variant"])
        self.assertIsNotNone(kilix_pty.select_journal(entries, "arc")["variant"])

    def test_eviction_never_touches_a_name_the_reader_rejects(self):
        self.dest().mkdir(parents=True, mode=0o700)
        stray = self.dest() / "..1.journal.zst"       # the IDs "." and ".." are never listed
        stray.write_bytes(b"USER DATA")
        self.assertEqual(kilix_pty.journals(str(self.dest())), [])
        self.archive(b"journal")
        self.pty("reap", KILIX_PTY_JOURNAL_BUDGET="1")
        self.assertTrue(stray.exists())


@unittest.skipUnless(HAVE_ZSTD, "the transcript pass needs zstd and flock")
class TranscriptBoundTests(PtyCliCase):
    def setUp(self):
        super().setUp()
        self.reply("list", out="[]")
        self.transcripts = self.tmp / "transcripts"
        (self.transcripts / "recent").mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.pidfile = self.tmp / "zstd.pid"
        (self.bin / "zstd").write_text(f'#!/bin/sh\necho $$ > "{self.pidfile}"\nexec sleep 90\n')
        (self.bin / "zstd").chmod(0o755)

    def run_pass(self, verb):
        env = self.env(PATH=f"{self.bin}:/usr/bin:/bin", KILIX_TRANSCRIPT_DIR=str(self.transcripts),
                       KILIX_PTY_ZSTD_TIMEOUT="1")
        process = subprocess.Popen(["bash", str(LAUNCHER), "transcript", verb], env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            while not self.pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(self.pidfile.exists(), "the compressor never started")
            out, err = process.communicate(timeout=30)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, 9)
                process.wait()
        lock = self.state / "transcript-reaper.lock"
        with lock.open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)      # raises if anything still holds it
        return process.returncode, out, err

    def test_r3_a_hung_log_compressor_is_cut_off_and_the_lock_released(self):
        log = self.transcripts / "deadpane00000000.log"
        log.write_bytes(b"OLD LOG\n" * 10)
        old = time.time() - 600
        os.utime(log, (old, old))
        code, out, err = self.run_pass("prune")
        self.assertEqual(code, 0, err)
        self.assertEqual(log.read_bytes(), b"OLD LOG\n" * 10, "the original is kept")
        self.assertEqual(list((self.transcripts / "recent").iterdir()), [], "no partial output")

    def test_r3_a_hung_recompression_is_cut_off_too(self):
        stored = self.transcripts / "recent" / "olderpane0000000.log.zst"
        real = subprocess.run(["/usr/bin/zstd", "-q", "-3", "-c"], input=b"older log\n" * 50, capture_output=True,
                              check=True).stdout
        stored.write_bytes(real)
        code, out, err = self.run_pass("archive")
        self.assertEqual(code, 0, err)
        self.assertEqual(stored.read_bytes(), real, "the original is kept")
        archive = self.transcripts / "archive"
        self.assertEqual(list(archive.iterdir()) if archive.exists() else [], [])


class LockOpenTests(PtyCliCase):
    def test_the_transcript_lock_never_follows_a_symlink(self):
        victim = self.tmp / "victim"
        victim.write_bytes(b"KEEP THIS FILE")
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "transcript-reaper.lock").symlink_to(victim)
        transcripts = self.tmp / "transcripts"
        transcripts.mkdir()
        (transcripts / "deadpane00000000.log").write_bytes(b"log\n")
        self.reply("list", out="[]")
        result = subprocess.run(["bash", str(LAUNCHER), "transcript", "prune"], env=self.env(
            KILIX_TRANSCRIPT_DIR=str(transcripts)), capture_output=True, text=True, timeout=60)
        self.assertEqual(victim.read_bytes(), b"KEEP THIS FILE")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((transcripts / "deadpane00000000.log").exists(), "nothing ran under a lock we could not take")

    def test_a_lock_that_is_a_fifo_symlink_is_refused_promptly_not_opened(self):
        fifo = self.tmp / "fifo"
        os.mkfifo(fifo)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "pty-journals.lock").symlink_to(fifo)
        reaped_pair(self.runtime, "arc", 100, b"journal\n")
        started = time.monotonic()
        result = self.pty("reap", timeout=30)
        self.assertLess(time.monotonic() - started, 15)
        self.assertTrue((self.runtime / "reaped" / "arc.100.journal").exists())
        self.assertIn("refusing the PTY journal lock", result.stderr + result.stdout)

    def test_the_helper_refuses_every_unsafe_lock_without_creating_through_it(self):
        target = self.tmp / "target"
        dangling = self.tmp / "dangling"
        dangling.symlink_to(target)
        directory = self.tmp / "dir"
        directory.mkdir()
        for path in (dangling, directory):
            program = ["python3", str(Path(kilix_pty.__file__)), "hold-lock", str(path)]
            result = subprocess.run(program, input="", capture_output=True, text=True, timeout=30)
            self.assertEqual(result.stdout.strip(), "refused", path)
        self.assertFalse(target.exists())

    def test_the_helper_creates_a_private_file_and_reports_a_held_lock(self):
        path = self.tmp / "fresh.lock"
        holder = subprocess.Popen(["python3", str(Path(kilix_pty.__file__)), "hold-lock", str(path)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            second = subprocess.run(["python3", str(Path(kilix_pty.__file__)), "hold-lock", str(path)],
                                    input="", capture_output=True, text=True, timeout=30)
            self.assertEqual(second.stdout.strip(), "busy")
        finally:
            holder.stdin.close()
            holder.wait(timeout=10)
            holder.stdout.close()
        again = subprocess.run(["python3", str(Path(kilix_pty.__file__)), "hold-lock", str(path)],
                               input="", capture_output=True, text=True, timeout=30)
        self.assertEqual(again.stdout.strip(), "locked", "the lock is free once the holder is gone")

    def test_a_lock_held_by_the_pass_is_released_when_it_ends(self):
        reaped_pair(self.runtime, "arc", 100, b"journal\n")
        self.assertEqual(self.pty("reap").returncode, 0)
        lock = self.state / "pty-journals.lock"
        with lock.open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


if __name__ == "__main__":
    unittest.main()
