import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import default_models as defaults
import content_models as cm


class DefaultModelTests(unittest.TestCase):
    def test_selected_catalog_and_policy_cover_every_default(self):
        api, lic = cm._api(), cm._license()
        catalog, records = cm._verified_catalog(api), lic.load_determined_records()
        self.assertEqual(len(defaults.DEFAULT_MODELS), 9)
        self.assertTrue({'needle2', 'needle2-runtime', 'yolox_s'} <= set(defaults.DEFAULT_MODELS))
        self.assertNotIn('whisper-tiny-ggml', defaults.DEFAULT_MODELS)
        for name in defaults.DEFAULT_MODELS:
            spec = catalog.require_asset(name)
            for item in spec.licenses:
                self.assertTrue(lic.standard_policy_eligible(records.by_digest(item.record_digest)), name)

    def archive(self, directory, members):
        path = Path(directory) / 'bundle.tar.xz'
        with tarfile.open(path, 'w:xz') as stream:
            for name, payload, kind in members:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.size = len(payload)
                stream.addfile(info, io.BytesIO(payload))
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_archive_rejects_escape_links_duplicates_receipts_and_corruption(self):
        catalog = SimpleNamespace(require_asset=lambda _: SimpleNamespace(files=[
            SimpleNamespace(path='weights.bin', bytes=4)]))
        cases = [('escape', [('../escape', b'x', tarfile.REGTYPE)]),
                 ('link', [('model/weights.bin', b'', tarfile.SYMTYPE)]),
                 ('duplicate', [('manifest.json', b'{}', tarfile.REGTYPE)] * 2),
                 ('receipt', [('receipt.json', b'{}', tarfile.REGTYPE)]),
                 ('oversize', [('model/weights.bin', b'12345', tarfile.REGTYPE)])]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(defaults, 'DEFAULT_MODELS', ('model',)), \
                patch.object(cm, '_verified_catalog', return_value=catalog), \
                patch.object(defaults, 'populate') as populate:
            for name, members in cases:
                path, digest = self.archive(directory, members)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    defaults.install_archive(path, digest)
            with self.assertRaisesRegex(ValueError, 'checksum'):
                defaults.install_archive(path, '0' * 64)
            populate.assert_not_called()

    def test_bundle_requires_exact_catalog_manifest_before_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'manifest.json').write_text(json.dumps({'assets': {}}))
            with self.assertRaisesRegex(ValueError, 'differs'):
                defaults.populate(root)


if __name__ == '__main__':
    unittest.main()
