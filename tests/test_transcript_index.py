"""Tests for `kilix transcript list` and the sidecars that label it.

The index is the part of session logging that makes a lost pane findable, so
these cover the two ways it has failed to do that: exiting before it printed
anything, and printing only random session IDs.
"""

import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "kilix"
MODULE_PATH = ROOT / "src" / "kitty" / "pty_broker.py"

HAVE_REAPER = bool(shutil.which("zstd") and shutil.which("flock"))


def load_module():
    spec = importlib.util.spec_from_file_location(
        "kilix_test_transcript_broker", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load pty_broker module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_launcher(*args, storage, transcript_dir=None, extra_env=None):
    """Run the launcher against a storage root of our own.

    Every KILIX_/KITTY_ variable is dropped first: the suite is usually run
    from inside a Kilix pane, and an inherited KILIX_CONFIG_HOME pointing at
    the real installation trips the launcher's "writable roots must be strict
    descendants of Kilix storage" check before it reaches the subcommand.
    """
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("KILIX", "KITTY"))
    }
    env["KILIX_STORAGE_HOME"] = str(storage)
    if transcript_dir is not None:
        env["KILIX_TRANSCRIPT_DIR"] = str(transcript_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(LAUNCHER), *args],
        env=env, capture_output=True, text=True, timeout=120)


class TranscriptIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.storage = self.tmp / "storage"
        self.storage.mkdir()
        self.transcripts = self.tmp / "transcripts"
        self.transcripts.mkdir()

    def list_transcripts(self, **kwargs):
        return run_launcher(
            "transcript", "list", storage=self.storage,
            transcript_dir=self.transcripts, **kwargs)

    def write_log(self, name, contents="pane output\n", tier=None):
        directory = self.transcripts if tier is None else self.transcripts / tier
        directory.mkdir(parents=True, exist_ok=True)
        suffix = ".log" if tier is None else ".log.zst"
        path = directory / f"{name}{suffix}"
        path.write_text(contents)
        return path

    def write_sidecar(self, name, cwd, cmd=""):
        path = self.transcripts / f"{name}.meta"
        path.write_text(f"cwd={cwd}\ncmd={cmd}\n")
        return path

    def test_list_prints_index_without_tier_directories(self):
        # recent/ and archive/ appear only once a pane has died and been
        # compressed, so a fresh install walks directories that do not exist.
        # find fails, and under `set -o pipefail` that used to end the whole
        # command after its two header lines.
        self.write_log("aaaaaaaaaaaaaaaa")
        self.assertFalse((self.transcripts / "recent").exists())
        self.assertFalse((self.transcripts / "archive").exists())

        result = self.list_transcripts()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("directory:", result.stdout)
        self.assertIn("recent (zstd -3):", result.stdout)
        self.assertIn("aaaaaaaaaaaaaaaa.log [live]", result.stdout)

    def test_list_reports_every_tier(self):
        self.write_log("livepane00000000")
        self.write_log("recentpane000000", tier="recent")
        self.write_log("olderpane0000000", tier="archive")

        result = self.list_transcripts()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("livepane00000000.log [live]", result.stdout)
        self.assertIn("recentpane000000.log.zst [recent]", result.stdout)
        self.assertIn("olderpane0000000.log.zst [older]", result.stdout)

    def test_list_labels_entries_from_their_sidecar(self):
        self.write_log("labelled00000000")
        self.write_sidecar(
            "labelled00000000", os.path.join(os.path.expanduser("~"), "proj"),
            "codex --yolo")

        result = self.list_transcripts()

        self.assertEqual(result.returncode, 0, result.stderr)
        # $HOME is abbreviated so the width goes to the part being scanned.
        self.assertIn("~/proj — codex --yolo", result.stdout)

    def test_list_labels_a_compressed_pane(self):
        # The sidecar stays in the top-level directory whatever tier its log
        # reaches, because a dead pane is exactly when the label is wanted.
        self.write_log("deadpane00000000", tier="recent")
        self.write_sidecar("deadpane00000000", "/srv/build", "make test")

        result = self.list_transcripts()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deadpane00000000.log.zst [recent]  /srv/build — make test",
                      result.stdout)

    def test_list_survives_an_entry_with_no_sidecar(self):
        self.write_log("nosidecar0000000")

        result = self.list_transcripts()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nosidecar0000000.log [live]", result.stdout)

    def test_list_truncates_an_overlong_command(self):
        self.write_log("verbose000000000")
        self.write_sidecar("verbose000000000", "/tmp", "cmd " + "x" * 200)

        result = self.list_transcripts()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("…", result.stdout)
        self.assertNotIn("x" * 100, result.stdout)
        for line in result.stdout.splitlines():
            self.assertLess(len(line), 160, line)

    @unittest.skipUnless(HAVE_REAPER, "prune needs zstd and flock")
    def test_prune_drops_only_sidecars_whose_log_is_gone(self):
        broker = self.tmp / "stub-broker"
        broker.write_text('#!/bin/sh\necho "[]"\n')
        broker.chmod(0o755)

        self.write_log("livelog000000000")
        self.write_sidecar("livelog000000000", "/workspace/one")
        self.write_log("storedlog0000000", tier="recent")
        self.write_sidecar("storedlog0000000", "/workspace/two")
        orphan = self.write_sidecar("orphan0000000000", "/workspace/three")

        result = run_launcher(
            "transcript", "prune", storage=self.storage,
            transcript_dir=self.transcripts,
            extra_env={"KITTY_PTY_BROKER_EXECUTABLE": str(broker)})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(orphan.exists(), "a sidecar outlived its log")
        # The stub reports no live panes, so the plain log is compressed --
        # its sidecar has to follow the log into the recent tier, not vanish.
        self.assertTrue((self.transcripts / "livelog000000000.meta").exists())
        self.assertTrue((self.transcripts / "storedlog0000000.meta").exists())


class TranscriptSidecarTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {"KITTY_PTY_BROKER_TRANSCRIPT_DIR": str(self.tmp)}

    def test_sidecar_records_cwd_and_command_privately(self):
        path = self.module.write_transcript_metadata(
            "session00000001", "/workspace/proj", ["codex", "--yolo", "a b"],
            self.env)

        self.assertEqual(Path(path).name, "session00000001.meta")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(
            Path(path).read_text(),
            "cwd=/workspace/proj\ncmd=codex --yolo 'a b'\n")

    def test_sidecar_stays_line_oriented(self):
        # The reader splits on newlines, so an embedded one would let a
        # crafted cwd or argument forge a second field.
        path = self.module.write_transcript_metadata(
            "session00000002", "/tmp/a\nb", ["cmd\nnewline"], self.env)

        contents = Path(path).read_text()
        self.assertEqual(len(contents.splitlines()), 2)
        self.assertIn("cwd=/tmp/a b", contents)

    def test_sidecar_does_not_replace_an_existing_path(self):
        target = self.tmp / "session00000004.meta"
        target.write_text("keep\n")
        target.chmod(0o644)

        self.assertEqual(
            self.module.write_transcript_metadata(
                "session00000004", "/workspace/project", ["sh"], self.env),
            "")
        self.assertEqual(target.read_text(), "keep\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_no_sidecar_when_not_recording(self):
        self.assertEqual(
            self.module.write_transcript_metadata(
                "session00000003", "/tmp", ["sh"], {}),
            "")

    def test_no_sidecar_for_an_unusable_session_id(self):
        self.assertEqual(
            self.module.write_transcript_metadata(
                "../escape", "/tmp", ["sh"], self.env),
            "")
        self.assertEqual(list(self.tmp.iterdir()), [])


class TranscriptWiringTests(unittest.TestCase):
    def test_fork_writes_the_sidecar_when_it_wraps_a_pane(self):
        child = (ROOT / "src" / "kitty" / "child.py").read_text()
        self.assertIn("write_transcript_metadata(", child)
        # Recorded from the launch request, so the label names the program
        # asked for rather than the broker invocation wrapped around it.
        self.assertLess(
            child.index("write_transcript_metadata("),
            child.index("argv = wrap_command("))

    def test_launcher_labels_the_index(self):
        launcher = LAUNCHER.read_text()
        self.assertIn("_kilix_transcript_label", launcher)
        self.assertIn('"$_tr_dir/$_tr_id.meta"', launcher)


if __name__ == "__main__":
    unittest.main()
