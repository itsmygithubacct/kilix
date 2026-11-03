"""Optional software setup is explicit, ordered and bounded to OS helpers."""
import contextlib
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import content_models as cm
import wizard_converters as converters
import model_wizard as wizard


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.api = cm._api()
        self.spec = cm._verified_catalog(self.api).require_asset('kilix-diarization-cpu')
        self.installer = Mock()
        self.error = self.api.ConverterPrerequisiteError(self.spec)
        self.helper = converters.OS_INSTALLERS[self.spec.convert_tool_asset_id]
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def prepare(self, answer='yes'):
        converters.prepare_converters(self.api, self.installer, [self.spec], input_fn=lambda _: answer)

    def test_present_converter_requires_no_prompt_or_process(self):
        with patch('subprocess.run') as run:
            converters.prepare_converters(self.api, self.installer, [self.spec],
                input_fn=Mock(side_effect=AssertionError('unexpected prompt')))
        run.assert_not_called()

    def test_missing_converter_does_not_prompt_or_elevate(self):
        self.installer.resolve_converter.side_effect = self.error
        with patch('subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'remain pending'):
                converters.prepare_converters(self.api, self.installer, [self.spec],
                    input_fn=Mock(side_effect=AssertionError('unexpected prompt')))
        run.assert_not_called()
        self.installer.resolve_converter.assert_called_once()

    def test_all_missing_catalog_tools_have_explicit_os_routes(self):
        catalog = cm._verified_catalog(self.api)
        missing = {spec.convert_tool_asset_id for spec in catalog.assets
                   if spec.source_mode == 'upstream-convert' and catalog.get(spec.convert_tool_asset_id) is None}
        self.assertEqual(missing, set(converters.OS_INSTALLERS))
        self.assertEqual(converters.OS_INSTALLERS['pocket-tts-convert-hf-to-gguf'],
                         '/usr/local/sbin/plebian-os-install-ollama-converter')

    def test_listing_and_staging_never_prepare_or_acquire_software_or_models(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(wizard, 'state_root', return_value=Path(directory)), \
                patch.object(wizard, 'sizing', return_value=({}, None, 'Not measured.')), \
                patch.object(converters, 'prepare_converters') as prepare, \
                patch('subprocess.run') as run, \
                patch('kilix_content.install.fetch_exact') as fetch, \
                patch.object(self.api.Installer, 'ensure') as ensure:
            self.assertEqual(wizard.main(['--json']), 0)
            self.assertEqual(wizard.main(['list']), 0)
            self.assertEqual(list(Path(directory).iterdir()), [])
            wizard.apply('kilix-transcribe', [self.spec.asset_id], 'yes')
            self.assertEqual(wizard.pending()['kilix-transcribe']['models'], [self.spec.asset_id])
        prepare.assert_not_called()
        run.assert_not_called()
        fetch.assert_not_called()
        ensure.assert_not_called()


if __name__ == '__main__': unittest.main()
