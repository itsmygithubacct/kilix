"""Regression tests for the A2 round-3 review (reviews/A2-r3/REVIEW.md): N1 and N2.

Each fails on 1dce58c and passes after the fix. Real zstd where it matters; a fake broker; scratch directories.
"""
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_a2r2_fixes import SECOND, decompress, variant_stem  # noqa: E402
from test_pty_cli import HAVE_ZSTD, LAUNCHER, PtyCliCase, reaped_pair  # noqa: E402
import kilix_pty  # noqa: E402

ZERO_SPELLINGS = ("0", "0.0", "00", ".0", "0.", "-1", "-0.5", "", "abc", "1e1", "5s", "1 2", " ")


@unittest.skipUnless(HAVE_ZSTD, "the journal archive needs zstd and flock")
class StrayVariantTests(PtyCliCase):
    def dest(self):
        return self.state / "pty-journals"

    def test_n1_a_name_the_reader_rejects_is_never_adopted_as_an_archive(self):
        self.dest().mkdir(parents=True, mode=0o700)
        stray = self.dest() / "arc.100+notes.journal.zst"
        stray.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=SECOND, capture_output=True,
                                         check=True).stdout)
        before = stray.read_bytes()
        reaped_pair(self.runtime, "arc", 100, SECOND)
        result = self.pty("reap")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(stray.read_bytes(), before, "an unrecognised file was touched")
        entries = kilix_pty.journals(str(self.dest()))
        self.assertEqual([e["path"].rsplit("/", 1)[1] for e in entries], ["arc.100.journal.zst"],
                         "the reaped journal was discarded for a file the reader does not list")
        self.assertEqual(decompress(self.dest() / "arc.100.journal.zst"), SECOND)
        self.assertEqual(self.pty("journals", "show", "arc").stdout, "SECOND")
        self.assertEqual(list((self.runtime / "reaped").iterdir()), [])

    def test_n1_other_near_misses_are_left_alone_too(self):
        self.dest().mkdir(parents=True, mode=0o700)
        compressed = subprocess.run(["zstd", "-q", "-c"], input=SECOND, capture_output=True, check=True).stdout
        names = ("arc.100+ABCDEF012345.journal.zst",      # upper case
                 "arc.100+abcdef01234.journal.zst",       # eleven
                 "arc.100+abcdef0123456.journal.zst",     # thirteen
                 "arc.100+.journal.zst")
        for name in names:
            (self.dest() / name).write_bytes(compressed)
        reaped_pair(self.runtime, "arc", 100, SECOND)
        self.assertEqual(self.pty("reap").returncode, 0)
        for name in names:
            self.assertEqual((self.dest() / name).read_bytes(), compressed, name)
        self.assertEqual(decompress(self.dest() / "arc.100.journal.zst"), SECOND)

    def test_n1_a_valid_variant_is_still_reused_beside_a_stray(self):
        stray = self.dest() / "arc.100+notes.journal.zst"
        for body in (b"FIRST", SECOND):
            reaped_pair(self.runtime, "arc", 100, body)
            self.assertEqual(self.pty("reap").returncode, 0)
            if body == b"FIRST":
                self.dest().mkdir(parents=True, exist_ok=True)
                stray.write_bytes(b"NOT AN ARCHIVE")
        variant = self.dest() / (variant_stem(SECOND) + ".journal.zst")
        self.assertEqual(decompress(variant), SECOND)
        inode = variant.stat().st_ino
        reaped_pair(self.runtime, "arc", 100, SECOND)
        result = self.pty("reap")
        self.assertIn("1 already archived", result.stdout)
        self.assertEqual(variant.stat().st_ino, inode, "a retry rewrote the variant")
        self.assertEqual(stray.read_bytes(), b"NOT AN ARCHIVE")
        self.assertEqual(list((self.runtime / "reaped").iterdir()), [])


class DurationTests(PtyCliCase):
    """Every deadline setting is a positive number or the default; zero never reaches GNU timeout."""

    def seconds(self, value):
        script = ('source <(sed -n "/^_kilix_pty_seconds()/,/^}/p" "$1"); '
                  '_kilix_pty_seconds "$2" 77')
        return subprocess.run(["bash", "-c", script, "x", str(LAUNCHER), value], capture_output=True, text=True,
                              check=True).stdout

    def test_n2_the_normaliser_replaces_every_non_positive_spelling(self):
        for value in ZERO_SPELLINGS:
            with self.subTest(value=value):
                self.assertEqual(self.seconds(value), "77\n")

    def test_n2_the_normaliser_keeps_positive_values(self):
        for value in ("1", "0.5", ".5", "2.", "30", "0.001", "007"):
            with self.subTest(value=value):
                self.assertEqual(self.seconds(value), value + "\n")


