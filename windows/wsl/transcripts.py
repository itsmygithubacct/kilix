"""Retained transcript readers and explicit native pane snapshots.

ConPTY does not provide the Linux persistent PTY recording broker. Captures
contain only output still retained by the terminal at the time of capture.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid


def positive(value):
    if not re.fullmatch(r'[1-9][0-9]*', value):
        raise argparse.ArgumentTypeError('pane ID must be a positive integer')
    return value


def directory():
    if os.environ.get('KILIX_TRANSCRIPT_DIR'):
        return Path(os.environ['KILIX_TRANSCRIPT_DIR']).expanduser().absolute()
    if os.environ.get('KILIX_STATE_DIRECTORY'):
        return Path(os.environ['KILIX_STATE_DIRECTORY']) / 'transcripts'
    if sys.platform == 'win32':
        return Path(os.environ['LOCALAPPDATA']) / 'Kilix/transcripts'
    return Path.home() / '.local/share/kilix-wsl/transcripts'


def live_text(pane, extent):
    import companion
    companion.require_control()
    result = subprocess.run([str(companion.KITTEN), '@', '--use-password', 'always',
                             'get-text', '--match=id:' + pane, '--extent=' + extent],
                            env=companion.environment(), capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError(result.stderr.decode('utf-8', errors='replace').strip() or 'Cannot read pane')
    return result.stdout


def session_id(value):
    name = value.removesuffix('.zst').removesuffix('.log')
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}', name):
        raise ValueError('Invalid transcript session identifier')
    return name


def locate(root, value):
    name = session_id(value)
    for path in (root / (name + '.log'), root / 'recent' / (name + '.log.zst'),
                 root / 'archive' / (name + '.log.zst')):
        if path.is_file() and not path.is_symlink():
            return path
    raise ValueError(f'No retained transcript for {name}; use transcript capture --pane ID')


def load_viewer(shared):
    path = shared / 'scripts/transcript-view.py'
    spec = importlib.util.spec_from_file_location('transcript_view', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(args, *, shared, read_live=live_text):
    parser = argparse.ArgumentParser(prog='kilix transcript', description=__doc__,
        epilog='No automatic ConPTY recording or pruning is performed. '
               'Use --help after a verb. Aliases: transcripts, session-log, session-logs.')
    subs = parser.add_subparsers(dest='verb')
    listing = subs.add_parser('list', aliases=['ls'], help='list retained logs, newest first')
    listing.add_argument('--json', action='store_true')
    path = subs.add_parser('path', help='print the directory or an existing session path')
    path.add_argument('session', nargs='?')
    for name in ('show', 'cat', 'view'):
        sub = subs.add_parser(name, help='read retained output' if name != 'view' else 'browse searchable text')
        sub.add_argument('session')
    capture = subs.add_parser('capture', help='save an explicit snapshot of live retained pane output')
    capture.add_argument('--pane', required=True, type=positive)
    capture.add_argument('--extent', choices=('screen', 'all'), default='all')
    subs.add_parser('clean', help='replay FILE, SESSION, stdin (-), or live --pane ID as text/JSON',
                    add_help=False)
    # Keep the replay library's parser authoritative, including its help.
    if args[:1] == ['clean']:
        root = directory()
        sys.path.insert(0, str(shared / 'third_party/kilix-transcript-clean/src'))
        from kilix_transcript_clean import sources
        from kilix_transcript_clean import __main__ as cleaner
        original = sources.resolve

        def resolve(source=None, pane=None):
            if pane is not None:
                try:
                    return read_live(positive(pane), 'all'), {'pane': pane, 'kind': 'live-snapshot'}
                except (OSError, ValueError, argparse.ArgumentTypeError) as error:
                    raise sources.SourceError(str(error)) from error
            if source is not None and not Path(source).is_file():
                try:
                    source = str(locate(root, source))
                except ValueError as error:
                    raise sources.SourceError(str(error)) from error
            return original(source)

        cleaner.resolve = resolve
        return cleaner.main(args[1:])
    ns = parser.parse_args(args)
    root = directory()
    if ns.verb in (None, 'list', 'ls'):
        rows = []
        for tier, pattern in (('retained', '*.log'), ('recent', 'recent/*.log.zst'),
                              ('archive', 'archive/*.log.zst')):
            for path in root.glob(pattern):
                if path.is_file() and not path.is_symlink():
                    info = path.stat()
                    rows.append({'session': session_id(path.name), 'path': str(path),
                                 'tier': tier, 'bytes': info.st_size, 'modified': info.st_mtime})
        rows.sort(key=lambda row: (row['modified'], row['session']), reverse=True)
        result = {'directory': str(root), 'automatic_recording': False, 'transcripts': rows}
        if getattr(ns, 'json', False):
            print(json.dumps(result))
        else:
            print('Retained logs and explicit snapshots; automatic ConPTY recording is unavailable.')
            print('directory: ' + json.dumps(str(root), ensure_ascii=False))
            for row in rows:
                print(f"{row['session']}  {row['tier']}  {row['bytes']} bytes")
            if not rows:
                print('(no transcripts yet)')
        return 0
    if ns.verb == 'path':
        print(locate(root, ns.session) if ns.session else root)
        return 0
    if ns.verb == 'capture':
        data = read_live(ns.pane, ns.extent)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = uuid.uuid4().hex[:16]
        path = root / (name + '.log')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
        print(json.dumps({'session': name, 'path': str(path), 'pane': ns.pane,
                          'bytes': len(data), 'kind': 'retained-output-snapshot'}))
        return 0
    path = locate(root, ns.session)
    if ns.verb in ('show', 'cat'):
        if path.suffix == '.zst':
            if not shutil.which('zstd'):
                raise ValueError('Install zstd to read archived logs')
            return subprocess.call(['zstd', '-dcq', '--', str(path)])
        with path.open('rb') as source:
            # A growing recording is bounded by its size when opened.
            remaining = os.fstat(source.fileno()).st_size
            while remaining:
                block = source.read(min(65536, remaining))
                if not block:
                    break
                sys.stdout.buffer.write(block)
                remaining -= len(block)
        return 0
    viewer = load_viewer(shared)
    previous = sys.argv
    try:
        sys.argv = ['transcript-view', str(root), session_id(ns.session)]
        return viewer.main()
    finally:
        sys.argv = previous
