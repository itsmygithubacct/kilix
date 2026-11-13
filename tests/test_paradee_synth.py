import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import wave

spec = importlib.util.spec_from_file_location('paradee_synth', Path(__file__).parents[1] / 'config/paradee_synth.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ParadeeSynthesisTests(unittest.TestCase):
    def test_long_phonemes_keep_the_tail_and_bound_every_input(self):
        for text in ('spoken words. ' * 170 + 'finalword', 'x' * 1400):
            chunks = list(module.phoneme_chunks(text))
            self.assertGreater(len(chunks), 1)
            self.assertTrue(all(0 < len(chunk) <= 510 for chunk in chunks))
            self.assertEqual(''.join(''.join(chunks).split()), ''.join(text.split()))

    def test_worker_reuses_session_and_recovers_from_invalid_request(self):
        session = Mock()
        session.synthesize.return_value = {'samples': 24, 'voice': 'af_heart'}
        source = io.StringIO('{broken}\n' + json.dumps({'text': 'One.', 'wav': 'a.wav'}) + '\n' +
                             json.dumps({'text': 'Two.', 'wav': 'b.wav'}) + '\n')
        output = io.StringIO()
        self.assertEqual(module.serve(session, source, output), 0)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([reply['ok'] for reply in replies], [False, True, True])
        self.assertEqual(session.synthesize.call_count, 2)

    @unittest.skipUnless(importlib.util.find_spec('numpy'), 'NumPy required for waveform regression')
    def test_all_model_windows_reach_wav_with_exact_duration(self):
        import numpy as np
        session = module.ParadeeSession.__new__(module.ParadeeSession)
        session.np, session.vocab = np, {'a': 1}
        session.voice, session.voice_id = 'af_heart', 0
        session.grapheme = lambda text: ('a' * 1200, None)
        session.session = Mock()
        session.session.run.return_value = [np.ones(240, dtype=np.float32)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'speech.wav'
            result = session.synthesize('Long story.', path)
            calls = session.session.run.call_args_list
            self.assertEqual([call.args[1]['input_ids'].size for call in calls], [512, 512, 182])
            self.assertEqual(sum(call.args[1]['input_ids'].size - 2 for call in calls), 1200)
            with wave.open(str(path)) as wav:
                self.assertEqual(wav.getnframes(), 720 + 2 * 1920)
                self.assertEqual(wav.getnframes() / wav.getframerate(), result['audio_seconds'])
            self.assertEqual(result['phoneme_chunks'], 3)
            session.session.run.return_value = [np.array([float('nan')])]
            with self.assertRaises(ValueError):
                session.synthesize('Invalid audio.', path)
            self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
