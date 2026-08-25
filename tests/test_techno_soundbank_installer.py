"""Qualification for preserved WAV/OGG/SFZ/SF2 soundbank assets."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-kilix-techno-soundbank.py"
SPEC = importlib.util.spec_from_file_location("techno_bank_installer", SCRIPT)
bank_installer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bank_installer)


class PreservedAssetTests(unittest.TestCase):
    def test_nested_destination_is_bounded_below_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            target = bank_installer._asset_destination(
                stage, "Samples/Grand Piano/tone.wav")
            self.assertEqual(
                target, stage / "Samples" / "Grand Piano" / "tone.wav")
            self.assertTrue(target.parent.is_dir())
            for unsafe in ("/absolute.sf2", "../escape.wav", "a/../b.sfz",
                           "windows\\escape.ogg"):
                with self.subTest(unsafe=unsafe), \
                     self.assertRaises(bank_installer.InstallError):
                    bank_installer._asset_destination(stage, unsafe)

    def test_asset_mode_preserves_nested_bytes_and_metadata(self):
        payload = b"OggS" + bytes(range(60))
        pack = {
            "id": "fixture",
            "directory": "fixture",
            "label": "Fixture",
            "license": "CC0-1.0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "license_evidence": "https://example.invalid/evidence",
            "source": "https://example.invalid/source",
            "download_source": "https://example.invalid/distributor",
            "revision": "0123456789abcdef",
            "download_bytes": len(payload),
            "installed_bytes": len(payload),
            "mode": "assets",
            "raw_base": "https://example.invalid/raw/",
            "install_note": "Preserved fixture.",
            "files": (("samples/tone.ogg", "tone.ogg", len(payload),
                       "ignored-by-mock"),),
        }

        def fake_download(_url, expected_size, _digest, destination):
            self.assertEqual(expected_size, len(payload))
            destination.write_bytes(payload)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            stage = root / "stage"
            work.mkdir()
            stage.mkdir()
            with mock.patch.object(bank_installer, "_download",
                                   side_effect=fake_download):
                bank_installer._assets(pack, work, stage)
            self.assertEqual((stage / "samples/tone.ogg").read_bytes(),
                             payload)
            bank_installer._write_metadata(pack, stage)
            receipt = json.loads((stage / ".kilix-bank").read_text(
                encoding="utf-8"))
            self.assertEqual(receipt["license_evidence"],
                             pack["license_evidence"])
            self.assertIn("samples/tone.ogg", receipt["files"])


if __name__ == "__main__":
    unittest.main()
