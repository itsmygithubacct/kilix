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
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "config/kilix_sdk/backup.py"),
                                 "restore", archive], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("replace  settings.conf", result.stdout)
        self.assertIn("Nothing written", result.stdout)
        self.assertEqual((self.gt / "settings.conf").read_text(), "clock=12h\n")

    def test_launcher_dispatches_backup_before_setup(self):
        launcher = (ROOT / "kilix").read_text()
        self.assertIn('exec python3 -B "$KILIX_HOME/config/kilix_sdk/backup.py" "$@" ;;', launcher)


if __name__ == "__main__":
    unittest.main()
