import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('speech_buffer', Path(__file__).parents[1] / 'config/kilix_sdk/speech_buffer.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
AdaptiveBuffer = module.AdaptiveBuffer


class SpeechBufferTests(unittest.TestCase):
    def test_schedules_against_remaining_audio_and_safety(self):
        model = AdaptiveBuffer(buffer_seconds=5)
        for _ in range(8):
            model.observe(10, 20, 0)
        self.assertAlmostEqual(model.estimate(10, 0)['lead_seconds'], 25)
        self.assertAlmostEqual(model.delay(40, 10, 0), 15)
        self.assertEqual(model.delay(12, 10, 0), 0)

    def test_learns_slowdown_at_similar_load_and_extrapolates(self):
        model = AdaptiveBuffer(buffer_seconds=5)
        for _ in range(10):
            model.observe(10, 10, 0)
            model.observe(10, 30, 1)
        self.assertGreater(model.estimate(10, 1)['average_seconds'], 2 * model.estimate(10, 0)['average_seconds'])
        self.assertGreater(model.estimate(10, 2)['lead_seconds'], model.estimate(10, 1)['lead_seconds'])

    def test_jitter_widens_margin_and_history_is_bounded(self):
        model = AdaptiveBuffer(buffer_seconds=5)
        for i in range(100):
            model.observe(10, 10 if i % 2 else 30, 0)
        self.assertEqual(len(model.state()['samples']), 64)
        self.assertGreater(model.estimate(10, 0)['safety_seconds'], 5)
        restored = AdaptiveBuffer(model.state(), buffer_seconds=5)
        self.assertEqual(restored.estimate(10, 0), model.estimate(10, 0))

    def test_rejects_invalid_measurements(self):
        model = AdaptiveBuffer()
        for values in ((0, 1, 0), (1, -1, 0), (1, 1, float('nan'))):
            with self.assertRaises(ValueError):
                model.observe(*values)


if __name__ == '__main__':
    unittest.main()
