"""Playalong's default models install unless the wizard page or the setting says no."""
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import model_wizard as wizard
import playalong_models as playalong


def asset(name, provider):
    return dict(id=name, label=name, provider=provider, download_bytes=10,
                installed_bytes=20, temporary_bytes=30)


class DeclineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.settings = self.root / 'settings.conf'
        environment = patch.dict(os.environ, KILIX95_STATE_HOME=str(self.root / 'state'),
                                 GPU_TERMINAL_SETTINGS_FILE=str(self.settings))
        environment.start()
        self.addCleanup(environment.stop)

    def answer(self, value):
        wizard.record(playalong.WIZARD_PAGE, value, [])

    def test_no_answer_and_no_setting_install(self):
        self.assertIsNone(playalong.declined())
        self.settings.write_text('# GPU Terminal shared settings\nKILIX_CHROME_CLOCK=1\n')
        self.assertIsNone(playalong.declined())

    def test_a_wizard_no_declines_and_a_yes_does_not(self):
        self.answer('yes')
        self.assertIsNone(playalong.declined())
        self.answer('no')
        self.assertIn('Playalong page was answered no', playalong.declined())

    def test_the_setting_declines_on_0_and_on_any_off_value(self):
        for value in ('0', 'no', 'off', 'false', ''):
            with self.subTest(value=value):
                self.settings.write_text(f'KILIX_PLAYALONG_MODELS={value}\n')
                reason = playalong.declined()
                self.assertIsNotNone(reason)
                self.assertIn(f'KILIX_PLAYALONG_MODELS={value}', reason)
        for value in ('1', 'yes', 'on'):
            with self.subTest(value=value):
                self.settings.write_text(f'KILIX_PLAYALONG_MODELS={value}\n')
                self.assertIsNone(playalong.declined())
        # The last assignment wins, as everywhere in the shared settings.
        self.settings.write_text('KILIX_PLAYALONG_MODELS=0\nKILIX_PLAYALONG_MODELS=1\n')
        self.assertIsNone(playalong.declined())

    def test_either_answer_alone_is_enough(self):
        self.answer('no')
        self.settings.write_text('KILIX_PLAYALONG_MODELS=1\n')
        self.assertIsNotNone(playalong.declined())
        self.answer('yes')
        self.settings.write_text('KILIX_PLAYALONG_MODELS=0\n')
        self.assertIsNotNone(playalong.declined())

    def test_an_unreadable_answer_is_never_read_as_install(self):
        state = self.root / 'state'
        state.mkdir(parents=True, exist_ok=True)
        (state / 'model-wizard.json').write_text(json.dumps(
            {'schema': wizard.SCHEMA, 'answers': {playalong.WIZARD_PAGE: {'answer': 'maybe'}}}))
        with self.assertRaises(ValueError):
            playalong.declined()
        stderr, stdout = io.StringIO(), io.StringIO()
        with redirect_stderr(stderr), redirect_stdout(stdout):
            status = playalong.main(['declined'])
        self.assertEqual(status, playalong.UNREADABLE)
        self.assertNotIn(status, (playalong.DECLINED, playalong.NOT_DECLINED))
        self.assertIn('cannot read', stderr.getvalue())

    def test_cli_statuses_are_distinct(self):
        self.assertEqual(len({playalong.DECLINED, playalong.NOT_DECLINED, playalong.UNREADABLE}), 3)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(playalong.main(['declined']), playalong.NOT_DECLINED)
        self.answer('no')
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(playalong.main(['declined', '--json']), playalong.DECLINED)
        self.assertEqual(json.loads(out.getvalue())['declined'], True)

    def finish_page(self, selected, refused, segment=playalong.WIZARD_PAGE):
        """Choose rows on a wizard page, then refuse some licences in finish()."""
        assets = [asset('faster-whisper-large-v3', 'kilix-playalong'),
                  asset('basic-pitch-icassp-2022-onnx', 'kilix-playalong'),
                  asset('faster-whisper-medium-en', 'kilix-whisper-stt')]
        with patch.object(wizard, 'catalog', return_value=assets), \
                patch.object(wizard, 'run_command'), \
                patch.object(wizard, 'activate_page', return_value=True), \
                patch.object(wizard, 'legacy_state'), \
                patch('wizard_licenses.consent_and_install',
                      return_value=(set(selected) - set(refused), set(refused))):
            wizard.apply(segment, selected, 'yes')
            with redirect_stdout(io.StringIO()):
                wizard.finish()

    def test_refusing_only_large_v3s_licence_declines_nothing(self):
        self.finish_page(['faster-whisper-large-v3'], ['faster-whisper-large-v3'])
        entry = wizard.answers()[playalong.WIZARD_PAGE]
        # The page is settled, so the wizard does not ask it again ...
        self.assertEqual(entry['answer'], 'no')
        self.assertEqual(entry['refused'], ['faster-whisper-large-v3'])
        # ... but refusing the optional model is not a "no" to the defaults.
        self.assertIsNone(playalong.declined())

    def test_refusing_a_default_licence_on_the_page_declines(self):
        self.finish_page(['basic-pitch-icassp-2022-onnx', 'faster-whisper-large-v3'],
                         ['basic-pitch-icassp-2022-onnx', 'faster-whisper-large-v3'])
        reason = playalong.declined()
        self.assertIsNotNone(reason)
        self.assertIn('refused the licence of basic-pitch-icassp-2022-onnx', reason)
        self.assertNotIn('faster-whisper-large-v3', reason)

    def test_refusing_a_default_licence_declines_even_when_the_page_ends_yes(self):
        # Large-v3 accepted, Basic Pitch refused: the page is a "yes", but no
        # default install may then write a receipt for the refused terms.
        self.finish_page(['basic-pitch-icassp-2022-onnx', 'faster-whisper-large-v3'],
                         ['basic-pitch-icassp-2022-onnx'])
        entry = wizard.answers()[playalong.WIZARD_PAGE]
        self.assertEqual((entry['answer'], entry['models'], entry['refused']),
                         ('yes', ['faster-whisper-large-v3'], ['basic-pitch-icassp-2022-onnx']))
        self.assertIn('refused the licence of basic-pitch-icassp-2022-onnx', playalong.declined())

    def test_refusing_a_default_licence_on_another_page_declines(self):
        self.finish_page(['faster-whisper-medium-en'], ['faster-whisper-medium-en'], segment='dictation')
        self.assertEqual(wizard.answers()['dictation']['refused'], ['faster-whisper-medium-en'])
        self.assertNotIn(playalong.WIZARD_PAGE, wizard.answers())
        self.assertIn('refused the licence of faster-whisper-medium-en', playalong.declined())

    def test_a_page_with_no_refusal_records_none(self):
        self.finish_page(['basic-pitch-icassp-2022-onnx'], [])
        entry = wizard.answers()[playalong.WIZARD_PAGE]
        self.assertEqual(entry['answer'], 'yes')
        self.assertNotIn('refused', entry)
        self.assertIsNone(playalong.declined())

    def test_a_page_answered_no_still_declines(self):
        assets = [asset('basic-pitch-icassp-2022-onnx', 'kilix-playalong')]
        with patch.object(wizard, 'catalog', return_value=assets):
            wizard.apply(playalong.WIZARD_PAGE, [], 'no')
        self.assertNotIn('refused', wizard.answers()[playalong.WIZARD_PAGE])
        self.assertIn('Playalong page was answered no', playalong.declined())

    def test_an_unreadable_refusal_is_never_read_as_install(self):
        state = self.root / 'state'
        state.mkdir(parents=True, exist_ok=True)
        for refused in ([], 'faster-whisper-large-v3', [1]):
            with self.subTest(refused=refused):
                (state / 'model-wizard.json').write_text(json.dumps(
                    {'schema': wizard.SCHEMA, 'answers': {playalong.WIZARD_PAGE: {
                        'answer': 'no', 'models': [], 'phase': 'complete', 'refused': refused}}}))
                with self.assertRaises(ValueError):
                    playalong.declined()

    def test_large_v3_is_never_a_default(self):
        self.assertEqual(playalong.DEFAULT_MODELS,
                         ('demucs-htdemucs-6s', 'basic-pitch-icassp-2022-onnx', 'faster-whisper-medium-en'))
        self.assertNotIn('faster-whisper-large-v3', playalong.DEFAULT_MODELS)
        self.assertEqual(playalong.OPTIONAL_MODELS, ('faster-whisper-large-v3',))


class PlayalongPageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, KILIX95_STATE_HOME=directory.name)
        environment.start()
        self.addCleanup(environment.stop)
        self.assets = [
            asset('faster-whisper-medium-en', 'kilix-whisper-stt'),
            asset('demucs-htdemucs-6s', 'kilix-split'),
            asset('faster-whisper-large-v3', 'kilix-playalong'),
            asset('basic-pitch-icassp-2022-onnx', 'kilix-playalong'),
        ]

    def test_large_v3_sits_on_the_playalong_page_and_is_not_pre_checked(self):
        self.assertEqual(wizard.PLAYALONG_PAGE, playalong.WIZARD_PAGE)
        self.assertEqual(wizard.group(self.assets[2]), 'kilix-playalong')
        # Shared models keep their own pages; the Playalong "no" still covers them.
        self.assertEqual(wizard.group(self.assets[0]), 'dictation')
        self.assertEqual(wizard.group(self.assets[1]), 'kilix-split')
        page = next(page for page in wizard.pages(assets=self.assets, measure=False)
                    if page['id'] == 'kilix-playalong')
        # Large-v3 comes first in this catalog order; it must still not be the default.
        self.assertEqual([row['id'] for row in page['models']],
                         ['faster-whisper-large-v3', 'basic-pitch-icassp-2022-onnx'])
        self.assertEqual(page['default'], 'basic-pitch-icassp-2022-onnx')
        self.assertEqual(page['title'], 'Playalong')
        self.assertIn('unless this page is answered no or KILIX_PLAYALONG_MODELS=0',
                      page['recommendation'])
        self.assertIn('Whisper large-v3', page['recommendation'])


if __name__ == '__main__':
    unittest.main()
