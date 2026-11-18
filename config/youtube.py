#!/usr/bin/env python3
"""Download a video's transcript, music or video using uv-managed yt-dlp."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from urllib.parse import parse_qs, urlsplit
import wave

YTDLP = 'yt-dlp==2026.10.26'
FOLDERS = {'transcript': 'transcripts', 'music': 'music', 'video': 'videos'}


def run(argv, *, capture=False, timeout=7200):
    return subprocess.run(argv, check=True, text=True, stdout=subprocess.PIPE if capture else None,
                          timeout=timeout).stdout


def download(*args, capture=False):
    if not shutil.which('uv'):
        raise RuntimeError('uv is required for YouTube downloads')
    return run(['uv', 'tool', 'run', '--isolated', '--from', YTDLP, 'yt-dlp',
                '--ignore-config', '--no-playlist', '--socket-timeout', '30',
                '--retries', '2', *map(str, args)], capture=capture)


def youtube_url(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.hostname not in {
                'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443)):
            raise ValueError
        if parsed.hostname == 'youtu.be':
            identity = parsed.path.removeprefix('/')
        elif parsed.path == '/watch':
            values = parse_qs(parsed.query).get('v', [])
            identity = values[0] if len(values) == 1 else ''
        else:
            match = re.fullmatch(r'/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})', parsed.path)
            identity = match[1] if match else ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', identity):
            raise ValueError
    except ValueError as error:
        raise argparse.ArgumentTypeError('use an HTTPS YouTube video URL, not a channel or playlist') from error
    # A full-video operation must not inherit a playlist or start-time selector.
    return 'https://www.youtube.com/watch?v=' + identity


def plain_text(value):
    return ''.join(c for c in str(value) if not unicodedata.category(c).startswith('C'))


def basename(title, identity):
    text = re.sub(r'[/\\:*?"<>|]', '_', plain_text(title)).strip(' .')
    # Leave room for the ID, suffix and filesystem's byte-based NAME_MAX.
    text = text.encode('utf-8')[:180].decode('utf-8', 'ignore') or 'YouTube'
    identity = re.sub(r'[^A-Za-z0-9_-]', '', str(identity))[:32]
    return f'{text} [{identity}]'


def output_template(directory, name):
    return str(directory / name).replace('%', '%%') + '.%(ext)s'


def caption_formats(formats):
    # Plain language keys may be machine translations. Check the selected URL,
    # not just the key or display name; yt-dlp marks translations with tlang.
    result = []
    for item in formats:
        if not isinstance(item, dict) or item.get('ext') != 'json3':
            continue
        url = item.get('url')
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or 'tlang' in parse_qs(parsed.query, keep_blank_values=True):
                continue
        except ValueError:
            continue
        result.append(item)
    return result


def caption_track(info, language):
    for field in ('subtitles', 'automatic_captions'):
        tracks = info.get(field) or {}
        for key in (language + '-orig', language):
            if caption_formats(tracks.get(key, [])):
                return key, field == 'automatic_captions'
    return None


def caption_rows(document):
    rows = []
    for event in document.get('events', []):
        text = ' '.join(''.join(segment.get('utf8', '') for segment in event.get('segs', [])).split())
        text = plain_text(text)
        if not text:
            continue
        start = float(event.get('tStartMs', 0)) / 1000
        end = start + float(event.get('dDurationMs', 0)) / 1000
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise ValueError('invalid caption timestamps')
        if rows and text == rows[-1][2] and start < rows[-1][1]:
            continue
        rows.append((start, end, text))
    return sorted(rows)


def caption_coverage(rows, duration):
    """A disclosed coverage heuristic, not proof of recognition accuracy."""
    result = {'usable': False, 'covered_fraction': 0.0, 'maximum_gap_seconds': None}
    if not rows or type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return result
    covered = last = gap = 0.0
    for start, end, _ in sorted(rows):
        start, end = min(start, duration), min(end, duration)
        gap = max(gap, start - last)
        covered += max(0, end - max(last, start))
        last = max(last, end)
    gap = max(gap, duration - last)
    words = sum(len(row[2].split()) for row in rows)
    result.update(covered_fraction=covered / duration, maximum_gap_seconds=gap)
    result['usable'] = (words >= max(3, duration / 12) and covered / duration >= .9
                        and gap <= min(30, max(5, duration * .01)))
    return result


def usable_captions(rows, duration):
    return caption_coverage(rows, duration)['usable']


def _first_use_terminal() -> bool:
    value = os.environ.get('KILIX_MODEL_AUTO_INSTALL')
    if value is not None and value.strip().casefold() in {'0', 'no', 'false', 'off'}:
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _whisper_provider_ready(provider: Path, generation: Path, identity: str, ref: str) -> bool:
    return bool(re.fullmatch(r'[0-9a-f]{40}', identity) and provider.is_file()
                and os.access(provider, os.X_OK)
                and provider.resolve() == generation / 'bin/kilix-whisper-stt'
                and (generation / 'REF').is_file()
                and (generation / 'REF').read_text().strip() == ref + ' layout=copied-no-weights-1')


def timestamp(seconds):
    seconds = int(seconds)
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'


def local_transcript(url, work, language, *, info):
    original = (info.get('language') or '').split('-')[0].lower()
    if language != 'en' or original != 'en':
        raise RuntimeError('the managed Whisper-small fallback supports original English audio only; '
                           'use original-language captions or a compatible local provider')
    setup = ('; run `kilix models install faster-whisper-small-en` in a terminal, '
             'then `kilix stt --install whisper-small-en`')
    try:
        import content_models
        api = content_models._api()
        lic = content_models._license()
    except (ImportError, RuntimeError, OSError, ValueError) as error:
        raise RuntimeError('local fallback authority is unavailable: ' + str(error) + setup) from error
    try:
        spec = content_models._verified_catalog(api).require_asset('faster-whisper-small-en')
        root = content_models.apps_root()
        # A model directory is not consent. First use on a terminal shows the
        # licence and waits for an answer. Anything else is the read-only check.
        ensure = getattr(content_models, 'ensure_installed', None)
        if ensure is not None:
            ensure('faster-whisper-small-en')
        else:
            content_models._verify(api, lic, spec, root, io.StringIO())
    except (lic.LicenseError, RuntimeError, OSError, ValueError) as error:
        raise RuntimeError('local fallback is not ready: ' + str(error) + setup) from error
    model = Path(api.Installer(root).asset_destination(spec))
    installer = Path(__file__).resolve().parents[1] / 'scripts/install-kilix-whisper-stt.sh'
    provider = Path(run(['bash', str(installer), '--print-path'], capture=True).strip())
    ref = run(['bash', str(installer), '--print-ref'], capture=True).strip()
    identity = ref.removeprefix('kilix-whisper-stt=')
    generation = provider.parents[2] / 'generations' / identity
    # A wrong pin is not a missing install. Only an absent provider is built.
    if not provider.is_file() and _first_use_terminal():
        run(['kilix', 'stt', '--install', 'whisper-small-en'])
        provider = Path(run(['bash', str(installer), '--print-path'], capture=True).strip())
        ref = run(['bash', str(installer), '--print-ref'], capture=True).strip()
        identity = ref.removeprefix('kilix-whisper-stt=')
        generation = provider.parents[2] / 'generations' / identity
    if not _whisper_provider_ready(provider, generation, identity, ref):
        raise RuntimeError('prepare the pinned local provider with '
                           '`kilix stt --install whisper-small-en`')
    # Select the known original English audio instead of an arbitrary auto-dub.
    audio_format = 'bestaudio[language^=en]/best[language^=en]'
    formats = info.get('formats') or []
    if not any((item.get('language') or '').split('-')[0] == 'en' for item in formats):
        if any(item.get('language') for item in formats):
            raise RuntimeError('no original English audio format is available')
        audio_format = 'bestaudio/best'  # original language was explicitly identified above
    download('-f', audio_format, '-o', output_template(work, 'audio'), '--', url)
    audio = [p for p in work.glob('audio.*') if p.is_file() and not p.is_symlink()
             and '.part' not in p.name and p.suffix != '.ytdl']
    if len(audio) != 1:
        raise RuntimeError('audio download did not produce one complete file')
    run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(audio[0]), '-vn', '-ar', '16000',
         '-ac', '1', '-c:a', 'pcm_s16le', '-f', 'segment', '-segment_time', '540',
         str(work / 'chunk-%05d.wav')])
    rows, position = [], 0.0
    for chunk in sorted(work.glob('chunk-*.wav')):
        with wave.open(str(chunk), 'rb') as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
                raise RuntimeError('local audio chunk has an unexpected PCM format')
            duration = wav.getnframes() / wav.getframerate()
            if not 0 < duration <= 600:
                raise RuntimeError('local audio chunk exceeds the provider limit')
        text = plain_text(run([str(provider), 'transcribe', '--model', str(model), '--audio', str(chunk),
                               '--threads', '2', '--beam', '5'], capture=True).strip())
        if text:
            rows.append((position, position + duration, text))
        position += duration
    if not rows:
        raise RuntimeError('the local model returned no speech')
    return rows


def refuse_existing(*paths):
    for path in paths:
        if os.path.lexists(path):
            raise RuntimeError(f'output already exists: {path}')


def publish_pair(target, metadata, body, receipt, work):
    staged = work / 'transcript.txt'
    staged.write_text(body, encoding='utf-8')
    staged_meta = work / 'transcript.json'
    staged_meta.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    os.link(staged, target)  # atomic no-overwrite, including dangling symlinks
    try:
        os.link(staged_meta, metadata)
    except OSError:
        # Roll back only this invocation's own link; preserve a competing file.
        try:
            if os.stat(target, follow_symlinks=False).st_ino == staged.stat().st_ino:
                target.unlink()
        except FileNotFoundError:
            pass
        raise


def transcript(info, url, output, language, force, work):
    name = basename(info['title'], info['id'])
    target, metadata = output / (name + '.txt'), output / (name + '.json')
    refuse_existing(target, metadata)  # before caption fetch, audio or inference
    selected = caption_track(info, language)
    rows = []
    source = 'local-whisper-small-en'
    coverage = None
    if selected and not force:
        key, automatic = selected
        field = 'automatic_captions' if automatic else 'subtitles'
        # Reuse the exact checked track URLs; re-extraction could replace a plain
        # language key with a translated track between metadata and download.
        captured = dict(info, subtitles={}, automatic_captions={})
        captured[field] = {key: caption_formats(info[field][key])}
        captured.pop('requested_subtitles', None)
        snapshot = work / 'caption-info.json'
        snapshot.write_text(json.dumps(captured), encoding='utf-8')
        try:
            download('--load-info-json', snapshot, '--skip-download',
                     '--write-auto-subs' if automatic else '--write-subs',
                     '--sub-langs', re.escape(key), '--sub-format', 'json3',
                     '-o', output_template(work, 'captions'))
            rows = caption_rows(json.loads((work / f'captions.{key}.json3').read_text(encoding='utf-8')))
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            print(f'Caption download failed; trying the local model: {error}', file=sys.stderr)
        coverage = caption_coverage(rows, info.get('duration'))
        if coverage['usable']:
            source = 'youtube-automatic-captions' if automatic else 'youtube-manual-captions'
        else:
            rows = []
    if not rows:
        print('Using the local transcription model.', file=sys.stderr)
        rows = local_transcript(url, work, language, info=info)
    lines = [plain_text(info['title']), url, f'Source: {source}', f'Language: {language}', '']
    paragraph, start = [], None
    for position, _, text in rows:
        if start is not None and position - start >= 30:
            lines.extend([f'[{timestamp(start)}] ' + ' '.join(paragraph), ''])
            paragraph, start = [], None
        if start is None:
            start = position
        paragraph.append(text)
    if paragraph:
        lines.extend([f'[{timestamp(start)}] ' + ' '.join(paragraph), ''])
    body = '\n'.join(lines)
    receipt = {'url': url, 'id': info['id'], 'title': info['title'], 'duration_seconds': info.get('duration'),
               'language': language, 'source': source, 'yt_dlp': YTDLP, 'segments': len(rows),
               'words': sum(len(row[2].split()) for row in rows), 'caption_coverage': coverage,
               'transcribed_through_seconds': rows[-1][1],
               'quality_check': 'coverage heuristic only; completeness and recognition accuracy are not guaranteed',
               'transcript_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()}
    publish_pair(target, metadata, body, receipt, work)
    print(target)
    return target


def media_download(info, url, output, mode):
    if not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg is required for music and video downloads')
    name = basename(info['title'], info['id'])
    extensions = ('.mp3',) if mode == 'music' else ('.mkv', '.mp4', '.webm')
    refuse_existing(*(output / (name + ext) for ext in extensions))
    # yt-dlp and its postprocessors work only inside a fresh private directory.
    # Publish a completed result with no-overwrite semantics in the output dir.
    with tempfile.TemporaryDirectory(prefix='.kilix-youtube-', dir=output) as temporary:
        work = Path(temporary)
        options = ['-x', '--audio-format', 'mp3'] if mode == 'music' else [
            '-f', 'bv*+ba/b', '--merge-output-format', 'mkv']
        template = output_template(work, name)
        download(*options, '--no-overwrites', '-o', template, '--', url)
        files = [p for p in work.iterdir() if p.is_file() and not p.is_symlink()
                 and p.stem == name and p.suffix.lower() in extensions]
        if len(files) != 1:
            raise RuntimeError('download did not produce one complete media file')
        target = output / files[0].name
        refuse_existing(target)
        os.link(files[0], target)
    print(target)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(prog='kilix youtube', description=__doc__,
        epilog='Uses uv and ffmpeg. Some YouTube formats also require a supported yt-dlp JavaScript runtime.')
    parser.add_argument('url', nargs='?', type=youtube_url)
    parser.add_argument('--mode', choices=FOLDERS)
    parser.add_argument('--language', default='en')
    parser.add_argument('--force-transcribe', action='store_true', help='use the local model even if captions exist')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args(argv)
    try:
        if not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', args.language):
            raise ValueError('invalid caption language')
        if not args.mode:
            print('1. Transcript\n2. Music\n3. Video')
            choice = input('Download [1]: ').strip().lower() or '1'
            args.mode = {'1': 'transcript', '2': 'music', '3': 'video'}.get(choice, choice)
            if args.mode not in FOLDERS:
                raise ValueError('choose transcript, music or video')
        url = args.url or youtube_url(input('YouTube URL: ').strip())
        output = (args.output_dir or Path.home() / 'Downloads' / FOLDERS[args.mode]).expanduser()
        output.mkdir(parents=True, exist_ok=True)
        info = json.loads(download('--dump-single-json', '--skip-download', '--', url, capture=True))
        if info.get('is_live'):
            raise ValueError('wait until the live stream has finished')
        if (info.get('_type', 'video') != 'video' or info.get('id') != parse_qs(urlsplit(url).query)['v'][0]
                or not isinstance(info.get('title'), str)):
            raise ValueError('YouTube did not return the requested single video')
        if args.mode == 'transcript':
            with tempfile.TemporaryDirectory(prefix='.kilix-youtube-', dir=output) as temporary:
                transcript(info, url, output, args.language, args.force_transcribe, Path(temporary))
        else:
            media_download(info, url, output, args.mode)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, EOFError, wave.Error) as error:
        print(f'kilix youtube: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
