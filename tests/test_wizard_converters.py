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
        with patch.object(converters.subprocess, 'run') as run:
            converters.prepare_converters(self.api, self.installer, [self.spec],
                                          input_fn=Mock(side_effect=AssertionError('unexpected consent')))
        run.assert_not_called()

    def test_missing_converter_previews_asks_runs_helper_and_resolves_again(self):
        events = []
        def resolve(*_):
            events.append('resolve')
            if events.count('resolve') == 1: raise self.error
        self.installer.resolve_converter.side_effect = resolve
        with patch.object(converters, 'checked_helper', return_value=self.helper), \
                patch.object(converters.subprocess, 'run', side_effect=lambda argv, **_: events.append(argv)):
            converters.prepare_converters(self.api, self.installer, [self.spec],
                input_fn=lambda _: events.append('consent') or 'yes')
        self.assertEqual(events, ['resolve', [self.helper, '--dry-run'], 'consent',
                                  ['/usr/bin/sudo', self.helper], 'resolve'])

    def test_decline_never_runs_sudo(self):
        self.installer.resolve_converter.side_effect = self.error
        with patch.object(converters, 'checked_helper', return_value=self.helper), \
                patch.object(converters.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'remain pending'): self.prepare('no')
        run.assert_called_once_with([self.helper, '--dry-run'], check=True)

    def test_helper_failures_and_false_success_stop_setup(self):
        for failure in ('preview', 'sudo', 'missing-after-success'):
            self.installer.resolve_converter.side_effect = self.error
            results = [None, None]
            if failure != 'missing-after-success':
                results[0 if failure == 'preview' else 1] = subprocess.CalledProcessError(1, 'helper')
            with self.subTest(failure=failure), \
                    patch.object(converters, 'checked_helper', return_value=self.helper), \
                    patch.object(converters.subprocess, 'run', side_effect=results):
                with self.assertRaisesRegex(ValueError, 'remain pending'):
                    self.prepare()

    def test_missing_or_untrusted_helper_never_runs_a_process(self):
        self.installer.resolve_converter.side_effect = self.error
        for error in (FileNotFoundError('missing'), ValueError('not root-controlled')):
            with self.subTest(error=error), \
                    patch.object(converters, 'checked_helper', side_effect=error), \
                    patch.object(converters.subprocess, 'run') as run:
                with self.assertRaises(ValueError): self.prepare()
                run.assert_not_called()

    def test_root_control_check_refuses_writable_owned_symlink_or_nonexecutable_helper(self):
        for uid, mode, executable in ((1000, stat.S_IFREG | 0o755, True),
                                     (0, stat.S_IFREG | 0o777, True),
                                     (0, stat.S_IFLNK | 0o777, True),
                                     (0, stat.S_IFREG | 0o644, False)):
            def info(path):
                return Mock(st_uid=uid if str(path) == self.helper else 0,
                            st_mode=mode if str(path) == self.helper else stat.S_IFDIR | 0o755)
            with self.subTest(uid=uid, mode=mode), patch.object(Path, 'lstat', info), \
                    patch.object(os, 'access', return_value=executable):
                with self.assertRaises(ValueError): converters.checked_helper(self.helper)

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
                patch.object(converters.subprocess, 'run') as run, \
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
