"""Real determined licence records, private receipts and a non-fetching installer."""
import contextlib
import io
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import content_models as cm
import wizard_licenses as batch
import system_llm
import model_wizard
import wizard_converters


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='wizard-consent-test-')
        self.addCleanup(self.directory.cleanup)
        self.api = cm._api()
        self.lic = cm._license()
        self.store = self.lic.ReceiptStore(Path(self.directory.name) / 'receipts')
        self.installer = Mock()
        self.questions = []

    def run_batch(self, names, decisions, llm_entries=None, *, finish=False, acquire=None,
                  llm_error=None):
        decisions = iter(decisions)
        def answer(question):
            self.questions.append(question)
            return next(decisions)
        output = io.TextIOWrapper(io.BytesIO(), encoding='utf-8', write_through=True)
        with patch.object(cm, '_api', return_value=self.api), \
                patch.object(self.lic.ReceiptStore, 'shared', return_value=self.store), \
                patch.object(self.lic, 'observe_capture', return_value=self.lic.observe_capture(interactive=True)), \
                patch.object(self.api, 'Installer', return_value=self.installer), \
                patch.object(system_llm, 'license_texts', return_value=llm_entries or {},
                             side_effect=llm_error), \
                patch.object(system_llm, 'acquire', side_effect=acquire), \
                patch.object(model_wizard, 'state_root', return_value=Path(self.directory.name)), \
                contextlib.redirect_stdout(output):
            try:
                result = (model_wizard.finish(input_fn=answer) if finish else
                          batch.consent_and_install(names, input_fn=answer))
            finally:
                # Failing batches raise; their output is still evidence.
                output.flush()
                self.output = output.buffer.getvalue()
        return result

    def test_standard_licenses_install_without_prompt_and_keep_documents(self):
        names = ['yolox_s', 'yolox_nano', 'needle2', 'needle2-runtime', 'needle3-kilix-commands']
        self.assertEqual(self.run_batch(names, []), (set(names), set()))
        self.assertEqual(self.questions, [])
        self.assertEqual(len(list(self.store.root.glob('*.json'))), len(names))
        import json
        for name in names:
            index = json.loads((Path(self.directory.name) / 'license-documents' / (name + '.json')).read_text())
            for document in index['documents']:
                self.assertTrue(Path(document['path']).is_file())
                self.assertTrue(document['url'].startswith('file:'))
        for path in self.store.root.glob('*.json'):
            self.assertIn('standard-license-policy-v1',
                          json.loads(path.read_text())['context']['statement_digests'])

    def test_retry_reuses_current_model_receipts_without_another_question(self):
        self.run_batch(['yolox_s'], ['yes'])
        self.questions.clear()
        self.assertEqual(self.run_batch(['yolox_s'], []), ({'yolox_s'}, set()))
        self.assertEqual(self.questions, [])
        self.assertEqual(len(list(self.store.root.glob('*.json'))), 1)

    def test_no_creates_no_receipts_and_downloads_nothing(self):
        names = ['encodec-24khz-stateful']
        self.assertEqual(self.run_batch(names, ['no']), (set(), set(names)))
        self.assertEqual(list(self.store.root.glob('*.json')), [])
        self.installer.ensure_upstream_asset.assert_not_called()

    def test_interrupted_batch_does_not_mint_partial_receipts(self):
        with self.assertRaises(StopIteration):
            self.run_batch(['yolox_s', 'encodec-24khz-stateful'], [])
        self.assertEqual(list(self.store.root.glob('*.json')), [])
        self.installer.ensure_upstream_asset.assert_not_called()

    def test_one_declined_group_does_not_block_other_accepted_models(self):
        allowed, declined = self.run_batch(['yolox_s', 'encodec-24khz-stateful'], ['no'])
        self.assertEqual(allowed, {'yolox_s'})
        self.assertEqual(declined, {'encodec-24khz-stateful'})
        self.installer.ensure_upstream_asset.assert_called_once()
        self.assertEqual(self.installer.ensure_upstream_asset.call_args.args[0].asset_id, 'yolox_s')

    def test_kilix_llm_gguf_is_acquired_through_content_not_ollama(self):
        # Its licence records come from the catalog asset, as for kilix-female-02.
        name = 'qwen-0.8b-no-loop-02'
        spec = Mock(asset_id=name, licenses=(), manifest_digest='0' * 64,
                    source_mode='upstream-files')
        catalog = Mock()
        catalog.require_asset.return_value = spec
        with patch.object(cm, '_verified_catalog', return_value=catalog):
            self.assertEqual(self.run_batch([name], [], acquire=AssertionError('Ollama path')),
                             ({name}, set()))
        catalog.require_asset.assert_called_once_with(name)
        self.installer.ensure_upstream_asset.assert_called_once()
        self.assertIs(self.installer.ensure_upstream_asset.call_args.args[0], spec)

    def test_selected_catalog_installs_the_no_loop_gguf_under_standard_policy(self):
        # The selected kilix-content's own record: Apache-2.0 and CC BY 4.0
        # rows, both approved by the owner, with no typed answer, as for
        # kilix-female-02.
        name = 'qwen-0.8b-no-loop-02'
        spec = cm._verified_catalog(self.api).require_asset(name)
        self.assertEqual((spec.provider, spec.source_mode), ('kilix-llm', 'upstream-files'))
        self.assertEqual(sorted(item.license_id for item in spec.licenses),
                         ['apache-2.0', 'cc-by-4.0'])
        self.assertEqual(self.run_batch([name], []), ({name}, set()))
        self.assertEqual(self.questions, [])
        self.installer.ensure_upstream_asset.assert_called_once()
        self.assertEqual(self.installer.ensure_upstream_asset.call_args.args[0].asset_id, name)
        import json
        receipts = [json.loads(path.read_text()) for path in self.store.root.glob('*.json')]
        self.assertTrue(receipts)
        for receipt in receipts:
            self.assertIn('standard-license-policy-v1', receipt['context']['statement_digests'])
        index = json.loads((Path(self.directory.name) / 'license-documents' / (name + '.json')).read_text())
        labels = {document['label'] for document in index['documents']}
        self.assertTrue({'licence:apache-2.0', 'licence:cc-by-4.0'} <= labels, labels)

    def test_download_failure_names_model_and_preserves_the_original_cause(self):
        self.installer.ensure_upstream_asset.side_effect = FileNotFoundError('/missing/converter')
        with self.assertRaisesRegex(RuntimeError, 'Could not install yolox_s') as caught:
            self.run_batch(['yolox_s'], ['yes'])
        self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
        self.assertEqual(len(list(self.store.root.glob('*.json'))), 1)

    def test_model_failure_keeps_prior_success_and_attempts_later_accepted_models(self):
        names = ['yolox_s', 'qwen3:8b', 'yamnet']
        cause = ValueError('Local Ollama backend unavailable')
        with self.assertRaises(batch.InstallFailures) as caught:
            self.run_batch(names, ['yes'], {'qwen3:8b': [('apache-2.0', b'Original terms')]},
                           acquire=cause)
        self.assertEqual(caught.exception.installed, {'yolox_s', 'yamnet'})
        self.assertEqual(caught.exception.declined, set())
        self.assertEqual(caught.exception.failures, {'qwen3:8b': cause})
        self.assertIs(caught.exception.__cause__, cause)
        self.assertEqual([call.args[0].asset_id for call in self.installer.ensure_upstream_asset.call_args_list],
                         ['yolox_s', 'yamnet'])

    def test_unreachable_llm_registry_fails_that_model_and_installs_the_rest(self):
        # Offline, the Ollama manifest fetch raised before any question and
        # cancelled every other selected model.
        cause = OSError('Temporary failure in name resolution')
        acquire = Mock()
        with self.assertRaises(batch.InstallFailures) as caught:
            self.run_batch(['yolox_s', 'qwen3:8b', 'yamnet'], [], llm_error=cause, acquire=acquire)
        self.assertEqual(caught.exception.installed, {'yolox_s', 'yamnet'})
        self.assertEqual(caught.exception.declined, set())
        self.assertEqual(caught.exception.failures, {'qwen3:8b': cause})
        acquire.assert_not_called()
        self.assertIn('Could not read the licence for qwen3:8b: Temporary failure in name resolution',
                      self.output.decode())

    def test_remaining_questions_come_after_licences_and_before_downloads(self):
        # The dictation microphone question: never for a refused model, and
        # never after the downloads, where an unattended setup waited for it.
        events = []
        self.installer.ensure_upstream_asset.side_effect = (
            lambda spec, **_kwargs: events.append(('download', spec.asset_id)))
        def before_install(allowed):
            events.append(('questions', frozenset(allowed), len(self.questions)))
        decisions = iter(['no'])
        def answer(question):
            self.questions.append(question)
            return next(decisions)
        output = io.TextIOWrapper(io.BytesIO(), encoding='utf-8', write_through=True)
        with patch.object(cm, '_api', return_value=self.api), \
                patch.object(self.lic.ReceiptStore, 'shared', return_value=self.store), \
                patch.object(self.lic, 'observe_capture', return_value=self.lic.observe_capture(interactive=True)), \
                patch.object(self.api, 'Installer', return_value=self.installer), \
                patch.object(model_wizard, 'state_root', return_value=Path(self.directory.name)), \
                contextlib.redirect_stdout(output):
            result = batch.consent_and_install(['faster-whisper-small-en', 'yolox_s', 'encodec-24khz-stateful'],
                                               input_fn=answer, before_install=before_install)
        allowed = {'faster-whisper-small-en', 'yolox_s'}
        self.assertEqual(result, (allowed, {'encodec-24khz-stateful'}))
        self.assertEqual(len(self.questions), 1)
        self.assertEqual(events[0], ('questions', frozenset(allowed), 1))
        self.assertEqual(sorted(event[1] for event in events[1:]), sorted(allowed))

    def test_each_installed_model_reports_its_outcome(self):
        # Sixteen bare "Installing X…" lines could not show which had finished.
        self.run_batch(['yolox_s', 'yamnet'], [])
        text = self.output.decode()
        for name in ('yolox_s', 'yamnet'):
            self.assertIn(f'Installed {name}.', text)
        self.assertLess(text.index('Installed yolox_s.'), text.index('Installing yamnet'))

    def test_failed_model_reason_is_printed_before_later_models(self):
        # The owner's setup terminal stopped at a later question with only
        # "Could not install qwen3:8b" on screen; the reason must come first.
        cause = ValueError('The local Ollama backend at 127.0.0.1:11434 is unavailable.')
        with self.assertRaises(batch.InstallFailures):
            self.run_batch(['qwen3:8b', 'yamnet'], ['yes'],
                           {'qwen3:8b': [('apache-2.0', b'Original terms')]}, acquire=cause)
        text = self.output.decode()
        reason = 'Could not install qwen3:8b: The local Ollama backend at 127.0.0.1:11434 is unavailable.'
        self.assertIn(reason, text)
        self.assertLess(text.index(reason), text.index('Installing yamnet'))

    def test_partial_finish_retry_skips_completed_pages_and_reuses_exact_licences(self):
        name = 'qwen3:8b'
        names = ['yolox_s', name, 'yamnet']
        catalog = cm._verified_catalog(self.api)
        rows = [dict(id=spec.asset_id, label=spec.label, provider=spec.provider,
                     download_bytes=spec.download_bytes, installed_bytes=spec.installed_bytes,
                     temporary_bytes=spec.temporary_bytes)
                for spec in (catalog.require_asset('yolox_s'), catalog.require_asset('yamnet'))]
        rows += system_llm.catalog()
        terms = {name: [('apache-2.0', b'Original terms')]}
        with patch.object(model_wizard, 'state_root', return_value=Path(self.directory.name)), \
                patch.object(model_wizard, 'catalog', return_value=rows):
            model_wizard.apply('vision', ['yolox_s'], 'yes')
            model_wizard.apply('system-local-llm', [name], 'yes')
            model_wizard.apply('sound', ['yamnet'], 'yes')
            with self.assertRaises(model_wizard.SetupIncomplete):
                self.run_batch(names, ['yes'], terms, finish=True,
                               acquire=ValueError('Local Ollama backend unavailable'))
            self.assertEqual(set(model_wizard.pending()), {'system-local-llm'})
            self.assertEqual(self.installer.ensure_upstream_asset.call_count, 2)
            receipts = {path.name: path.read_bytes() for path in self.store.root.glob('*.json')}
            llm_receipt = Path(self.directory.name) / 'model-wizard-llm-licenses.json'
            old_llm_receipt = llm_receipt.read_bytes()
            self.questions.clear()
            self.run_batch([name], [], terms, finish=True)
            self.assertEqual(model_wizard.pending(), {})
            self.assertEqual(self.questions, [])
            self.assertEqual(self.installer.ensure_upstream_asset.call_count, 2)
            self.assertEqual(receipts, {path.name: path.read_bytes() for path in self.store.root.glob('*.json')})
            self.assertEqual(llm_receipt.read_bytes(), old_llm_receipt)

    def test_llm_retry_reuses_and_preserves_exact_prior_acceptance(self):
        name = 'qwen3:4b-instruct-2507-q4_K_M'
        entries = {name: [('apache-2.0', b'Original terms')]}
        self.run_batch([name], ['yes'], entries)
        saved = Path(self.directory.name) / 'model-wizard-llm-licenses.json'
        original = saved.read_bytes()
        self.questions.clear()
        self.assertEqual(self.run_batch([name], [], entries), ({name}, set()))
        self.assertEqual(self.questions, [])
        self.assertEqual(saved.read_bytes(), original)

    def test_changed_llm_terms_require_new_answer(self):
        name = 'qwen3:4b-instruct-2507-q4_K_M'
        self.run_batch([name], ['yes'], {name: [('apache-2.0', b'Original terms')]})
        self.questions.clear()
        self.assertEqual(self.run_batch([name], ['no'],
            {name: [('apache-2.0', b'Changed terms')]}), (set(), {name}))
        self.assertEqual(len(self.questions), 1)

    def test_install_error_names_model_and_reaches_wizard_error_handler(self):
        self.installer.ensure_upstream_asset.side_effect = self.api.InstallError('converter missing')
        with self.assertRaisesRegex(RuntimeError, 'Could not install yolox_s') as caught:
            self.run_batch(['yolox_s'], ['yes'])
        self.assertIsInstance(caught.exception.__cause__, self.api.InstallError)
        self.assertEqual(str(caught.exception.__cause__), 'converter missing')

    def test_download_error_reaches_wizard_error_handler(self):
        original = self.api.DownloadError('size-mismatch')
        self.installer.ensure_upstream_asset.side_effect = original
        with self.assertRaisesRegex(RuntimeError, 'Could not install yolox_s') as caught:
            self.run_batch(['yolox_s'], ['yes'])
        self.assertIsInstance(caught.exception.__cause__, self.api.DownloadError)
        self.assertIs(caught.exception.__cause__, original)


    def test_diarization_prerequisite_failure_preserves_other_choices_and_retries(self):
        catalog = cm._verified_catalog(self.api)
        spec = catalog.require_asset('kilix-diarization-cpu')
        real = self.api.Installer(str(Path(self.directory.name) / 'apps'))
        self.installer.resolve_converter.side_effect = real.resolve_converter
        names = ['yolox_nano', spec.asset_id]
        with patch.object(model_wizard, 'state_root', return_value=Path(self.directory.name)), \
                patch('kilix_content.install.shutil.which', return_value=None), \
                patch('subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'remain pending'):
                self.run_batch(names, ['yes'])
            self.installer.ensure_upstream_asset.assert_not_called()
            run.assert_not_called()
            receipts = {path.name: path.read_bytes() for path in self.store.root.glob('*.json')}
            self.assertEqual(len(receipts), 2)
            self.questions.clear()
            # The OS installer repaired the prerequisite outside the wizard.
            self.installer.resolve_converter.side_effect = None
            self.run_batch(names, [])
            self.assertEqual(self.questions, [])
            self.assertEqual([call.args[0].asset_id for call in self.installer.ensure_upstream_asset.call_args_list], names)
            self.assertEqual(receipts, {path.name: path.read_bytes() for path in self.store.root.glob('*.json')})

    def test_declining_diarization_licence_never_prepares_software(self):
        with patch('subprocess.run') as run:
            self.assertEqual(self.run_batch(['kilix-diarization-cpu'], ['no']),
                             (set(), {'kilix-diarization-cpu'}))
        run.assert_not_called()
        self.installer.resolve_converter.assert_not_called()
        self.installer.ensure_upstream_asset.assert_not_called()
        self.assertEqual(list(self.store.root.glob('*.json')), [])


if __name__ == '__main__': unittest.main()
