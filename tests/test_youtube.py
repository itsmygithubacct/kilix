"""Caption completeness, source selection and literal output names."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('youtube', Path(__file__).parents[1] / 'config/youtube.py')
youtube = importlib.util.module_from_spec(spec)
spec.loader.exec_module(youtube)


class YoutubeTests(unittest.TestCase):
    def test_original_language_and_manual_precedence(self):
        track = [{'ext': 'json3'}]
        info = {'automatic_captions': {'ar-orig': track, 'en-orig': track, 'en': track}}
        self.assertEqual(youtube.caption_track(info, 'en'), ('en-orig', True))
        info['subtitles'] = {'en': track}
        self.assertEqual(youtube.caption_track(info, 'en'), ('en', False))
        self.assertIsNone(youtube.caption_track(info, 'fr'))

    def test_newline_events_and_duplicate_cues(self):
        events = [{'tStartMs': 0, 'dDurationMs': 5000, 'segs': [{'utf8': 'Hello'}, {'utf8': ' world'}]},
                  {'tStartMs': 1000, 'segs': [{'utf8': '\n'}]},
                  {'tStartMs': 2000, 'segs': [{'utf8': 'Hello world'}]}]
        self.assertEqual(youtube.caption_rows({'events': events}), [(0, 5, 'Hello world')])

    def test_missing_middle_is_not_complete(self):
        rows = [(0, 5, 'some words ' * 50), (95, 100, 'more words ' * 50)]
        self.assertFalse(youtube.usable_captions(rows, 100))
        self.assertTrue(youtube.usable_captions([(0, 100, 'some words ' * 50)], 100))
        self.assertFalse(youtube.usable_captions([], 100))

    def test_safe_byte_bounded_filename(self):
        name = youtube.basename('../\x1b[31m/a\\b' + '界' * 200, '../../id')
        self.assertNotIn('/', name)
        self.assertNotIn('\\', name)
        self.assertNotIn('\x1b', name)
        self.assertLess(len(name.encode()), 240)

    def test_incomplete_captions_use_local_model(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'captions.en-orig.json3').write_text('{"events": []}')
            info = {'id': 'test', 'title': 'Test', 'duration': 100,
                    'automatic_captions': {'en-orig': [{'ext': 'json3'}]}}
            with patch.object(youtube, 'download'), patch.object(youtube, 'local_transcript',
                    return_value=[(0, 100, 'Recognized speech')]) as local:
                result = youtube.transcript(info, 'https://youtu.be/test', work, 'en', False, work)
                local.assert_called_once()
                self.assertIn('Recognized speech', result.read_text())
                receipt = json.loads(result.with_suffix('.json').read_text())
                self.assertEqual(receipt['source'], 'local-whisper-small-en')
                with self.assertRaisesRegex(RuntimeError, 'already exists'):
                    youtube.transcript(info, 'https://youtu.be/test', work, 'en', True, work)

    def test_untrusted_url_refused(self):
        import argparse
        for value in ('file:///etc/passwd', 'https://youtube.com.evil.test/watch', '--exec=x'):
            with self.assertRaises(argparse.ArgumentTypeError):
                youtube.youtube_url(value)


if __name__ == '__main__':
    unittest.main()
