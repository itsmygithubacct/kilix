import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from kilix_sdk import state


class KilixStateSdkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.storage = Path(cls.temporary.name) / "storage"
        cls.build = cls.storage / "build"
        cls.environment = {
            "KILIX_STORAGE_HOME": str(cls.storage),
            "KILIX_BUILD_DIRECTORY": str(cls.build),
        }
        environment = dict(os.environ, **cls.environment)
        result = subprocess.run(
            [str(ROOT / "scripts" / "build-state-library.sh"), "--print-path"],
            check=True, capture_output=True, text=True, env=environment)
        cls.library = Path(result.stdout.strip())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_host_build_is_private_and_loadable(self):
        self.assertTrue(self.library.is_file())
        self.assertEqual(stat.S_IMODE(self.library.parent.stat().st_mode), 0o700)
        with mock.patch.dict(os.environ, {
                **self.environment,
                "KILIX_STATE_LIBRARY": str(self.library)}):
            native = state.default_library()
            self.assertEqual(Path(native.path), self.library)

    def test_store_round_trip_uses_native_crc_record(self):
        with tempfile.TemporaryDirectory() as records:
            with mock.patch.dict(os.environ, {
                    **self.environment,
                    "KILIX_STATE_LIBRARY": str(self.library)}):
                store = state.Store(
                    absolute_path=Path(records) / "desktop.state",
                    max_payload=4096)
                value = {"schema_version": 1, "flavor": "95"}
                store.save(json.dumps(value).encode("utf-8"))
                self.assertEqual(json.loads(store.load()), value)
                self.assertEqual(store.path.read_bytes()[:4], b"KST1")
                self.assertEqual(
                    stat.S_IMODE(store.path.stat().st_mode), 0o600)
                store.close()

    def test_missing_host_build_has_actionable_error(self):
        missing = Path(self.temporary.name) / "missing" / "libkilix-state.so"
        with mock.patch.dict(os.environ, {
                "KILIX_STATE_LIBRARY": str(missing)}, clear=False):
            with self.assertRaises(state.LibraryNotFoundError) as caught:
                state.default_library()
        self.assertIn("build-state-library.sh", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
