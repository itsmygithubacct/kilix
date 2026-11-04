"""Unpublished Needle source is selected through Content's verified carrier."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'wsl'
sys.path.insert(0, str(SOURCE))
import catalog_sources


class NeedleCarrierTests(unittest.TestCase):
    def test_selected_needle_and_changed_dependency_match_reviewed_source(self):
        pins = catalog_sources.revisions(['needle'])
        self.assertEqual(pins['needle'], '0a936d30893c3063cf0ad36d07bbcee12c522ee5')
        self.assertEqual(pins['needle/third_party/kilix-ml'], 'aa3489323ee74dc1661aa604e7145c5e14bcf500')
        self.assertEqual(pins['needle/third_party/kilix-tui-utils'], '860c0db951e3da59173d292217f5a2908cdb785e')
        self.assertNotIn('kilix-needle/' + pins['needle'], json.loads((SOURCE/'source-archives.json').read_text()))

    def test_exact_repository_revision_and_destination_reach_packaged_engine(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(catalog_sources, '_carrier_engine') as loader:
            destination = Path(temp)/'needle'
            self.assertTrue(catalog_sources._needle_carrier(destination, 'a'*40))
            loader.return_value.assert_called_once_with('https://github.com/itsmygithubacct/kilix-needle.git', 'a'*40, destination)

    def test_complete_needle_closure_skips_public_archive_fallback(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(catalog_sources, '_needle_carrier', return_value=True) as acquire, \
                patch.object(catalog_sources, 'download', side_effect=AssertionError('network fallback')):
            root = Path(temp)
            pins = catalog_sources.materialize(['needle'], root/'source', root/'cache')
            acquire.assert_called_once_with(root/'source/needle', pins['needle'])
            self.assertEqual(len(pins), 5)

    def test_missing_or_rejected_carrier_does_not_download_unpublished_needle(self):
        for error in (None, ValueError('carrier digest mismatch')):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temp, \
                    patch.object(catalog_sources, '_needle_carrier', return_value=False, side_effect=error), \
                    patch.object(catalog_sources, 'download', side_effect=AssertionError('network fallback')):
                with self.assertRaises(ValueError):
                    catalog_sources.materialize(['needle'], Path(temp)/'source', Path(temp)/'cache')

    def test_combined_app_and_needle_carriers_skip_both_complete_closures(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(catalog_sources, '_needle_carrier', return_value=True) as needle, \
                patch.object(catalog_sources, '_playalong_carrier', return_value=True) as app, \
                patch.object(catalog_sources, 'download', side_effect=AssertionError('network fallback')):
            pins = catalog_sources.materialize(['needle', 'apps/kilix-playalong'], Path(temp)/'source', Path(temp)/'cache')
            self.assertIn('needle', pins)
            self.assertIn('apps/kilix-playalong', pins)
            needle.assert_called_once(); app.assert_called_once()


if __name__ == '__main__':
    unittest.main()
