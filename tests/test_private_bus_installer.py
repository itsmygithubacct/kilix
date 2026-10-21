"""Real filesystem installation checks; the native CI job runs these as root."""
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "private_bus_installer",
    Path(__file__).resolve().parents[1] / "scripts/install-private-bus-supervisor.py",
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@unittest.skipUnless(os.geteuid() == 0, "requires the native CI root installation fixture")
class InstalledDirectories(unittest.TestCase):
    def setUp(self):
        # All ancestors must satisfy the installer's root ownership checks.
        self.temp = tempfile.TemporaryDirectory(prefix="kilix-installer-test-", dir="/usr/libexec")
        self.addCleanup(self.temp.cleanup)
        self.fixture = Path(self.temp.name)
        self.root = self.fixture / "supervisor"
        self.version = self.root / "version"
        self.target = self.version / "bus_supervisor.py"
        self.filesystem = installer.InstalledFilesystem()
        self.patch = patch.object(installer, "SUPERVISOR_ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def assert_public_directory(self, path):
        info = path.lstat()
        self.assertTrue(stat.S_ISDIR(info.st_mode))
        self.assertEqual(info.st_uid, 0)
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o755)

    def test_restrictive_umask_keeps_new_installed_directories_readable(self):
        previous = os.umask(0o077)
        try:
            self.filesystem.parent(self.target)
            self.assertEqual(os.umask(0o077), 0o077)
        finally:
            os.umask(previous)
        self.assert_public_directory(self.root)
        self.assert_public_directory(self.version)

    def test_reinstall_repairs_private_delivery_directories(self):
        self.root.mkdir(mode=0o700)
        self.version.mkdir(mode=0o700)
        self.filesystem.parent(self.target)
        self.assert_public_directory(self.root)
        self.assert_public_directory(self.version)

    def test_existing_parent_permissions_are_preserved(self):
        self.fixture.chmod(0o700)
        self.filesystem.parent(self.target)
        self.assertEqual(stat.S_IMODE(self.fixture.stat().st_mode), 0o700)

    def test_writable_or_symlink_delivery_directory_is_refused(self):
        self.root.mkdir()
        self.root.chmod(0o777)
        with self.assertRaisesRegex(RuntimeError, "unsafe installed directory"):
            self.filesystem.parent(self.target)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o777)
        self.root.rmdir()
        self.root.symlink_to(self.fixture, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "unsafe installed directory"):
            self.filesystem.parent(self.target)
        self.assertTrue(self.root.is_symlink())


if __name__ == "__main__":
    unittest.main()
