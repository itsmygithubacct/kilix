"""Paradee supervisor: local files, saved selection, and the greeting protocol."""
import hashlib
import io
import json
import os
from pathlib import Path
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
        self.sizes = (runtime.ONNX_BYTES, runtime.ONNX_SHA256, runtime.CONFIG_BYTES, runtime.CONFIG_SHA256)
        self.addCleanup(self._restore_sizes)

    def _restore_sizes(self):
        runtime.ONNX_BYTES, runtime.ONNX_SHA256, runtime.CONFIG_BYTES, runtime.CONFIG_SHA256 = self.sizes

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

    def _install_files(self, onnx=b'onnx', config=b'{"voices":["af_heart"]}'):
        model = self.root / 'model'
        (model / 'onnx').mkdir(parents=True)
        (model / 'onnx' / 'paradee_int8.onnx').write_bytes(onnx)
        (model / 'config.json').write_bytes(config)
        runtime.ONNX_BYTES, runtime.ONNX_SHA256 = self._pin(onnx)
        runtime.CONFIG_BYTES, runtime.CONFIG_SHA256 = self._pin(config)
        python = self.root / 'python'
        python.write_text('#!/bin/sh\nexit 0\n')
        python.chmod(0o755)

    def test_engine_reports_only_a_saved_paradee_selection(self):
        self.assertEqual(runtime.engine(), 1)
        self._write_selection('piper', 'kilix-female-02')
        self.assertEqual(runtime.engine(), 1)
        self._write_selection('paradee', 'af_heart')
        output = io.StringIO()
        with patch.object(runtime.sys, 'stdout', output):
            self.assertEqual(runtime.engine(), 0)
        self.assertEqual(output.getvalue().strip(), 'af_heart')

    def test_install_checks_bytes_and_refuses_a_symlink(self):
        self._install_files()
        self.assertEqual(runtime.install_check(), 0)
        target = self.root / 'model' / 'config.json'
        target.unlink()
        link = self.root / 'elsewhere.json'
        link.write_bytes(b'{"voices":["af_heart"]}')
        target.symlink_to(link)
        with self.assertRaises(SystemExit):
            runtime.install_check()

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
