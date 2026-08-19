"""`--install-desktop` and `--uninstall-desktop` are one transaction, apart.

The freedesktop paths are the only place kilix writes outside its own storage
tree, and they belong to the whole machine: `~/.local/share/applications` holds
every other launcher on the system and `hicolor` holds every other theme icon.
So an uninstall verb that deleted the paths the install verb happens to use
would be a worse bug than having no uninstall verb at all — it would take a
hand-edited entry, or another package's `kilix.png`, with it.

These tests pin the ownership contract instead: what the install recorded and
nothing has touched is removed, anything else is reported and survives, and the
directories that were already on the machine stay.
"""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DesktopEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.data = self.base / "data"
        self.apps = self.data / "applications"
        self.icons = self.data / "icons" / "hicolor"
        self.entry = self.apps / "kilix.desktop"
        self.icon_files = [
            self.icons / size / "apps" / "kilix.png"
            for size in ("128x128", "256x256", "512x512")
        ]
        self.manifest = (self.home / ".local" / "gpu_terminal" / "kilix" /
                         "state" / "desktop-entry.manifest")

    def tearDown(self):
        self.temp.cleanup()

    def run_kilix(self, *args):
        return subprocess.run(
            [str(ROOT / "kilix"), *args],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=120,
            env={
                "HOME": str(self.home),
                "PATH": "/usr/bin:/bin",
                "TERM": "xterm",
                "XDG_DATA_HOME": str(self.data),
            },
        )

    def install(self):
        result = self.run_kilix("--install-desktop")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def installed_paths(self):
        return [self.entry, *self.icon_files]

    def assertReleased(self, directory):
        """Uninstall prunes a directory it created, but only while empty.

        Asserting the directory is simply gone tests something stronger than
        the contract, and something that is not true of a normal desktop
        machine: `update-desktop-database` writes `mimeinfo.cache` into the
        applications directory as a side effect of installing an entry, so
        after uninstall the directory legitimately still holds a file this
        install never recorded and correctly must not be removed. That made
        the suite pass only on hosts which happen to lack that tool.

        What the contract actually forbids is retaining a directory that IS
        empty, so that is what this checks.
        """
        if not directory.exists():
            return
        remaining = sorted(p.name for p in directory.iterdir())
        self.assertTrue(
            remaining,
            f"{directory} survived while empty; an empty directory this "
            f"install created must be pruned")

    def test_install_then_uninstall_leaves_nothing_behind(self):
        self.install()
        for path in self.installed_paths():
            self.assertTrue(path.is_file(), path)
        self.assertTrue(self.manifest.is_file())

        result = self.run_kilix("--uninstall-desktop")

        self.assertEqual(result.returncode, 0, result.stderr)
        for path in self.installed_paths():
            self.assertFalse(path.exists(), path)
        self.assertFalse(self.manifest.exists())
        # Only directories this install created, and only while empty.
        self.assertReleased(self.apps)
        self.assertReleased(self.icons)
        self.assertIn("removed: 4 installed file(s)", result.stdout)

    def test_the_record_is_private_and_names_what_was_written(self):
        self.install()

        self.assertEqual(self.manifest.stat().st_mode & 0o777, 0o600)
        lines = self.manifest.read_text().splitlines()
        self.assertEqual(lines[0], "kilix-desktop-entry 1")
        files = {}
        directories = []
        for line in lines[1:]:
            fields = line.split("\t")
            if fields[0] == "file":
                files[fields[2]] = fields[1]
            else:
                directories.append(fields[1])
        self.assertEqual(set(files), {str(p) for p in self.installed_paths()})
        for path, digest in files.items():
            self.assertEqual(
                hashlib.sha256(Path(path).read_bytes()).hexdigest(), digest)
        self.assertIn(str(self.apps), directories)

    def test_an_edited_entry_is_reported_and_kept(self):
        self.install()
        self.entry.write_text(
            self.entry.read_text() + "X-Kilix-Local-Change=true\n")

        result = self.run_kilix("--uninstall-desktop")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kept (changed since it was installed)", result.stderr)
        self.assertIn(str(self.entry), result.stderr)
        self.assertIn("X-Kilix-Local-Change=true", self.entry.read_text())
        # The rest of the install is still removed; only the edit survives.
        for icon in self.icon_files:
            self.assertFalse(icon.exists(), icon)
        # And the record survives with it, so the run can be repeated once the
        # user has dealt with the file it named.
        self.assertTrue(self.manifest.is_file())

    def test_a_repeated_run_finishes_after_the_edit_is_resolved(self):
        self.install()
        self.entry.write_text("hand written\n")
        self.assertNotEqual(self.run_kilix("--uninstall-desktop").returncode, 0)

        self.entry.unlink()
        result = self.run_kilix("--uninstall-desktop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("4 already gone", result.stdout)
        self.assertFalse(self.manifest.exists())
        self.assertReleased(self.apps)

    def test_a_replaced_icon_is_never_followed(self):
        # A symlink where a recorded regular file was is somebody else's
        # arrangement; removing it could take an unrelated file with it.
        self.install()
        elsewhere = self.base / "other-theme.png"
        elsewhere.write_bytes(b"another package's icon\n")
        icon = self.icon_files[0]
        icon.unlink()
        icon.symlink_to(elsewhere)

        result = self.run_kilix("--uninstall-desktop")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kept (no longer a regular file", result.stderr)
        self.assertTrue(icon.is_symlink())
        self.assertEqual(elsewhere.read_bytes(), b"another package's icon\n")

    def test_unowned_neighbours_and_their_directories_survive(self):
        self.apps.mkdir(parents=True)
        neighbour = self.apps / "someone-else.desktop"
        neighbour.write_text("[Desktop Entry]\nName=Not kilix\n")
        self.install()

        result = self.run_kilix("--uninstall-desktop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.entry.exists())
        self.assertEqual(neighbour.read_text(),
                         "[Desktop Entry]\nName=Not kilix\n")
        # The directory predates the install, so it is not this verb's to
        # prune even though the install would have created it.
        self.assertTrue(self.apps.is_dir())

    def test_reinstalling_keeps_the_directories_it_first_created(self):
        self.install()
        self.install()

        result = self.run_kilix("--uninstall-desktop")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertReleased(self.apps)
        self.assertReleased(self.icons)

    def test_uninstall_without_a_record_refuses_and_removes_nothing(self):
        self.apps.mkdir(parents=True)
        stray = self.apps / "kilix.desktop"
        stray.write_text("[Desktop Entry]\nName=installed by hand\n")

        result = self.run_kilix("--uninstall-desktop")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing recorded to uninstall", result.stderr)
        self.assertIn("installed by hand", stray.read_text())

    def test_a_corrupt_record_is_refused_before_anything_is_removed(self):
        self.install()
        self.manifest.write_text("something else entirely\n")

        result = self.run_kilix("--uninstall-desktop")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized install record format", result.stderr)
        for path in self.installed_paths():
            self.assertTrue(path.is_file(), path)

    def test_a_relative_path_in_the_record_is_refused(self):
        self.install()
        self.manifest.write_text(
            "kilix-desktop-entry 1\n"
            "file\t" + "0" * 64 + "\tapplications/kilix.desktop\n")

        result = self.run_kilix("--uninstall-desktop")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing a relative path", result.stderr)
        self.assertTrue(self.entry.is_file())


if __name__ == "__main__":
    unittest.main()
