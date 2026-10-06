"""kilix backup: settings and desktop documents round-trip; hostile archives are refused."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from kilix_sdk import backup  # noqa: E402
sys.path.insert(0, str(ROOT / "tests"))
from _env_support import sandbox_env  # noqa: E402


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kilix-backup-test-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))
        gt = self.tmp / "gt"
        self.env = {
            "HOME": str(self.tmp / "home"),
            "GPU_TERMINAL_HOME": str(gt),
            "GPU_TERMINAL_SETTINGS_FILE": str(gt / "settings.conf"),
            "KILIX_CONFIG_HOME": str(gt / "kilix" / "config"),
            "KILIX95_STORAGE_HOME": str(gt / "kilix-95"),
            "KILIX95_CONFIG_HOME": str(gt / "kilix-95" / "config"),
            "KILIX95_STATE_HOME": str(gt / "kilix-95" / "state"),
            "KILIX_DESKTOP_DIR": str(gt / "desktop"),
        }
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("KITTY_CONFIG_DIRECTORY", "KILIX95_DATA_HOME"):
            os.environ.pop(key, None)
        (gt / "kilix" / "config").mkdir(parents=True)
        (gt / "kilix-95" / "config").mkdir(parents=True)
        state = gt / "kilix-95" / "state"
        (state / "document-recovery").mkdir(parents=True)
        (gt / "desktop" / "notes").mkdir(parents=True)
        (gt / "settings.conf").write_text("clock=24h\n")
        (gt / "kilix" / "config" / "kilix.env").write_text("KILIX_DESKTOP_FLAVOR=95\n")
        (gt / "kilix-95" / "config" / "theme.json").write_text('{"wall": "teal"}')
        (state / "desktop.state").write_bytes(b"\x00\x01layout")
        (state / "crash.log").write_text("traceback\n")
        (state / "document-recovery" / "abc").write_text("checkpoint")
        (gt / "desktop" / "letter.txt").write_text("Dear owner\n")
        (gt / "desktop" / "notes" / "todo.txt").write_text("buy milk\n")
        os.symlink("/etc/passwd", gt / "desktop" / "link-to-passwd")
        self.gt = gt

    def names(self, archive):
        with tarfile.open(archive) as tar:
            return [m.name for m in tar.getmembers()]

    def test_create_is_private_and_carries_only_user_files(self):
        archive = backup.create()
        self.assertEqual(stat.S_IMODE(os.stat(archive).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(archive)).st_mode), 0o700)
        names = self.names(archive)
        self.assertEqual(names[0], "manifest.json")
        self.assertEqual(sorted(names[1:]), [
            "desktop/letter.txt", "desktop/notes/todo.txt",
            "kilix/kilix.env", "kilix95/config/theme.json",
            "kilix95/state/desktop.state", "settings.conf"])

    def test_round_trip_restores_changed_files_and_keeps_a_safety_copy(self):
        archive = backup.create()
        (self.gt / "settings.conf").write_text("clock=12h\n")
        (self.gt / "desktop" / "letter.txt").unlink()
        (self.gt / "desktop" / "new.txt").write_text("written after the backup\n")
        actions = {name: action for name, _dest, action in backup.plan(archive)}
        self.assertEqual(actions["settings.conf"], "replace")
        self.assertEqual(actions["desktop/letter.txt"], "create")
        self.assertEqual(actions["kilix/kilix.env"], "same")
        result = backup.restore(archive)
        self.assertEqual((self.gt / "settings.conf").read_text(), "clock=24h\n")
        self.assertEqual((self.gt / "desktop" / "letter.txt").read_text(), "Dear owner\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.gt / "desktop" / "letter.txt").st_mode), 0o600)
        self.assertTrue((self.gt / "desktop" / "new.txt").exists(), "restore never deletes")
        self.assertEqual(result["written"], 2)
        with tarfile.open(result["safety"]) as tar:
            saved = tar.extractfile("settings.conf").read()
        self.assertEqual(saved, b"clock=12h\n", "the safety backup holds the replaced file")

    def hostile(self, members, manifest_entries=None, skip_manifest=False):
        path = self.tmp / "hostile.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            if not skip_manifest:
                entries = manifest_entries if manifest_entries is not None else {
                    name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    for name, data, _kind in members if data is not None}
                body = json.dumps({"format": backup.FORMAT, "entries": entries}).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
            for name, data, kind in members:
                info = tarfile.TarInfo(name)
                if kind == "symlink":
                    info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                    tar.addfile(info)
                else:
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
        return str(path)

    def assert_refused(self, archive):
        before = (self.gt / "settings.conf").read_text()
        with self.assertRaises(backup.BackupError):
            backup.restore(archive)
        self.assertEqual((self.gt / "settings.conf").read_text(), before)
        self.assertFalse((self.tmp / "home" / "kilix-backups").exists(),
                         "a refused archive takes no safety backup either")

    def test_hostile_archives_are_refused_whole(self):
        ok = (b"clock=0\n", "file")
        self.assert_refused(self.hostile([("settings.conf",) + ok, ("../escape", b"x", "file")]))
        self.assert_refused(self.hostile([("settings.conf",) + ok, ("desktop/../../x", b"x", "file")]))
        self.assert_refused(self.hostile([("settings.conf",) + ok, ("/etc/cron.d/x", b"x", "file")]))
        self.assert_refused(self.hostile([("settings.conf",) + ok, ("desktop/l", None, "symlink")]))
        self.assert_refused(self.hostile([("settings.conf",) + ok, ("elsewhere/x", b"x", "file")]))
        self.assert_refused(self.hostile([("settings.conf",) + ok], skip_manifest=True))
        self.assert_refused(self.hostile([("settings.conf",) + ok],
                                         manifest_entries={"settings.conf": {"size": 8, "sha256": "0" * 64}}))
        self.assert_refused(self.hostile([("settings.conf",) + ok],
                                         manifest_entries={"settings.conf": {"size": 8, "sha256": hashlib.sha256(b"clock=0\n").hexdigest()},
                                                           "desktop/missing.txt": {"size": 1, "sha256": "0" * 64}}))

    def test_validation_alone_refuses_links_and_strays(self):
        ok = hashlib.sha256(b"clock=0\n").hexdigest()
        listed_link = self.hostile(
            [("settings.conf", b"clock=0\n", "file"), ("desktop/l", None, "symlink")],
            manifest_entries={"settings.conf": {"size": 8, "sha256": ok},
                              "desktop/l": {"size": 0, "sha256": hashlib.sha256(b"").hexdigest()}})
        with self.assertRaisesRegex(backup.BackupError, "refusing archive member"):
            backup.read(listed_link)
        stray = self.hostile([("settings.conf", b"clock=0\n", "file"), ("elsewhere/x", b"x", "file")])
        with self.assertRaisesRegex(backup.BackupError, "outside the backup"):
            backup.read(stray)

    def test_restore_never_writes_through_a_symlinked_directory(self):
        archive = backup.create()
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.gt / "desktop" / "notes" / "todo.txt").unlink()
        (self.gt / "desktop" / "notes").rmdir()
        os.symlink(outside, self.gt / "desktop" / "notes")
        (self.gt / "desktop" / "letter.txt").unlink()        # sorts before notes/
        with self.assertRaises(backup.BackupError):
            backup.restore(archive)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.gt / "desktop" / "letter.txt").exists(),
                         "nothing is written before every destination is checked")

    def test_cli_restore_without_yes_writes_nothing(self):
        archive = backup.create()
        (self.gt / "settings.conf").write_text("clock=12h\n")
        env = sandbox_env(**self.env, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "config/kilix_sdk/backup.py"),
                                 "restore", archive], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("replace  settings.conf", result.stdout)
        self.assertIn("Nothing written", result.stdout)
        self.assertEqual((self.gt / "settings.conf").read_text(), "clock=12h\n")

    def test_a_file_changing_mid_backup_still_gives_a_valid_archive(self):
        real_dumps = backup.json.dumps
        letter = self.gt / "desktop" / "letter.txt"

        def edit_then_dump(*args, **kwargs):          # after spooling, before the tar
            letter.write_text("DEAR OWNER\n")        # same length, different bytes
            return real_dumps(*args, **kwargs)
        with mock.patch.object(backup.json, "dumps", edit_then_dump):
            archive = backup.create()
        _manifest, payload = backup.read(archive)      # must not be a digest mismatch
        self.assertEqual(payload["desktop/letter.txt"], b"Dear owner\n")

    def test_the_desktop_folder_set_in_kilix_env_is_the_one_backed_up(self):
        elsewhere = self.tmp / "my-desktop"
        elsewhere.mkdir()
        (elsewhere / "mine.txt").write_text("from kilix.env's desktop\n")
        (self.gt / "kilix" / "config" / "kilix.env").write_text(f"KILIX_DESKTOP_DIR={elsewhere}\n")
        os.environ.pop("KILIX_DESKTOP_DIR")
        self.assertIn("desktop/mine.txt", self.names(backup.create()))
        os.environ["KILIX_DESKTOP_DIR"] = str(self.gt / "desktop")   # explicit env still wins
        self.assertIn("desktop/letter.txt", self.names(backup.create()))

    def test_cli_restore_refuses_while_the_desktop_runs(self):
        archive = backup.create()
        (self.gt / "settings.conf").write_text("clock=12h\n")
        with mock.patch.object(backup, "desktop_running", return_value=True):
            self.assertEqual(backup.main(["restore", archive, "--yes"]), 3)
        self.assertEqual((self.gt / "settings.conf").read_text(), "clock=12h\n")

    def test_a_running_desktop_is_detected(self):
        k95 = self.tmp / "k95"
        k95.mkdir()
        (k95 / "main.py").write_text("import time\ntime.sleep(30)\n")
        # Set where the launcher reads it: kilix.env, not this process's environment.
        (self.gt / "kilix" / "config" / "kilix.env").write_text(f"KILIX95_DIR={k95}\n")
        os.environ.pop("KILIX95_DIR", None)
        self.assertFalse(backup.desktop_running())
        proc = subprocess.Popen([sys.executable, str(k95 / "main.py")])
        self.addCleanup(proc.kill)
        for _ in range(50):
            if Path(f"/proc/{proc.pid}/cmdline").read_bytes().count(b"main.py"):
                break
        self.assertTrue(backup.desktop_running())

    def test_colliding_long_surplus_and_crash_members_are_refused(self):
        ok = (b"clock=0\n", "file")
        cases = {
            "both a file and a folder": [("settings.conf",) + ok, ("desktop/a", b"x", "file"),
                                         ("desktop/a/b", b"y", "file")],
            "refusing archive member": [("settings.conf",) + ok, ("desktop/" + "n" * 300, b"x", "file")],
            "crash state": [("settings.conf",) + ok, ("kilix95/state/crash.log", b"t", "file")],
        }
        for message, members in cases.items():
            with self.subTest(message=message):
                with self.assertRaisesRegex(backup.BackupError, message):
                    backup.read(self.hostile(members))
        surplus = self.hostile([("settings.conf",) + ok, ("desktop/x", b"x", "file"),
                                ("desktop/y", b"y", "file")],
                               manifest_entries={"settings.conf": {"size": 8, "sha256": hashlib.sha256(b"clock=0\n").hexdigest()}})
        with self.assertRaisesRegex(backup.BackupError, "more members than its manifest"):
            backup.read(surplus)

    def test_restore_says_kilix_env_needs_a_new_session(self):
        env_file = self.gt / "kilix" / "config" / "kilix.env"
        archive = backup.create()
        (self.gt / "settings.conf").write_text("clock=12h\n")
        result = backup.restore(archive)
        self.assertEqual(result["names"], ["settings.conf"])
        self.assertNotIn("log out", backup.restart_advice(result["names"]))
        env_file.write_text("KILIX_DESKTOP_FLAVOR=classic\n")
        out = io.StringIO()
        with mock.patch("sys.stdout", out), \
                mock.patch.object(backup, "desktop_running", return_value=False):
            self.assertEqual(backup.main(["restore", archive, "--yes"]), 0)
        self.assertIn("log out and back in", out.getvalue())
        self.assertIn("restarting the desktop is not enough", out.getvalue())

    def test_the_safety_copy_holds_only_what_is_replaced(self):
        archive = backup.create()
        (self.gt / "settings.conf").write_text("clock=12h\n")
        result = backup.restore(archive)
        self.assertEqual(self.names(result["safety"]), ["manifest.json", "settings.conf"])
        self.assertIsNone(backup.restore(archive)["safety"], "nothing replaced, nothing copied")

    def test_setting_changes_are_shown_and_launch_keys_flagged(self):
        env_file = self.gt / "kilix" / "config" / "kilix.env"
        env_file.write_text("KILIX_DESKTOP_COMMAND=/usr/bin/trusted\n")
        archive = backup.create()
        env_file.write_text("KILIX_DESKTOP_COMMAND=/usr/bin/other\n")
        self.assertIn(("kilix/kilix.env", "KILIX_DESKTOP_COMMAND", "/usr/bin/other", "/usr/bin/trusted"),
                      backup.changes(archive))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            backup.main(["list", archive])
        self.assertIn("decides what the desktop runs", out.getvalue())

    def test_the_size_cap_holds_on_create_and_read(self):
        archive = backup.create()
        with mock.patch.object(backup, "MAX_TOTAL", 10):
            with self.assertRaisesRegex(backup.BackupError, "exceed 1 GiB"):
                backup.create()
            with self.assertRaisesRegex(backup.BackupError, "larger than 1 GiB"):
                backup.read(archive)

    def test_create_refuses_what_read_would_refuse(self):
        with mock.patch.object(backup, "MAX_MEMBERS", 2):
            with self.assertRaisesRegex(backup.BackupError, "more than 2 files"):
                backup.create()
        archive = backup.create()
        with mock.patch.object(backup, "MAX_MANIFEST", 64):
            with self.assertRaisesRegex(backup.BackupError, "manifest would be too large"):
                backup.create()
            with self.assertRaisesRegex(backup.BackupError, "manifest too large"):
                backup.read(archive)
        # A desktop of many small files still round-trips: the manifest stays compact.
        many = self.gt / "desktop" / "many"
        many.mkdir()
        for i in range(3000):
            (many / f"note-{i:05d}-{'x' * 40}.txt").write_text(str(i))
        archive = backup.create()
        _manifest, payload = backup.read(archive)
        self.assertEqual(sum(name.startswith("desktop/many/") for name in payload), 3000)
        with tarfile.open(archive) as tar:
            raw = tar.extractfile("manifest.json").read()
        self.assertEqual(raw, json.dumps(_manifest, sort_keys=True, separators=(",", ":")).encode())

    def test_every_kind_of_launch_key_is_flagged(self):
        for key in ("KILIX_DESKTOP_COMMAND", "KILIX95_DIR", "KILIX95_REPO", "KILIX95_REF",
                    "KILIX95_TRUST_EXISTING_CHECKOUT", "KILIX_TUI_UTILS_DIR",
                    "KILIX_ALLOW_UNVERIFIED_PREBUILT", "KILIX_CAP_AUTO_INSTALL",
                    "KILIX_AVATAR_PREFIX", "KILIX_QWEN_GPU_PYTHON", "KILIX_HOME",
                    "KILIX_DESKTOP_FLAVOR", "KILIX95_BRANCH", "KILIX_PTY_BROKER"):
            self.assertTrue(backup.launch_key(key), key)
        for key in ("clock", "KILIX_STREAM", "KILIX_PTY_BROKER_JOURNAL_LIMIT"):
            self.assertFalse(backup.launch_key(key), key)

    def test_a_failed_write_names_what_was_restored_and_where_the_old_files_are(self):
        archive = backup.create()
        (self.gt / "settings.conf").write_text("clock=12h\n")
        (self.gt / "desktop" / "letter.txt").write_text("edited\n")
        real = os.replace
        def fail_second(src, dst):
            if dst.endswith("settings.conf"):
                raise OSError(28, "No space left on device")
            return real(src, dst)
        with mock.patch.object(backup.os, "replace", fail_second):
            with self.assertRaises(backup.BackupError) as caught:
                backup.restore(archive)
        message = str(caught.exception)
        self.assertIn("could not write settings.conf: No space left on device", message)
        self.assertIn("already restored: desktop/letter.txt", message)
        safety = message.rsplit("previous files: ", 1)[1]
        self.assertEqual(sorted(self.names(safety)),
                         ["desktop/letter.txt", "manifest.json", "settings.conf"])
        self.assertEqual((self.gt / "desktop" / "letter.txt").read_text(), "Dear owner\n")
        self.assertEqual((self.gt / "settings.conf").read_text(), "clock=12h\n")
        self.assertEqual([p for p in self.gt.iterdir() if p.name.startswith(".kilix-restore-")], [])

    def test_launcher_dispatches_backup_before_setup(self):
        launcher = (ROOT / "kilix").read_text()
        self.assertIn('exec python3 -B "$KILIX_HOME/config/kilix_sdk/backup.py" "$@" ;;', launcher)


if __name__ == "__main__":
    unittest.main()
