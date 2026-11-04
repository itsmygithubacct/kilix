"""Playalong's WSL adapter delegates source trust to Content's carrier."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'wsl'
sys.path.insert(0, str(SOURCE))
import catalog_sources


class PlayalongCarrierTests(unittest.TestCase):
    def test_exact_accepted_app_and_nested_pins_are_selected(self):
        pins = catalog_sources.revisions(['apps/kilix-playalong'])
        self.assertEqual(pins['apps/kilix-playalong'], 'a63d83e7a99f82cfcae31e1b5b0b0a51d903e67e')
        self.assertEqual(pins['apps/kilix-playalong/third_party/kitty-terminal-session'],
                         '0bd98cacef9ec12f021ecf9db1828579970b702a')
        self.assertEqual(pins['apps/kilix-playalong/third_party/kitty-terminal-session/third_party/kitty-input'],
                         '2b36dd1c93a0c2b9f1378f6a380c7ffa5f877814')
        hashes = json.loads((SOURCE / 'source-archives.json').read_text())
        self.assertNotIn('kilix-playalong/' + pins['apps/kilix-playalong'], hashes)

    def test_verified_carrier_supplies_complete_closure_without_download(self):
        pins = catalog_sources.revisions(['apps/kilix-playalong'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(catalog_sources, '_playalong_carrier', return_value=True) as carrier, \
                 patch.object(catalog_sources, 'download', side_effect=AssertionError('unexpected fetch')):
                self.assertEqual(catalog_sources.materialize(['apps/kilix-playalong'], root/'source', root/'cache'), pins)
            carrier.assert_called_once_with(root/'source/apps/kilix-playalong', pins['apps/kilix-playalong'])

    def test_missing_carrier_refuses_unavailable_public_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(catalog_sources, '_playalong_carrier', return_value=False), \
                 patch.object(catalog_sources, 'download', side_effect=AssertionError('unexpected fetch')):
                with self.assertRaisesRegex(ValueError, 'requires a configured verified source carrier'):
                    catalog_sources.materialize(['apps/kilix-playalong'], root/'source', root/'cache')

    def test_carrier_refusal_never_falls_back_to_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(catalog_sources, '_playalong_carrier', side_effect=ValueError('changed carrier')), \
                 patch.object(catalog_sources, 'download', side_effect=AssertionError('unexpected fetch')):
                with self.assertRaisesRegex(ValueError, 'changed carrier'):
                    catalog_sources.materialize(['apps/kilix-playalong'], root/'source', root/'cache')

    def test_configured_carrier_without_engine_refuses(self):
        with patch.dict(os.environ, {'KILIX_SOURCE_CARRIER_MANIFEST': '/fixture/manifest'}, clear=True), \
             patch.dict(sys.modules, {'kilix_content': None}):
            with self.assertRaisesRegex(ValueError, 'requires the Content source-carrier engine'):
                catalog_sources._playalong_carrier(Path('/fixture/destination'), 'a'*40)


if __name__ == '__main__':
    unittest.main()
