"""Wizard choices persist across interfaces; only checked models are acquired."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import model_wizard as wizard
import system_llm


def asset(name, provider='test'):
    return dict(id=name, label=name, provider=provider, download_bytes=10,
                installed_bytes=20, temporary_bytes=30)


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, KILIX95_STATE_HOME=self.directory.name)
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.assets = [asset('yolox_s'), asset('yolox_nano'), asset('encodec-24khz-stateful'),
                       asset('encodec-48khz-frame'), asset('bonsai-image-4b-ternary-gemlite'),
                       asset('bonsai-image-4b-binary-gemlite')]
        self.catalog = patch.object(wizard, 'catalog', return_value=self.assets)
        self.catalog.start(); self.addCleanup(self.catalog.stop)

    def test_all_types_and_defaults_without_writes(self):
        pages = wizard.pages(measure=False)
        self.assertEqual([p['id'] for p in pages], ['vision', 'audio', 'image'])
        self.assertEqual(pages[0]['default'], 'yolox_s')
        self.assertEqual(len(pages[2]['models']), 2)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_accept_only_checked_alternate_and_skip_answered(self):
        with patch.object(wizard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), \
                patch.object(wizard, 'run_command') as run:
            wizard.apply('vision', ['yolox_nano'], 'yes')
        run.assert_not_called()
        self.assertEqual(wizard.pending()['vision']['models'], ['yolox_nano'])
        self.assertEqual(wizard.answers()['vision']['models'], ['yolox_nano'])
        self.assertEqual([p['id'] for p in wizard.pages(measure=False)], ['audio', 'image'])
        self.assertEqual(len(wizard.pages(True, measure=False)), 3)

    def test_decline_never_invokes_installer(self):
        with patch.object(wizard.subprocess, 'run') as run:
            wizard.apply('audio', [], 'no')
        run.assert_not_called()
        self.assertNotIn('audio', [p['id'] for p in wizard.pages(measure=False)])

    def test_failure_keeps_saved_choices_pending_for_final_retry(self):
        wizard.apply('image', [self.assets[-1]['id']], 'yes')
        with patch('wizard_licenses.consent_and_install', side_effect=ValueError('download failed')):
            with self.assertRaises(ValueError): wizard.finish()
        self.assertIn('image', wizard.pending())
        self.assertNotIn('image', [p['id'] for p in wizard.pages(measure=False)])

    def test_final_batch_commits_accepted_and_declined_model_results(self):
        wizard.apply('vision', ['yolox_s', 'yolox_nano'], 'yes')
        wizard.apply('audio', ['encodec-24khz-stateful'], 'yes')
        with patch('wizard_licenses.consent_and_install', return_value=(
                {'yolox_nano'}, {'yolox_s', 'encodec-24khz-stateful'})) as batch:
            wizard.finish()
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(wizard.answers()['vision']['models'], ['yolox_nano'])
        self.assertEqual(wizard.answers()['audio']['answer'], 'no')
        self.assertEqual(wizard.pending(), {})

    def test_invalid_cross_page_and_empty_selection(self):
        for models in ([], ['encodec-24khz-stateful'], ['--malformed']):
            with self.assertRaises(ValueError): wizard.apply('vision', models, 'yes')
        self.assertEqual(wizard.answers(), {})

    def test_lease_prevents_competing_terminal(self):
        with wizard.exclusive():
            with self.assertRaisesRegex(ValueError, 'already running'):
                wizard.apply('vision', [], 'no')

    def test_terminal_quit_keeps_remaining_pages(self):
        replies = iter(['d', 'q'])
        self.assertEqual(wizard.interactive(input_fn=lambda _: next(replies)), 0)
        self.assertEqual(wizard.answers()['vision']['answer'], 'no')
        self.assertNotIn('audio', wizard.answers())

    def test_legacy_yes_and_no_migrate_but_offered_is_not_an_answer(self):
        for name in wizard.LEGACY.values(): (Path(self.directory.name) / name).touch()
        values = {'speech': {'offered': True, 'enabled': False},
                  'dictation': {'answer': 'no'}, 'workflows': {'answer': 'yes'}}
        with patch.object(wizard, 'legacy_state', side_effect=lambda key: values[key]):
            answers = wizard.answers()
        self.assertNotIn('speech', answers)
        self.assertEqual(answers['dictation']['answer'], 'no')
        self.assertEqual(answers['workflows']['answer'], 'yes')

    def test_system_llm_never_defaults_without_sizer_fit(self):
        with patch.object(wizard, 'catalog', return_value=system_llm.catalog()), \
                patch.object(system_llm, 'recommend', return_value=({}, None, 'unavailable')):
            page = wizard.pages()[0]
        self.assertEqual(page['id'], 'system-local-llm')
        self.assertIsNone(page['default'])

    def test_system_llm_sizer_choice_is_prechecked(self):
        model = list(system_llm.MODELS)[1]
        with patch.object(wizard, 'catalog', return_value=system_llm.catalog()), \
                patch.object(system_llm, 'recommend', return_value=({}, model, 'live estimate')):
            page = wizard.pages()[0]
        self.assertEqual(page['default'], model)

    def test_runtime_alternate_recommendation_and_unknown_fit_defaults(self):
        reports = {'yolox_s': {'verdict': 'does-not-fit', 'required_ram_bytes': 300000000},
                   'yolox_nano': {'verdict': 'estimated-fit', 'required_ram_bytes': 120000000}}
        with patch.object(wizard, 'sizing', return_value=(reports, 'yolox_nano', 'reference memory')):
            page = wizard.pages()[0]
        self.assertEqual(page['default'], 'yolox_nano')
        self.assertEqual(page['models'][1]['ram_bytes'], 120000000)
        with patch.object(wizard, 'sizing', return_value=(reports, None, 'unknown')):
            page = wizard.pages()[0]
        self.assertIsNone(page['default'])
        self.assertIn('No confirmed fit', page['recommendation'])

    def test_runtime_report_is_bound_to_request_and_checked_choice(self):
        rows = [dict(self.assets[0], manifest_digest='a' * 64)]
        def run(command, *, input, stdout, **kwargs):
            import hashlib
            request = json.loads(input)
            self.assertIn('runtime', command)
            self.assertNotIn('--task', command)
            report = {'schema': 'plebian.models.runtime-sizing/v1-development',
                      'resource_source': 'live', 'request_sha256': hashlib.sha256(input).hexdigest(),
                      'selected_model': None, 'qualification_eligible': False,
                      'defaults': {'vision': 'yolox_s'},
                      'candidates': [dict(request['models'][0], verdict='estimated-fit', backend='cpu',
                                          required_ram_bytes=120000000, required_vram_bytes=None,
                                          inference={'resources': {'ram': {'required_bytes': 120000000,
                                                      'budget_bytes': 240000000, 'status': 'estimated-fit'}}})]}
            stdout.write(json.dumps(report).encode())
            return subprocess.CompletedProcess(command, 0)
        with patch.object(system_llm, 'sizer_executable', return_value='/sizer'), \
                patch.object(wizard.subprocess, 'run', side_effect=run):
            reports, choice, _ = wizard.sizing(rows, 'vision')
        self.assertEqual(choice, 'yolox_s')
        self.assertEqual(reports['yolox_s']['manifest_digest'], 'a' * 64)

    def test_speech_sizing_marks_every_provider_served_qwen_model_supported(self):
        names = ('piper-en-us-kristin-medium', 'qwen3-tts-0.6b-customvoice', 'qwen3-tts-0.6b-base',
                 'qwen3-tts-1.7b-voicedesign', 'qwen3-tts-1.7b-base', 'pocket-tts-en-alba')
        rows = [asset(name) for name in names]
        requests = []
        def run(command, *, input, stdout, **kwargs):
            requests.append(json.loads(input))
            return subprocess.CompletedProcess(command, 1)
        with patch.object(system_llm, 'sizer_executable', return_value='/sizer'), \
                patch.object(wizard.subprocess, 'run', side_effect=run):
            wizard.sizing(rows, 'speech')
        supported = {row['id']: row['runtime_supported'] for row in requests[0]['models']}
        self.assertEqual(supported, {'piper-en-us-kristin-medium': True, 'qwen3-tts-0.6b-customvoice': True,
                                     'qwen3-tts-0.6b-base': True, 'qwen3-tts-1.7b-voicedesign': True,
                                     'qwen3-tts-1.7b-base': False, 'pocket-tts-en-alba': False})

    def test_malformed_runtime_reports_fall_back_without_memory_claims(self):
        rows = [dict(self.assets[0], manifest_digest='a' * 64)]
        for defect in ('object', 'ram', 'vram', 'budget', 'qualified', 'selected', 'duplicate',
                       'backend', 'missing-backend', 'unknown-inference', 'unknown-resources', 'unknown-check'):
            def run(command, *, input, stdout, **kwargs):
                import hashlib
                candidate = dict(json.loads(input)['models'][0], verdict='estimated-fit', backend='cpu',
                                 required_ram_bytes=120000000, required_vram_bytes=None,
                                 inference={'resources': {'ram': {'required_bytes': 120000000,
                                            'budget_bytes': 240000000, 'status': 'estimated-fit'}}})
                report = {'schema': 'plebian.models.runtime-sizing/v1-development', 'resource_source': 'live',
                          'request_sha256': hashlib.sha256(input).hexdigest(), 'selected_model': None,
                          'qualification_eligible': False, 'candidates': [candidate], 'defaults': {'vision': 'yolox_s'}}
                if defect == 'object': report = []
                elif defect == 'ram': candidate['required_ram_bytes'] = 'broken'
                elif defect == 'vram': candidate['required_vram_bytes'] = -1
                elif defect == 'budget': candidate['inference']['resources']['ram']['budget_bytes'] = True
                elif defect == 'qualified': report['qualification_eligible'] = True
                elif defect == 'selected': report['selected_model'] = 'yolox_s'
                elif defect == 'duplicate': report['candidates'] *= 2
                elif defect == 'backend': candidate['backend'] = 'unrecognized-gpu'
                elif defect == 'missing-backend': candidate.pop('backend')
                elif defect.startswith('unknown-'):
                    candidate.update(verdict='unknown', required_ram_bytes=None, required_vram_bytes=None)
                    report['defaults']['vision'] = None
                    candidate['inference'] = (['bad'] if defect == 'unknown-inference' else
                                               {'resources': ['bad']} if defect == 'unknown-resources' else
                                               {'resources': {'ram': ['bad']}})
                stdout.write(json.dumps(report).encode())
                return subprocess.CompletedProcess(command, 0)
            with self.subTest(defect=defect), patch.object(system_llm, 'sizer_executable', return_value='/sizer'), \
                    patch.object(wizard.subprocess, 'run', side_effect=run):
                reports, choice, note = wizard.sizing(rows, 'vision')
            self.assertEqual(reports, {})
            self.assertIsNone(choice)
            self.assertIn('unavailable', note)

    def test_memory_text_includes_vram_and_unknown_fit(self):
        self.assertEqual(wizard.memory_text({'ram_bytes': None, 'vram_bytes': 1024**3,
                                            'fit': 'unknown'}), 'VRAM 1.00 GiB; unknown')

    def test_no_acquisition_on_nonterminal_accept(self):
        with patch.object(wizard.sys.stdin, 'isatty', return_value=False), patch.object(wizard, 'apply') as apply:
            self.assertEqual(wizard.main(['finish']), 1)
        apply.assert_not_called()

    def test_existing_llm_collision_is_not_overwritten(self):
        model = next(iter(system_llm.MODELS))
        with patch.object(system_llm, 'installed', return_value={model: {'digest': 'wrong', 'size': 1}}), \
                patch.object(system_llm.subprocess, 'call') as call:
            with self.assertRaisesRegex(ValueError, 'different identity'):
                system_llm.acquire(model)
        call.assert_not_called()


if __name__ == '__main__': unittest.main()
