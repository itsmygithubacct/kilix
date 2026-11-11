"""Paradee supervisor: local files, saved selection, and the greeting protocol."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
import paradee_runtime as runtime


def _wav(path, rate=24000):
    with wave.open(str(path), 'w') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b'\x00\x00' * 8)


class ParadeeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.environment = patch.dict(os.environ, KILIX95_STATE_HOME=str(self.root / 'state'),
                                       KILIX_PARADEE_MODEL=str(self.root / 'model'),
                                       KILIX_PARADEE_PYTHON=str(self.root / 'python'))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.sizes = (runtime.ONNX_BYTES, runtime.ONNX_SHA256, runtime.CONFIG_BYTES,
                      runtime.CONFIG_SHA256, runtime.LICENCE_BYTES, runtime.LICENCE_SHA256)
        self.addCleanup(self._restore_sizes)

    def _restore_sizes(self):
        (runtime.ONNX_BYTES, runtime.ONNX_SHA256, runtime.CONFIG_BYTES,
         runtime.CONFIG_SHA256, runtime.LICENCE_BYTES, runtime.LICENCE_SHA256) = self.sizes

    def _write_selection(self, engine, voice):
        path = self.root / 'state' / 'system-voice-default.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({
            'schema': 'kilix.system-voice-default/v1',
            'engine': engine,
            'voice': voice,
        }))

    def _pin(self, payload):
        return len(payload), hashlib.sha256(payload).hexdigest()

    def _write_model(self, model, onnx=b'onnx', config=b'{"voices":["af_heart"]}', licence=b'apache'):
        model = Path(model)
        (model / 'onnx').mkdir(parents=True)
        (model / 'notices').mkdir()
        (model / 'onnx' / 'paradee_int8.onnx').write_bytes(onnx)
        (model / 'config.json').write_bytes(config)
        (model / 'notices' / 'LICENSE-apache-2.0.txt').write_bytes(licence)
        runtime.ONNX_BYTES, runtime.ONNX_SHA256 = self._pin(onnx)
        runtime.CONFIG_BYTES, runtime.CONFIG_SHA256 = self._pin(config)
        runtime.LICENCE_BYTES, runtime.LICENCE_SHA256 = self._pin(licence)

    def _install_files(self, onnx=b'onnx', config=b'{"voices":["af_heart"]}', licence=b'apache'):
        self._write_model(self.root / 'model', onnx, config, licence)
        python = self.root / 'python'
        python.write_text('#!/bin/sh\nexit 0\n')
        python.chmod(0o755)

    def _python(self):
        python = self.root / 'python'
        python.write_text('#!/bin/sh\nexit 0\n')
        python.chmod(0o755)

    def test_engine_reports_only_a_saved_paradee_selection(self):
        with patch.object(runtime.subprocess, 'run') as run:
            self.assertEqual(runtime.engine(), 1)
            self._write_selection('piper', 'kilix-female-02')
            self.assertEqual(runtime.engine(), 1)
            self._write_selection('paradee', 'af_heart')
            output = io.StringIO()
            with patch.object(runtime.sys, 'stdout', output):
                self.assertEqual(runtime.engine(), 0)
        self.assertEqual(output.getvalue().strip(), 'af_heart')
        run.assert_not_called()

    def test_install_checks_bytes_and_refuses_a_symlink(self):
        self._install_files()
        with patch.object(runtime.subprocess, 'run') as run:
            self.assertEqual(runtime.install_check(), 0)
        run.assert_not_called()
        target = self.root / 'model' / 'config.json'
        target.unlink()
        link = self.root / 'elsewhere.json'
        link.write_bytes(b'{"voices":["af_heart"]}')
        target.symlink_to(link)
        with self.assertRaises(SystemExit):
            runtime.install_check()

    def test_install_and_synthesis_refuse_a_missing_licence(self):
        self._install_files()
        licence = self.root / 'model' / 'notices' / 'LICENSE-apache-2.0.txt'
        licence.unlink()
        with patch.object(runtime.sys.stdin, 'isatty', return_value=True), \
                patch.object(runtime.sys.stdout, 'isatty', return_value=True), \
                patch.object(runtime.subprocess, 'run') as run:
            with self.assertRaises(SystemExit):
                runtime.install_check()
            with self.assertRaises(SystemExit):
                runtime.synthesize('hello', 'af_heart', self.root / 'out.wav')
            licence.write_bytes(b'other')
            with self.assertRaises(SystemExit):
                runtime.install_check()
        run.assert_not_called()

    def _calls(self, run):
        completed = []

        def fake(argv, **_kwargs):
            completed.append(list(argv))
            if argv[1:3] == ['models', 'verify']:
                if run.get('verified'):
                    return subprocess.CompletedProcess(argv, 0, json.dumps({
                        'verified': runtime.PARADEE_ID, 'root': str(self.root / 'apps')}), '')
                return subprocess.CompletedProcess(argv, 1, '', 'missing')
            if argv[1:3] == ['models', 'ensure']:
                run['ensured'] = True
                if run.get('decline'):
                    return subprocess.CompletedProcess(argv, 1, '', 'declined')
                run['verified'] = True
                return subprocess.CompletedProcess(argv, 0, '', '')
            raise AssertionError(argv)

        return fake, completed

    def test_a_verified_catalog_copy_is_not_installed_again(self):
        self._python()
        self._write_model(self.root / 'apps' / 'assets' / runtime.PARADEE_ID)
        state = {'verified': True}
        fake, completed = self._calls(state)
        with patch.dict(os.environ, KILIX_PARADEE_MODEL=str(self.root / 'absent'),
                         KILIX_MODEL_AUTO_INSTALL='1'), \
                patch.object(runtime.sys.stdin, 'isatty', return_value=True), \
                patch.object(runtime.sys.stdout, 'isatty', return_value=True), \
                patch.object(runtime.subprocess, 'run', side_effect=fake):
            self.assertEqual(runtime.install_check(), 0)
        self.assertEqual([item[1:3] for item in completed], [['models', 'verify']])

    def test_a_missing_image_on_a_terminal_installs_after_the_licence(self):
        self._python()
        self._write_model(self.root / 'apps' / 'assets' / runtime.PARADEE_ID)
        state = {}
        fake, completed = self._calls(state)
        with patch.dict(os.environ, KILIX_PARADEE_MODEL=str(self.root / 'absent'),
                         KILIX_MODEL_AUTO_INSTALL='1'), \
                patch.object(runtime.sys.stdin, 'isatty', return_value=True), \
                patch.object(runtime.sys.stdout, 'isatty', return_value=True), \
                patch.object(runtime.subprocess, 'run', side_effect=fake):
            self.assertEqual(runtime.install_check(), 0)
        self.assertEqual([item[1:3] for item in completed],
                         [['models', 'verify'], ['models', 'ensure'], ['models', 'verify']])
        self.assertTrue(str(completed[1][0]).endswith('/kilix'))

    def test_a_declined_licence_installs_nothing(self):
        state = {'decline': True}
        fake, completed = self._calls(state)
        with patch.dict(os.environ, KILIX_PARADEE_MODEL=str(self.root / 'absent')), \
                patch.object(runtime.sys.stdin, 'isatty', return_value=True), \
                patch.object(runtime.sys.stdout, 'isatty', return_value=True), \
                patch.object(runtime.subprocess, 'run', side_effect=fake):
            with self.assertRaises(SystemExit) as caught:
                runtime.install_check()
        self.assertIn('was not installed', str(caught.exception.code))
        self.assertEqual([item[1:3] for item in completed],
                         [['models', 'verify'], ['models', 'ensure']])
        self.assertFalse((self.root / 'absent').exists())

    def test_without_a_terminal_a_missing_image_is_not_installed(self):
        state = {}
        fake, completed = self._calls(state)
        with patch.dict(os.environ, KILIX_PARADEE_MODEL=str(self.root / 'absent')), \
                patch.object(runtime.sys.stdin, 'isatty', return_value=False), \
                patch.object(runtime.sys.stdout, 'isatty', return_value=True), \
                patch.object(runtime.subprocess, 'run', side_effect=fake):
            with self.assertRaises(SystemExit) as caught:
                runtime.install_check()
        self.assertIn('missing', str(caught.exception.code))
        self.assertEqual([item[1:3] for item in completed], [['models', 'verify']])

    def test_auto_install_off_refuses_on_a_terminal(self):
        state = {}
        fake, completed = self._calls(state)
        with patch.dict(os.environ, KILIX_PARADEE_MODEL=str(self.root / 'absent'),
                         KILIX_MODEL_AUTO_INSTALL='0'), \
                patch.object(runtime.sys.stdin, 'isatty', return_value=True), \
                patch.object(runtime.sys.stdout, 'isatty', return_value=True), \
                patch.object(runtime.subprocess, 'run', side_effect=fake):
            with self.assertRaises(SystemExit) as caught:
                runtime.session(['--speak=hello'])
        self.assertIn('missing', str(caught.exception.code))
        self.assertEqual([item[1:3] for item in completed], [['models', 'verify']])

    def test_a_symlink_model_path_is_not_installed(self):
        link = self.root / 'linked'
        link.symlink_to(self.root / 'elsewhere')
        with patch.dict(os.environ, KILIX_PARADEE_MODEL=str(link)), \
                patch.object(runtime.sys.stdin, 'isatty', return_value=True), \
                patch.object(runtime.sys.stdout, 'isatty', return_value=True), \
                patch.object(runtime.subprocess, 'run') as run:
            with self.assertRaises(SystemExit) as caught:
                runtime.install_check()
        self.assertIn('symlink', str(caught.exception.code))
        run.assert_not_called()

    def test_session_speaks_and_answers_goodbye(self):
        spoken = []

        def synthesize(text, voice, wav_path):
            spoken.append((text, voice))
            _wav(wav_path)

        played = []
        with patch.object(runtime, 'synthesize', side_effect=synthesize), \
                patch.object(runtime, 'play', side_effect=lambda path, volume: played.append(volume)), \
                patch.object(runtime.sys, 'stdin', type('Stdin', (), {'buffer': io.BytesIO(b'g')})()), \
                patch.object(runtime.sys, 'stdout', io.StringIO()) as output:
            self.assertEqual(runtime.session(['--system-voice', '--system-volume=7', '--speak=hello']), 0)
        self.assertEqual(spoken, [('hello', 'af_heart'), ('Goodbye.', 'af_heart')])
        self.assertEqual(played, [7, 7])
        self.assertIn('System voice ready: paradee af_heart', output.getvalue())
        self.assertIn('System voice goodbye complete', output.getvalue())

    def test_session_uses_a_saved_paradee_voice_and_stops_without_goodbye(self):
        self._write_selection('paradee', 'bf_isabella')
        spoken = []

        def synthesize(text, voice, wav_path):
            spoken.append(voice)
            _wav(wav_path)

        with patch.object(runtime, 'synthesize', side_effect=synthesize), \
                patch.object(runtime, 'play'), \
                patch.object(runtime.sys, 'stdin', type('Stdin', (), {'buffer': io.BytesIO(b'x')})()), \
                patch.object(runtime.sys, 'stdout', io.StringIO()) as output:
            self.assertEqual(runtime.session(['--speak=hello']), 0)
        self.assertEqual(spoken, ['bf_isabella'])
        self.assertIn('System voice ready: paradee bf_isabella', output.getvalue())
        self.assertNotIn('goodbye', output.getvalue())

    def test_a_non_paradee_saved_voice_falls_back_to_af_heart(self):
        self._write_selection('piper', 'kilix-female-02')
        spoken = []

        def synthesize(text, voice, wav_path):
            spoken.append(voice)
            _wav(wav_path)

        with patch.object(runtime, 'synthesize', side_effect=synthesize), \
                patch.object(runtime, 'play'), \
                patch.object(runtime.sys, 'stdin', type('Stdin', (), {'buffer': io.BytesIO(b'')})()):
            runtime.session(['--speak=hello'])
        self.assertEqual(spoken, ['af_heart'])

    def test_zero_volume_skips_playback(self):
        with patch.object(runtime.shutil, 'which', return_value='/usr/bin/paplay'), \
                patch.object(runtime.subprocess, 'run') as play:
            runtime.play(self.root / 'speech.wav', 0)
        play.assert_not_called()
