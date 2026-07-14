import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import browse
import gfx
import stream


def load_desktop_storage():
    spec = importlib.util.spec_from_file_location(
        "kilix_desktop_storage", ROOT / "desktop" / "storage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StoragePermissionTests(unittest.TestCase):
    def test_desktop_state_writer_is_private_and_atomic(self):
        storage = load_desktop_storage()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "desktop"
            path = directory / ".state.json"
            storage.atomic_write_private(
                str(path), lambda stream: json.dump({"wall_image": "x"}, stream))
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text()), {"wall_image": "x"})

    def test_frame_directory_and_file_are_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"KILIX_SESSION_HOME": tmp}):
                directory = Path(gfx.session_dir("graphics", "test"))
                frame = directory / "frame.rgb"
                gfx.write_frame(str(frame), b"rgb")
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(frame.stat().st_mode), 0o600)

    def test_frame_writer_rejects_collisions_without_changing_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "tty-graphics-protocol-frame.rgb"
            gfx.write_frame(str(frame), b"original")
            with self.assertRaises(FileExistsError):
                gfx.write_frame(str(frame), b"replacement")
            self.assertEqual(frame.read_bytes(), b"original")

    def test_session_log_writer_is_private_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.log"
            with stream._private_open(str(path), "a") as log:
                log.write("private\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            link = Path(tmp) / "link.log"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                stream._private_open(str(link), "a")

    def test_dead_chromium_singletons_are_removed_but_unknown_lock_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                os.symlink("host-999999999", profile / name)
            self.assertEqual(
                browse.profile_lock_state(str(profile / "SingletonLock")), "stale")
            self.assertTrue(browse.remove_stale_profile_singletons(str(profile)))
            self.assertFalse(any((profile / name).exists() or
                                 (profile / name).is_symlink()
                                 for name in ("SingletonLock", "SingletonCookie",
                                              "SingletonSocket")))

            (profile / "SingletonLock").write_text("not a symlink")
            self.assertEqual(
                browse.profile_lock_state(str(profile / "SingletonLock")), "unknown")
            self.assertFalse(browse.remove_stale_profile_singletons(str(profile)))
            self.assertTrue((profile / "SingletonLock").is_file())

    def test_native_browser_uses_private_profile_and_stale_lock_cleanup(self):
        source = (ROOT / "src" / "kittens" / "browse" / "browse.go").read_text()
        self.assertIn("ensurePrivateDir(profile)", source)
        self.assertIn('case "stale":', source)
        self.assertIn("removeStaleProfileSingletons(profile)", source)
        self.assertNotIn("os.MkdirAll(profile, 0o755)", source)


if __name__ == "__main__":
    unittest.main()
