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


class EncodecTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        version = patch.object(converters, 'plebian_version', return_value=(0, 2, 4))
        version.start()
        self.addCleanup(version.stop)
        self.helper = self.root / 'helper'
        self.helper.touch()
        self.binary = self.root / 'driver'
        self.store = self.root / 'store'
        self.installer = SimpleNamespace(root=self.store)
        self.specs = [SimpleNamespace(asset_id=key, install_name=key, convert_argv=(self.binary,))
                      for key in sorted(converters.ENCODEC_MODELS)]
        for name, value in [('ENCODEC_HELPER', self.helper), ('ENCODEC_REQUESTED', self.root / 'requested')]:
            patcher = patch.object(converters, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_size_and_defer_do_not_create_store_or_start_installer(self):
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '239108136', '')) as run:
            self.assertEqual(converters.encodec_transfer(self.store, self.specs),
                             ([spec.asset_id for spec in self.specs], 239108136))
            question = Mock(return_value=False)
            with self.assertRaisesRegex(ValueError, 'deferred'):
                converters.prepare_encodec(self.installer, self.specs, question)
        self.assertIn('228.0 MiB', question.call_args.args[0])
        self.assertFalse(self.store.exists())
        self.assertTrue(all(call.args[0] == [str(self.helper), '--download-bytes'] for call in run.call_args_list))

    def test_both_profiles_get_one_fixed_privileged_install(self):
        def run(command, **options):
            if command == [str(self.helper), '--download-bytes']:
                return subprocess.CompletedProcess(command, 0, '239108136', '')
            self.assertEqual(command, ['/usr/bin/sudo', '--', str(self.helper)])
            self.assertTrue(options['check'])
            self.binary.touch(mode=0o700)
        ask = Mock(return_value=True)
        with patch('subprocess.run', side_effect=run) as called, patch('os.geteuid', return_value=1000):
            converters.prepare_encodec(self.installer, self.specs, ask)
        ask.assert_called_once()
        self.assertEqual(called.call_count, 2)

    def test_prepared_models_need_no_converter_and_active_converter_needs_no_download(self):
        for spec in self.specs:
            (self.store / 'assets' / spec.install_name).mkdir(parents=True)
        with patch('subprocess.run') as run:
            converters.prepare_encodec(self.installer, self.specs, Mock(side_effect=AssertionError))
            for spec in self.specs:
                (self.store / 'assets' / spec.install_name).rmdir()
            self.binary.touch(mode=0o700)
            converters.ENCODEC_REQUESTED.touch()
            converters.prepare_encodec(self.installer, self.specs, Mock(side_effect=AssertionError))
        run.assert_not_called()

    def test_earlier_os_preserves_its_existing_converter_policy(self):
        with patch.object(converters, 'plebian_version', return_value=(0, 2, 3)), \
                patch('subprocess.run') as run:
            converters.prepare_encodec(self.installer, self.specs, Mock(side_effect=AssertionError))
        run.assert_not_called()

    def test_unknown_size_fails_before_source_build_or_administrator_prompt(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('helper', 5)):
            with self.assertRaisesRegex(ValueError, 'on-demand'):
                converters.prepare_encodec(self.installer, self.specs, Mock(side_effect=AssertionError))


if __name__ == '__main__': unittest.main()
