"""Regression boundaries for transcript selection, authority and safe publication."""
import argparse
from contextlib import redirect_stderr
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import wave

spec = importlib.util.spec_from_file_location('youtube_review', Path(__file__).parents[1] / 'config/youtube.py')
y = importlib.util.module_from_spec(spec)
spec.loader.exec_module(y)
URL = 'https://www.youtube.com/watch?v=dOkKO4SoeRQ'
INFO = {'id': 'dOkKO4SoeRQ', 'title': 'A video', 'duration': 100, 'language': 'en-US'}


def wav_file(path, duration):
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\0\0' * round(duration * 16000))


class YoutubeReviewTests(unittest.TestCase):
    def test_rejects_translated_automatic_track(self):
        tracks = {'automatic_captions': {'en': [
            {'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?lang=es&tlang=en'}]}}
        self.assertIsNone(y.caption_track(tracks, 'en'))
        tracks['automatic_captions']['en'].append(
            {'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?lang=en'})
        self.assertEqual(y.caption_track(tracks, 'en'), ('en', True))
        self.assertEqual(len(y.caption_formats(tracks['automatic_captions']['en'])), 1)

    def test_large_middle_gap_and_nonfinite_duration_refused(self):
        self.assertFalse(y.usable_captions([(0, 35, 'speech ' * 20), (65, 100, 'speech ' * 20)], 100))
        self.assertFalse(y.usable_captions([(0, 100, 'speech ' * 20)], float('nan')))
        self.assertFalse(y.usable_captions([(0, 100, 'speech ' * 20)], None))
        self.assertTrue(y.usable_captions([(0, 100, 'speech ' * 20)], 100))

    def test_invalid_caption_timestamps_refused(self):
        for value in ('NaN', float('inf'), -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                y.caption_rows({'events': [{'tStartMs': value, 'segs': [{'utf8': 'word'}]}]})

    def test_video_url_canonicalization_refuses_playlists_and_userinfo(self):
        self.assertEqual(y.youtube_url('https://youtu.be/dOkKO4SoeRQ?t=30'), URL)
        self.assertEqual(y.youtube_url(URL + '&list=playlist'), URL)
        for url in ('https://www.youtube.com/playlist?list=abc', 'https://youtu.be/id',
                    'https://:secret@youtube.com/watch?v=dOkKO4SoeRQ',
                    'https://www.youtube.com:444/watch?v=dOkKO4SoeRQ',
                    URL + '&v=abcdefghijk', 'https://www.youtube.com/@channel'):
            with self.subTest(url=url), self.assertRaises(argparse.ArgumentTypeError):
                y.youtube_url(url)

    def test_collision_precedes_network_and_inference_including_dangling_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / (y.basename(INFO['title'], INFO['id']) + '.txt')
            target.symlink_to(root / 'missing')
            with patch.object(y, 'download') as download, patch.object(y, 'local_transcript') as local:
                with self.assertRaisesRegex(RuntimeError, 'already exists'):
                    y.transcript(INFO, URL, root, 'en', True, root)
                download.assert_not_called(); local.assert_not_called()
            self.assertTrue(target.is_symlink())

    def test_snapshot_keeps_only_original_track_and_utf8_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?lang=en'}
            translated = {'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?lang=es&tlang=en'}
            info = dict(INFO, title='A % and 界 video', automatic_captions={'en': [original, translated]})
            def download(*args, **kwargs):
                self.assertIn('--load-info-json', args)
                snapshot = json.loads(Path(args[1]).read_text())
                self.assertEqual(snapshot['automatic_captions'], {'en': [original]})
                (root / 'captions.en.json3').write_text(json.dumps({'events': [{
                    'tStartMs': 0, 'dDurationMs': 100000, 'segs': [{'utf8': 'Some speech ' * 20}]}]}))
            with patch.object(y, 'download', side_effect=download), patch.object(y, 'local_transcript') as local:
                target = y.transcript(info, URL, root, 'en', False, root)
                local.assert_not_called()
            receipt = json.loads(target.with_suffix('.json').read_text())
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), receipt['transcript_sha256'])
            self.assertTrue(receipt['caption_coverage']['usable'])

    def test_metadata_publish_failure_preserves_collision_and_rolls_back_own_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); work = root / 'work'; work.mkdir()
            target, metadata = root / 'out.txt', root / 'out.json'
            metadata.write_text('other invocation')
            with self.assertRaises(FileExistsError):
                y.publish_pair(target, metadata, 'new text', {}, work)
            self.assertFalse(target.exists())
            self.assertEqual(metadata.read_text(), 'other invocation')

    def test_media_staging_does_not_replace_existing_final(self):
        for mode, extension in (('music', '.mp3'), ('video', '.mkv')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); info = dict(INFO, title='100% %(title)s')
                target = root / (y.basename(info['title'], info['id']) + extension)
                target.write_bytes(b'owner file')
                def download(*args, **kwargs):
                    template = args[args.index('-o') + 1]
                    file = Path(template.replace('%%', '%').replace('.%(ext)s', extension))
                    self.assertNotEqual(file.parent, root)
                    file.write_bytes(b'new media')
                with patch.object(y, 'download', side_effect=download), patch.object(y.shutil, 'which', return_value='/bin/ffmpeg'):
                    with self.assertRaisesRegex(RuntimeError, 'already exists'):
                        y.media_download(info, URL, root, mode)
                self.assertEqual(target.read_bytes(), b'owner file')
                self.assertEqual(list(root.iterdir()), [target])

    def test_all_output_templates_escape_percent_in_parent_directory(self):
        root = Path('/tmp/%(title)s/100% ready')
        for name in ('captions', 'audio', '100% video'):
            value = y.output_template(root, name)
            self.assertEqual(value % {'ext': 'json3'}, str(root / name) + '.json3')

    def test_metadata_playlist_refused_before_media_or_model(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(y, 'download', return_value=json.dumps(dict(INFO, _type='playlist'))), \
                    patch.object(y, 'media_download') as media, redirect_stderr(io.StringIO()):
                self.assertEqual(y.main(['--mode', 'music', '--output-dir', directory, URL]), 1)
                media.assert_not_called()

    def test_english_fallback_does_not_guess_foreign_or_unknown_original(self):
        with tempfile.TemporaryDirectory() as directory:
            for language in ('es', None):
                with self.subTest(language=language), patch.object(y, 'download') as download:
                    with self.assertRaisesRegex(RuntimeError, 'original English'):
                        y.local_transcript(URL, Path(directory), 'en', info=dict(INFO, language=language))
                    download.assert_not_called()

    def authority(self, root, verify):
        class LicenseError(Exception):
            pass
        spec = types.SimpleNamespace(asset_id='faster-whisper-small-en', install_name='selected')
        api = types.SimpleNamespace(Installer=lambda path: types.SimpleNamespace(
            asset_destination=lambda unused: str(Path(path) / 'assets/selected')))
        fake = types.ModuleType('content_models')
        fake._api = lambda: api
        fake._license = lambda: types.SimpleNamespace(LicenseError=LicenseError)
        fake._verified_catalog = lambda unused: types.SimpleNamespace(require_asset=lambda unused: spec)
        fake.apps_root = lambda: str(root)
        fake._verify = lambda *args: verify(LicenseError)
        return fake

    def test_refused_licence_and_missing_authority_are_actionable_without_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def refuse(error):
                raise error('no covering receipt')
            fake = self.authority(root, refuse)
            for module in (None, fake):
                with self.subTest(module=module), patch.dict('sys.modules', {'content_models': module}), \
                        patch.object(y, 'download') as download, patch.object(y, 'run') as run:
                    with self.assertRaisesRegex(RuntimeError, 'kilix models install faster-whisper-small-en'):
                        y.local_transcript(URL, root, 'en', info=INFO)
                    download.assert_not_called(); run.assert_not_called()

    def test_fallback_checks_generation_and_actual_last_chunk_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); work = root / 'work'; work.mkdir()
            identity = 'a' * 40
            generation = root / 'whisper/generations' / identity
            (generation / 'bin').mkdir(parents=True)
            binary = generation / 'bin/kilix-whisper-stt'; binary.write_text('provider'); binary.chmod(0o700)
            (root / 'whisper/current').symlink_to(generation)
            provider = root / 'whisper/current/bin/kilix-whisper-stt'
            (generation / 'REF').write_text(f'kilix-whisper-stt={identity} layout=copied-no-weights-1\n')
            events = []
            fake = self.authority(root, lambda error: events.append('verified'))
            def run(args, **kwargs):
                self.assertTrue(events and events[0] == 'verified')
                if args[-1] == '--print-path': return str(provider) + '\n'
                if args[-1] == '--print-ref': return 'kilix-whisper-stt=' + identity + '\n'
                if args[0] == 'ffmpeg': wav_file(work / 'chunk-00000.wav', 30); return None
                self.assertEqual(args[0], str(provider)); self.assertIn('transcribe', args)
                self.assertIn('--model', args); self.assertEqual(args[-4:], ['--threads', '2', '--beam', '5'])
                return 'Recognized speech\n'
            def download(*args, **kwargs):
                events.append('download'); (work / 'audio.wav').write_bytes(b'downloaded audio')
            with patch.dict('sys.modules', {'content_models': fake}), \
                    patch.object(y, 'download', side_effect=download), patch.object(y, 'run', side_effect=run):
                self.assertEqual(y.local_transcript(URL, work, 'en', info=INFO), [(0, 30, 'Recognized speech')])
                (generation / 'REF').write_text('wrong-generation')
                events.clear()
                with self.assertRaisesRegex(RuntimeError, 'pinned local provider'):
                    y.local_transcript(URL, work, 'en', info=INFO)
                self.assertEqual(events, ['verified'])


if __name__ == '__main__':
    unittest.main()
