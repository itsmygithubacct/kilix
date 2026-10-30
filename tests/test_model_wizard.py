"""Wizard choices persist across interfaces; only checked models are acquired."""
import json
import io
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

    def test_speech_defaults_to_female02_and_finish_installs_the_release(self):
        assets = [asset('piper-en-us-kristin-medium', 'kilix-piper-tts'),
                  asset('kilix-female-02', 'kilix-piper-tts')]
        with patch.object(wizard, 'catalog', return_value=assets), \
                patch.object(wizard, 'sizing', return_value=({}, 'piper-en-us-kristin-medium', 'sizer')):
            page = wizard.pages(measure=True)[0]
        self.assertEqual(page['id'], 'speech')
        self.assertEqual(page['default'], 'kilix-female-02')
        self.assertIn('Catalog default: kilix-female-02', page['recommendation'])
        self.assertEqual(wizard.group(assets[1]), 'speech')
        with patch.object(wizard, 'catalog', return_value=assets):
            wizard.apply('speech', ['kilix-female-02'], 'yes')
            with patch('wizard_licenses.consent_and_install', return_value=({'kilix-female-02'}, set())), \
                    patch.object(wizard, 'run_command') as run:
                wizard.finish()
        self.assertEqual([item.args for item in run.call_args_list], [
            ('tts', '--install=kilix-female-02'),
            ('tts', '--set', 'engine=piper', '--set', 'voice=kilix-female-02')])
        self.assertEqual(wizard.answers()['speech']['models'], ['kilix-female-02'])

    def test_kristin_only_speech_still_prepares_kristin(self):
        assets = [asset('piper-en-us-kristin-medium', 'kilix-piper-tts')]
        with patch.object(wizard, 'catalog', return_value=assets):
            wizard.apply('speech', ['piper-en-us-kristin-medium'], 'yes')
            with patch('wizard_licenses.consent_and_install',
                       return_value=({'piper-en-us-kristin-medium'}, set())), \
                    patch.object(wizard, 'run_command') as run:
                wizard.finish()
        self.assertEqual([item.args for item in run.call_args_list], [
            ('tts', '--prepare-system-voice')])

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

    def test_wrapped_installer_error_survives_temporary_result_cleanup(self):
        wizard.apply('audio', ['encodec-24khz-stateful'], 'yes')
        cause = FileNotFoundError(2, 'No such file or directory', '/missing/converter')
        error = RuntimeError('Could not install encodec-24khz-stateful')
        error.__cause__ = cause
        with tempfile.TemporaryDirectory() as transient:
            ipc = Path(transient) / 'result.json'
            with patch.object(wizard, 'finish', side_effect=error), \
                    patch.object(wizard.sys.stdin, 'isatty', return_value=True), \
                    patch.object(wizard.sys, 'stderr', io.StringIO()):
                self.assertEqual(wizard.main(['finish', '--result', str(ipc)]), 1)
            result = json.loads(ipc.read_text())
            self.assertIn('encodec-24khz-stateful', result['error'])
            self.assertIn('/missing/converter', result['error'])
        log = Path(result['error_log'])
        self.assertTrue(log.is_file())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        self.assertIn('FileNotFoundError', log.read_text())
        self.assertIn('/missing/converter', log.read_text())
        self.assertFalse(json.loads((log.parent / 'result.json').read_text())['ok'])
        self.assertIn('audio', wizard.pending())
        latest = wizard.read_json(wizard.state_root() / 'model-wizard-last-result.json')
        self.assertEqual(latest['error_log'], str(log))

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
        self.assertEqual(answers['legacy-workflows']['answer'], 'yes')
        self.assertNotIn('workflows', answers)

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
        names = ('kilix-female-02', 'piper-en-us-kristin-medium', 'qwen3-tts-0.6b-customvoice',
                 'qwen3-tts-0.6b-base', 'qwen3-tts-1.7b-voicedesign', 'qwen3-tts-1.7b-base',
                 'pocket-tts-en-alba')
        rows = [asset(name) for name in names]
        requests = []
        def run(command, *, input, stdout, **kwargs):
            requests.append(json.loads(input))
            return subprocess.CompletedProcess(command, 1)
        with patch.object(system_llm, 'sizer_executable', return_value='/sizer'), \
                patch.object(wizard.subprocess, 'run', side_effect=run):
            wizard.sizing(rows, 'speech')
        supported = {row['id']: row['runtime_supported'] for row in requests[0]['models']}
        self.assertEqual(supported, {'kilix-female-02': True, 'piper-en-us-kristin-medium': True,
                                     'qwen3-tts-0.6b-customvoice': True, 'qwen3-tts-0.6b-base': True,
                                     'qwen3-tts-1.7b-voicedesign': True, 'qwen3-tts-1.7b-base': False,
                                     'pocket-tts-en-alba': False})

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

    def test_frontend_failure_has_private_persistent_diagnostics(self):
        error = RuntimeError('installer could not start')
        outcome = wizard.record_failure(error, phase='start installer')
        self.assertFalse(outcome['ok'])
        log = Path(outcome['error_log'])
        self.assertIn('installer could not start', log.read_text())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        latest = wizard.read_json(wizard.state_root() / 'model-wizard-last-result.json')
        self.assertEqual(latest['phase'], 'start installer')
        with patch.object(wizard, 'new_run_directory', side_effect=OSError('disk full')):
            outcome = wizard.record_failure(error, phase='start installer')
        self.assertIn('disk full', outcome['error'])

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

    def test_workflows_readiness_asks_needle_through_the_selected_catalog(self):
        environment = {'KILIX95_STATE_HOME': self.directory.name,
                       'KILIX_APP_AUTO_INSTALL': '1', 'NEEDLE_PROBE_CANARY': 'preserved'}
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            status = {'schema': 'kilix.workflows/v1', 'ready': True, 'status': 'ready',
                      'backend': 'lfm2', 'scope': 'ask', 'errors': []}
            return subprocess.CompletedProcess(command, 0, b'notice\n' + json.dumps(status).encode())
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(wizard, 'catalog', return_value=[asset('lfm2-kilix-commands')]):
            wizard.apply('workflows', ['lfm2-kilix-commands'], 'yes')
            with patch('wizard_licenses.consent_and_install', return_value=({'lfm2-kilix-commands'}, set())), \
                    patch.object(wizard.subprocess, 'run', side_effect=run), \
                    patch.object(wizard.subprocess, 'call') as call, \
                    patch.object(wizard, 'legacy_state') as legacy:
                wizard.finish()
        call.assert_not_called()
        self.assertEqual(calls, [([wizard.launcher(), 'app', 'run', 'kilix-needle', '--',
                                   'workflows', 'select', '--json', '--model', 'lfm2'],
                                  {'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE,
                                   'env': dict(environment, KILIX_APP_AUTO_INSTALL='0')})])
        legacy.assert_called_once_with('workflows', {'answer': 'yes', 'scope': 'ask'})
        self.assertEqual(wizard.answers()['workflows']['models'], ['lfm2-kilix-commands'])
        self.assertEqual(wizard.pending(), {})

    def test_unready_or_unreadable_workflows_status_keeps_the_page_pending(self):
        ready = {'schema': 'kilix.workflows/v1', 'ready': True, 'status': 'ready',
                 'backend': 'lfm2', 'scope': 'ask', 'errors': []}
        unready = dict(ready, ready=False, status='not-ready', detail='Workflow model setup needs attention.',
                       errors=['lfm2-kilix-commands: agreement-required'])
        cases = {'unready': (1, unready, 'agreement-required'), 'unrouted': (1, None, 'did not report'),
                 'mismatch': (0, unready, 'did not report'), 'schema': (0, dict(ready, schema='other'), 'did not report'),
                 'crash': (2, ready, 'did not report'),
                 'wrong-model': (0, dict(ready, backend='needle3'), 'did not report')}
        with patch.object(wizard, 'catalog', return_value=[asset('lfm2-kilix-commands')]):
            for name, (code, status, message) in cases.items():
                with self.subTest(name):
                    wizard.apply('workflows', ['lfm2-kilix-commands'], 'yes')
                    output = b'' if status is None else json.dumps(status).encode()
                    with patch('wizard_licenses.consent_and_install', return_value=({'lfm2-kilix-commands'}, set())), \
                            patch.object(wizard.subprocess, 'run',
                                         return_value=subprocess.CompletedProcess([], code, output)), \
                            patch.object(wizard, 'legacy_state') as legacy:
                        with self.assertRaisesRegex(ValueError, message): wizard.finish()
                    legacy.assert_not_called()
                    self.assertIn('workflows', wizard.pending())

    def test_workflows_probe_never_installs_a_missing_app(self):
        root = Path(self.directory.name)
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        marker = root / 'unexpected-install'
        for command in ('git', 'make'):
            sentinel = bin_dir / command
            sentinel.write_text('#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$INSTALL_SENTINEL"\nexit 79\n')
            sentinel.chmod(0o700)
        environment = {
            'HOME': os.environ['HOME'], 'PATH': str(bin_dir) + os.pathsep + os.defpath,
            'GPU_TERMINAL_HOME': str(root / 'stack'),
            'GPU_TERMINAL_SOURCE_HOME': str(root / 'sources'),
            'KILIX95_STATE_HOME': str(root / 'wizard'),
            'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_DATA_HOME': str(root / 'data'),
            'XDG_CACHE_HOME': str(root / 'cache'), 'XDG_RUNTIME_DIR': str(root),
            'KILIX_APP_AUTO_INSTALL': '1', 'INSTALL_SENTINEL': str(marker),
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
            'PYTHONDONTWRITEBYTECODE': '1',
        }
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(wizard, 'catalog', return_value=[asset('lfm2-kilix-commands')]), \
                patch('wizard_licenses.consent_and_install', return_value=({'lfm2-kilix-commands'}, set())):
            wizard.apply('workflows', ['lfm2-kilix-commands'], 'yes')
            with self.assertRaisesRegex(ValueError, 'did not report workflow readiness'):
                wizard.finish()
            self.assertIn('workflows', wizard.pending())
            self.assertFalse((wizard.state_root() / wizard.LEGACY['workflows']).exists())
            self.assertEqual(os.environ['KILIX_APP_AUTO_INSTALL'], '1')
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else '')
        self.assertFalse((root / 'stack/kilix/data/desktop-apps/kilix-needle').exists())

    def test_every_launcher_verb_the_wizard_runs_is_routed_by_the_launcher(self):
        import re
        # An unrouted verb reaches the terminal engine as a program to open.
        labels = {label for line in re.findall(r'^\s*([a-z][a-z0-9|-]*)\)', Path(wizard.launcher()).read_text(), re.M)
                  for label in line.split('|')}
        verbs = set(re.findall(r"run_command\('([a-z-]+)'", Path(wizard.__file__).read_text()))
        verbs.add(wizard.workflows_command()[1])
        self.assertTrue(verbs >= {'tts', 'stt', 'app'})
        self.assertEqual(verbs - labels, set())

    def test_ask_and_legacy_models_have_distinct_pages_and_defaults(self):
        assets = [asset(name, 'kilix-needle') for name in (
            'needle2', 'needle2-runtime', 'lfm2-kilix-commands', 'needle3-kilix-commands')]
        pages = {page['id']: page for page in wizard.pages(assets=assets, measure=False)}
        self.assertEqual(set(pages), {'workflows', 'legacy-workflows'})
        self.assertEqual(pages['workflows']['default'], 'lfm2-kilix-commands')
        self.assertEqual([row['id'] for row in pages['workflows']['models']],
                         ['lfm2-kilix-commands', 'needle3-kilix-commands', 'grammar'])
        self.assertEqual(pages['legacy-workflows']['default'], 'needle2')
        self.assertEqual(pages['workflows']['models'][-1]['download_bytes'], 0)

    def test_grammar_choice_skips_all_licence_and_acquisition_calls(self):
        with patch.object(wizard, 'catalog', return_value=[asset('lfm2-kilix-commands')]), \
                patch('wizard_licenses.consent_and_install') as install, \
                patch.object(wizard, 'check_workflows') as check, \
                patch.object(wizard, 'legacy_state'):
            wizard.apply('workflows', ['grammar'], 'yes')
            wizard.finish()
        install.assert_not_called()
        check.assert_called_once_with(model='grammar', select=True)
        self.assertEqual(wizard.answers()['workflows']['models'], ['grammar'])

    def test_optional_needle3_is_selected_after_its_own_acceptance(self):
        with patch.object(wizard, 'catalog', return_value=[asset('lfm2-kilix-commands'), asset('needle3-kilix-commands')]), \
                patch('wizard_licenses.consent_and_install', return_value=({'needle3-kilix-commands'}, set())) as install, \
                patch.object(wizard, 'check_workflows') as check, \
                patch.object(wizard, 'legacy_state'):
            wizard.apply('workflows', ['needle3-kilix-commands'], 'yes')
            wizard.finish()
        self.assertEqual(install.call_args.args[0], ['needle3-kilix-commands'])
        check.assert_called_once_with(model='needle3', select=True)

    def test_first_accepted_ask_model_becomes_the_saved_selection(self):
        # Both Ask models can be acquired together; Needle saves only the one
        # the wizard selects, the first choice whose licence was accepted.
        cases = (({'lfm2-kilix-commands', 'needle3-kilix-commands'}, 'lfm2',
                  ['lfm2-kilix-commands', 'needle3-kilix-commands']),
                 ({'needle3-kilix-commands'}, 'needle3', ['needle3-kilix-commands']))
        with patch.object(wizard, 'catalog', return_value=[asset('lfm2-kilix-commands'), asset('needle3-kilix-commands')]):
            for allowed, backend, models in cases:
                with self.subTest(backend), \
                        patch('wizard_licenses.consent_and_install',
                              return_value=(allowed, {'lfm2-kilix-commands'} - allowed)) as install, \
                        patch.object(wizard, 'check_workflows') as check, \
                        patch.object(wizard, 'legacy_state'):
                    wizard.apply('workflows', ['lfm2-kilix-commands', 'needle3-kilix-commands'], 'yes')
                    wizard.finish()
                    self.assertEqual(install.call_args.args[0], ['lfm2-kilix-commands', 'needle3-kilix-commands'])
                    check.assert_called_once_with(model=backend, select=True)
                    self.assertEqual(wizard.answers()['workflows']['models'], models)
                    self.assertEqual(wizard.answers()['workflows']['scope'], 'ask')

    def test_old_saved_needle2_answer_does_not_hide_new_ask_page(self):
        assets = [asset('needle2'), asset('lfm2-kilix-commands')]
        for answer, models in (('yes', ['needle2']), ('no', [])):
            wizard.atomic_json(wizard.state_root() / 'model-wizard.json', {
                'schema': wizard.SCHEMA,
                'answers': {'workflows': {'answer': answer, 'models': models, 'phase': 'complete'}}})
            self.assertEqual([page['id'] for page in wizard.pages(assets=assets, measure=False)], ['workflows'])
            self.assertEqual(wizard.answers()['legacy-workflows']['models'], models)

    def test_desktop_offer_answers_file_under_ask_and_unmarked_ones_under_needle2(self):
        # The desktop and this wizard mark Ask answers; one from before 0.2.2
        # has no marker and was about Needle2.
        assets = [asset('needle2'), asset('lfm2-kilix-commands')]
        (Path(self.directory.name) / wizard.LEGACY['workflows']).touch()
        for answer in ('yes', 'no'):
            for state, page, offered in (({'answer': answer, 'scope': 'ask'}, 'workflows', 'legacy-workflows'),
                                         ({'answer': answer}, 'legacy-workflows', 'workflows')):
                with self.subTest(answer=answer, state=state), \
                        patch.object(wizard, 'legacy_state', return_value=state):
                    (Path(self.directory.name) / 'model-wizard.json').unlink(missing_ok=True)
                    answers = wizard.answers()
                    self.assertEqual(set(answers), {page})
                    self.assertEqual(answers[page]['answer'], answer)
                    self.assertEqual(answers[page].get('scope'), 'ask' if page == 'workflows' else None)
                    self.assertEqual([row['id'] for row in wizard.pages(assets=assets, measure=False)], [offered])
                    # Saving another answer keeps the imported one where it was filed.
                    wizard.record('vision', 'no', [])
                    self.assertEqual(wizard.answers()[page]['answer'], answer)
                    self.assertNotIn(offered, wizard.answers())

    def test_ask_answers_write_the_scope_marker_to_the_shared_offer_state(self):
        with patch.object(wizard, 'catalog', return_value=[asset('lfm2-kilix-commands')]), \
                patch.object(wizard, 'legacy_state') as legacy:
            wizard.apply('workflows', [], 'no')
            legacy.assert_called_once_with('workflows', {'answer': 'no', 'scope': 'ask'})
            legacy.reset_mock()
            wizard.apply('workflows', ['lfm2-kilix-commands'], 'yes')
            with patch('wizard_licenses.consent_and_install', return_value=(set(), {'lfm2-kilix-commands'})), \
                    patch.object(wizard, 'check_workflows') as check:
                wizard.finish()
            check.assert_not_called()
            legacy.assert_called_once_with('workflows', {'answer': 'no', 'scope': 'ask'})
        self.assertEqual(wizard.declined_state('speech'), {'answer': 'no', 'enabled': False, 'offered': True})
        self.assertEqual(wizard.declined_state('dictation'), {'answer': 'no'})

    def test_grammar_cannot_be_mixed_with_model_downloads(self):
        with patch.object(wizard, 'catalog', return_value=[asset('lfm2-kilix-commands')]):
            with self.assertRaisesRegex(ValueError, 'grammar alone'):
                wizard.apply('workflows', ['grammar', 'lfm2-kilix-commands'], 'yes')
        self.assertEqual(wizard.pending(), {})

    def test_legacy_page_still_checks_needle2_without_changing_ask(self):
        with patch.object(wizard, 'catalog', return_value=[asset('needle2')]), \
                patch('wizard_licenses.consent_and_install', return_value=({'needle2'}, set())), \
                patch.object(wizard, 'check_workflows') as check, \
                patch.object(wizard, 'legacy_state') as legacy:
            wizard.apply('legacy-workflows', ['needle2'], 'yes')
            wizard.finish()
        check.assert_called_once_with(legacy=True)
        legacy.assert_not_called()


if __name__ == '__main__': unittest.main()
