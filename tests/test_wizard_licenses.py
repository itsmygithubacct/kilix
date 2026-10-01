"""Real determined licence records, private receipts and a non-fetching installer."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import content_models as cm
import wizard_licenses as batch
import system_llm


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='wizard-consent-test-')
        self.addCleanup(self.directory.cleanup)
        self.api = cm._api()
        self.lic = cm._license()
        self.store = self.lic.ReceiptStore(Path(self.directory.name) / 'receipts')
        self.installer = Mock()
        self.questions = []

    def run_batch(self, names, decisions):
        decisions = iter(decisions)
        def answer(question):
            self.questions.append(question)
            return next(decisions)
        output = io.TextIOWrapper(io.BytesIO(), encoding='utf-8', write_through=True)
        with patch.object(cm, '_api', return_value=self.api), \
                patch.object(self.lic.ReceiptStore, 'shared', return_value=self.store), \
                patch.object(self.api, 'Installer', return_value=self.installer), \
                patch.object(system_llm, 'license_texts', return_value={}), \
                contextlib.redirect_stdout(output):
            result = batch.consent_and_install(names, input_fn=answer)
            output.flush()
            self.output = output.buffer.getvalue()
        return result

    def test_one_question_names_both_models_and_keeps_separate_receipts(self):
        names = ['yolox_s', 'yolox_nano']
        self.assertEqual(self.run_batch(names, ['yes']), (set(names), set()))
        self.assertEqual(len(self.questions), 1)
        for name in names: self.assertIn(name, self.questions[0])
        self.assertIn('apache-2.0', self.questions[0])
        self.assertNotIn(b'type exactly', self.output)
        self.assertEqual(len(list(self.store.root.glob('*.json'))), 2)
        self.assertEqual(self.installer.ensure_upstream_asset.call_count, 2)
        catalog = cm._verified_catalog(self.api)
        records = self.lic.load_determined_records()
        for name in names:
            self.assertFalse(self.api.first_use.needs_agreement(
                catalog.require_asset(name), records=records, store=self.store))

    def test_apache_variants_share_one_question_and_keep_all_original_terms(self):
        names = ['yolox_s', 'bonsai-image-4b-ternary-gemlite']
        self.assertEqual(self.run_batch(names, ['yes']), (set(names), set()))
        self.assertEqual(len(self.questions), 1)
        for name in names: self.assertIn(name, self.questions[0])
        self.assertEqual(len(list(self.store.root.glob('*.json'))), 2)

    def test_retry_reuses_current_model_receipts_without_another_question(self):
        self.run_batch(['yolox_s'], ['yes'])
        self.questions.clear()
        self.assertEqual(self.run_batch(['yolox_s'], []), ({'yolox_s'}, set()))
        self.assertEqual(self.questions, [])
        self.assertEqual(len(list(self.store.root.glob('*.json'))), 1)

    def test_no_creates_no_receipts_and_downloads_nothing(self):
        names = ['yolox_s', 'yolox_nano']
        self.assertEqual(self.run_batch(names, ['no']), (set(), set(names)))
        self.assertEqual(list(self.store.root.glob('*.json')), [])
        self.installer.ensure_upstream_asset.assert_not_called()

    def test_interrupted_batch_does_not_mint_partial_receipts(self):
        with self.assertRaises(StopIteration):
            self.run_batch(['yolox_s', 'encodec-24khz-stateful'], ['yes'])
        self.assertEqual(list(self.store.root.glob('*.json')), [])
        self.installer.ensure_upstream_asset.assert_not_called()

    def test_one_declined_group_does_not_block_other_accepted_models(self):
        allowed, declined = self.run_batch(['yolox_s', 'encodec-24khz-stateful'], ['yes', 'no'])
        self.assertEqual(allowed, {'yolox_s'})
        self.assertEqual(declined, {'encodec-24khz-stateful'})
        self.installer.ensure_upstream_asset.assert_called_once()
        self.assertEqual(self.installer.ensure_upstream_asset.call_args.args[0].asset_id, 'yolox_s')


if __name__ == '__main__': unittest.main()
