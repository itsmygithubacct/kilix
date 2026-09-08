"""An invalid optional bank must not break the rest of the install catalog."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import techno_soundbanks as banks


class ReceiptTests(unittest.TestCase):
    def test_invalid_receipts_are_not_ready_and_do_not_abort_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = banks.PACKS[0]
            target = root / pack["directory"]
            target.mkdir()
            receipt = target / ".kilix-bank"
            env = dict(os.environ, KILIX_TECHNO_SOUNDBANK_DIR=tmp)
            with mock.patch.dict(os.environ, env):
                for contents in ("[]", "null", "true", '"text"', "42", "{broken", "{}"):
                    with self.subTest(receipt=contents):
                        receipt.write_text(contents)
                        self.assertFalse(banks.ready(pack))
                receipt.write_text("[]")
                for args, expected in ((["--json"], 0), (["unknown-test-item"], 2)):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "config/install.py"), *args],
                        capture_output=True, text=True, env=env, timeout=20)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    if expected == 0:
                        self.assertTrue(any(row["id"] == pack["id"]
                                            for row in json.loads(result.stdout)))

    def test_valid_receipt_requires_matching_sample_bytes(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                os.environ, {"KILIX_TECHNO_SOUNDBANK_DIR": tmp}):
            pack = banks.PACKS[0]
            target = banks.directory(pack)
            target.mkdir()
            self.assertFalse(banks.ready(pack))
            data = b"sample fixture" * 8
            checksums = {}
            for name in banks.output_names(pack):
                (target / name).write_bytes(data)
                checksums[name] = hashlib.sha256(data).hexdigest()
            (target / ".kilix-bank").write_text(json.dumps({
                "schema": 1, "id": pack["id"], "files": checksums}))
            self.assertTrue(banks.ready(pack))
            (target / banks.output_names(pack)[0]).write_bytes(b"changed" * 8)
            self.assertFalse(banks.ready(pack))


if __name__ == "__main__":
    unittest.main()
