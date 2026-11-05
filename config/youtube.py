#!/usr/bin/env python3
"""Download a video's transcript, music or video using uv-managed yt-dlp."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from urllib.parse import urlsplit

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
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.hostname not in {
            'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'} or parsed.username:
        raise argparse.ArgumentTypeError('use an HTTPS YouTube video URL')
    return value


def basename(title, identity):
    text = ''.join(c for c in str(title) if not unicodedata.category(c).startswith('C'))
    text = re.sub(r'[/\\:*?"<>|]', '_', text).strip(' .')
    # Leave room for the ID, suffix and filesystem's byte-based NAME_MAX.
    text = text.encode('utf-8')[:180].decode('utf-8', 'ignore') or 'YouTube'
    identity = re.sub(r'[^A-Za-z0-9_-]', '', str(identity))[:32]
    return f'{text} [{identity}]'


def caption_track(info, language):
    # Never pick another auto-dub's *-orig track or a machine translation.
    for field in ('subtitles', 'automatic_captions'):
        tracks = info.get(field) or {}
        for key in (language + '-orig', language):
            if key in tracks and any(x.get('ext') == 'json3' for x in tracks[key]):
                return key, field == 'automatic_captions'
    return None


def caption_rows(document):
    rows = []
    for event in document.get('events', []):
        text = ' '.join(''.join(segment.get('utf8', '') for segment in event.get('segs', [])).split())
        if not text:
            continue
        start = max(0, float(event.get('tStartMs', 0)) / 1000)
        end = start + max(0, float(event.get('dDurationMs', 0)) / 1000)
        if rows and text == rows[-1][2] and start < rows[-1][1]:
            continue
        rows.append((start, end, text))
    return rows


def usable_captions(rows, duration):
    """Check completeness, not semantic accuracy; expose this distinction in metadata."""
    if not rows or not duration or duration <= 0:
        return False
    words = sum(len(row[2].split()) for row in rows)
    covered = 0.0
    last = 0.0
    for start, end, _ in sorted(rows):
        end = min(end, duration)
        covered += max(0, end - max(last, start))
        last = max(last, end)
    return (words >= max(3, duration / 12) and covered / duration >= .65
            and rows[0][0] <= max(15, duration * .05)
            and max(r[1] for r in rows) >= duration * .9)


def timestamp(seconds):
    seconds = int(seconds)
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'


def local_transcript(url, work, language):
    if language != 'en':
        raise RuntimeError('the managed Whisper-small English fallback requires --language en')
    import content_models
    api = content_models._api()
    spec = content_models._verified_catalog(api).require_asset('faster-whisper-small-en')
    root = content_models.apps_root()
    # Reuse the normal authority: a model directory alone is not a licence receipt.
    content_models._verify(api, content_models._license(), spec, root, io.StringIO())
    model = Path(root) / 'assets' / spec.install_name
    storage = Path(os.environ.get('KILIX_STORAGE_HOME') or
                   Path(os.environ.get('GPU_TERMINAL_HOME') or Path.home() / '.local/gpu_terminal') / 'kilix')
    data = Path(os.environ.get('KILIX_DATA_HOME') or storage / 'data')
    provider = data / 'voice/whisper/current/bin/kilix-whisper-stt'
    if not provider.is_file():
        raise RuntimeError('prepare the local Whisper provider with kilix stt --install whisper-small-en')
    download('-f', 'bestaudio/best', '-o', work / 'audio.%(ext)s', '--', url)
    audio = next(p for p in work.glob('audio.*') if p.suffix not in {'.part', '.ytdl'})
    # The managed provider accepts at most ten minutes per request.
    run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(audio), '-vn', '-ar', '16000',
         '-ac', '1', '-c:a', 'pcm_s16le', '-f', 'segment', '-segment_time', '540',
         str(work / 'chunk-%05d.wav')])
    rows = []
    for index, chunk in enumerate(sorted(work.glob('chunk-*.wav'))):
        text = run([str(provider), 'transcribe', '--model', str(model), '--audio', str(chunk),
                    '--threads', '2', '--beam', '5'], capture=True).strip()
        if text:
            rows.append((index * 540, (index + 1) * 540, text))
    if not rows:
        raise RuntimeError('the local model returned no speech')
    return rows


def transcript(info, url, output, language, force, work):
    selected = caption_track(info, language)
    rows = []
    source = 'local-whisper-small-en'
    if selected and not force:
        key, automatic = selected
        try:
            download('--skip-download', '--write-auto-subs' if automatic else '--write-subs',
                     '--sub-langs', key, '--sub-format', 'json3', '-o', work / 'captions.%(ext)s', '--', url)
            rows = caption_rows(json.loads((work / f'captions.{key}.json3').read_text()))
        except (subprocess.SubprocessError, OSError, ValueError) as error:
            print(f'Caption download failed; trying the local model: {error}', file=sys.stderr)
        if usable_captions(rows, info.get('duration')):
            source = 'youtube-automatic-captions' if automatic else 'youtube-manual-captions'
        else:
            rows = []
    if not rows:
        print('Using the local transcription model.', file=sys.stderr)
        rows = local_transcript(url, work, language)
    name = basename(info['title'], info['id'])
    target = output / (name + '.txt')
    metadata = output / (name + '.json')
    if target.exists() or metadata.exists():
        raise RuntimeError(f'output already exists: {target}')
    lines = [info['title'], url, f'Source: {source}', f'Language: {language}', '']
    # Group short caption cues into timestamped paragraphs, without rewording.
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
               'words': sum(len(row[2].split()) for row in rows),
               'quality_check': 'caption completeness only; recognition accuracy is not guaranteed',
               'transcript_sha256': hashlib.sha256(body.encode()).hexdigest()}
    with target.open('x') as stream:
        stream.write(body)
    with metadata.open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print(target)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(prog='kilix youtube', description=__doc__)
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
        if args.mode == 'transcript':
            with tempfile.TemporaryDirectory(prefix='.kilix-youtube-', dir=output) as temporary:
                transcript(info, url, output, args.language, args.force_transcribe, Path(temporary))
        else:
            options = ['-x', '--audio-format', 'mp3'] if args.mode == 'music' else ['-f', 'bv*+ba/b', '--merge-output-format', 'mkv']
            # Literal metadata percent signs must not become yt-dlp template directives.
            template = str(output / basename(info['title'], info['id'])).replace('%', '%%') + '.%(ext)s'
            download(*options, '--no-overwrites', '-o', template, '--', url)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, EOFError) as error:
        print(f'kilix youtube: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