@unittest.skipUnless(HAVE_ZSTD, "the transcript pass and journal archive need zstd and flock")
class DeadlineReachesZstdTests(PtyCliCase):
    """A `timeout` shim records the duration each zstd call is given, then runs it with a short stand-in."""

    def setUp(self):
        super().setUp()
        self.reply("list", out="[]")
        self.transcripts = self.tmp / "transcripts"
        (self.transcripts / "recent").mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "durations"
        (self.bin / "zstd").write_text("#!/bin/sh\nexec sleep 90\n")
        (self.bin / "zstd").chmod(0o755)
        real = subprocess.run(["which", "timeout"], capture_output=True, text=True, check=True).stdout.strip()
        (self.bin / "timeout").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = -k ]; then kill_after="$2"; duration="$3"; shift 3; else exit 99; fi\n'
            f'printf "%s %s\\n" "${{1##*/}}" "$duration" >> "{self.log}"\n'
            f'exec {real} -k "$kill_after" 0.3 "$@"\n')
        (self.bin / "timeout").chmod(0o755)

    def durations(self, command="zstd"):
        lines = self.log.read_text().split("\n")[:-1] if self.log.exists() else []
        return [line.split(" ", 1)[1] for line in lines if line.split(" ", 1)[0] == command]

    def run_transcript(self, setting):
        log = self.transcripts / "deadpane00000000.log"
        log.write_bytes(b"OLD LOG\n" * 10)
        old = time.time() - 600
        os.utime(log, (old, old))
        self.log.unlink(missing_ok=True)
        env = self.env(PATH=f"{self.bin}:/usr/bin:/bin", KILIX_TRANSCRIPT_DIR=str(self.transcripts),
                       KILIX_PTY_ZSTD_TIMEOUT=setting)
        done = subprocess.run(["bash", str(LAUNCHER), "transcript", "prune"], env=env, capture_output=True,
                              text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(log.read_bytes(), b"OLD LOG\n" * 10)

    def run_archive(self, setting):
        reaped_pair(self.runtime, "arc", 100, SECOND)
        self.log.unlink(missing_ok=True)
        self.pty("reap", PATH=f"{self.bin}:/usr/bin:/bin", KILIX_PTY_ZSTD_TIMEOUT=setting)

    def test_n2_a_zero_transcript_deadline_is_replaced_by_the_default(self):
        for setting in ZERO_SPELLINGS:
            with self.subTest(setting=setting):
                self.run_transcript(setting)
                self.assertEqual(self.durations(), ["120"])

    def test_n2_a_positive_transcript_deadline_is_used(self):
        self.run_transcript("7")
        self.assertEqual(self.durations(), ["7"])

    def test_n2_a_zero_journal_deadline_is_replaced_by_the_default(self):
        for setting in ZERO_SPELLINGS:
            with self.subTest(setting=setting):
                self.run_archive(setting)
                self.assertTrue(self.durations(), "the compressor never ran")
                self.assertEqual(set(self.durations()), {"600"})

    def test_n2_a_zero_deadline_does_not_unbound_the_comparison_against_an_archive(self):
        # The same name, different contents: the existing archive is decompressed to compare.
        reaped_pair(self.runtime, "arc", 100, b"FIRST")
        self.assertEqual(self.pty("reap").returncode, 0)
        reaped_pair(self.runtime, "arc", 100, SECOND)
        self.log.unlink(missing_ok=True)
        self.pty("reap", PATH=f"{self.bin}:/usr/bin:/bin", KILIX_PTY_ZSTD_TIMEOUT="0")
        self.assertIn("600", self.durations())
        self.assertEqual(set(self.durations()), {"600"})

    def test_n2_a_zero_list_deadline_is_replaced_by_the_default(self):
        for setting in ("0", "0.0", "-1", ""):
            with self.subTest(setting=setting):
                self.log.unlink(missing_ok=True)
                env = self.env(PATH=f"{self.bin}:/usr/bin:/bin", KILIX_TRANSCRIPT_DIR=str(self.transcripts),
                               KILIX_PTY_LIST_TIMEOUT=setting)
                subprocess.run(["bash", str(LAUNCHER), "transcript", "prune"], env=env, capture_output=True,
                               text=True, timeout=60, stdin=subprocess.DEVNULL)
                self.assertEqual(self.durations(self.broker.name), ["5"])

    def test_n2_a_positive_journal_deadline_is_used(self):
        self.run_archive("9")
        self.assertEqual(set(self.durations()), {"9"})


class GuardTests(PtyCliCase):
    def test_n2_a_zero_guard_is_replaced_by_the_default_not_taken_literally(self):
        (self.fake / "hang").write_text("")
        started = time.monotonic()
        result = self.pty("list", timeout=60, KILIX_PTY_GUARD="0")
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not answer", result.stderr)
        self.assertGreater(elapsed, 5, "a zero guard was used as given")
        self.assertLess(elapsed, 40)


if __name__ == "__main__":
    unittest.main()
