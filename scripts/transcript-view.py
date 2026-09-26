#!/usr/bin/env python3
"""Open all retained pane output as searchable text, without replaying escapes."""

import codecs
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


class PlainText:
    """Streaming terminal-control filter; sequences can span input chunks."""

    def __init__(self):
        self.state = 'text'
        self.after_cr = False

    def feed(self, text):
        result = []
        for char in text:
            code = ord(char)
            if self.state == 'string':
                if char in ('\x07', '\x9c'):
                    self.state = 'text'
                elif char == '\x1b':
                    self.state = 'string-escape'
            elif self.state == 'string-escape':
                self.state = 'text' if char == '\\' else 'string'
            elif self.state == 'csi':
                if 0x40 <= code <= 0x7e:
                    self.state = 'text'
                elif char == '\x1b':
                    self.state = 'escape'
            elif self.state == 'escape':
                if char == '[':
                    self.state = 'csi'
                elif char in ']P_^X':
                    self.state = 'string'
                elif not 0x20 <= code <= 0x2f:
                    self.state = 'text'
            elif char == '\x1b':
                self.state = 'escape'
            elif char == '\x9b':
                self.state = 'csi'
            elif char in '\x90\x98\x9d\x9e\x9f':
                self.state = 'string'
            elif char == '\r':
                result.append('\n')
                self.after_cr = True
            elif char == '\n':
                if not self.after_cr:
                    result.append(char)
                self.after_cr = False
            elif char == '\t' or (code >= 32 and not 0x7f <= code <= 0x9f):
                result.append(char)
                self.after_cr = False
        return ''.join(result)


def find_log(directory, session):
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', session) or session in ('.', '..'):
        raise ValueError('Invalid pane session identifier.')
    for path in (directory / f'{session}.log',
                 directory / 'recent' / f'{session}.log.zst',
                 directory / 'archive' / f'{session}.log.zst'):
        if path.is_file():
            return path
    raise ValueError('No saved log for this pane. Enable session logging in Settings '
                     'before starting a new pane; expired output cannot be recovered.')


def copy_text(source, destination, size=None):
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    clean = PlainText()
    while size is None or size > 0:
        data = source.read(65536 if size is None else min(65536, size))
        if not data:
            break
        if size is not None:
            size -= len(data)
        destination.write(clean.feed(decoder.decode(data)))
    destination.write(clean.feed(decoder.decode(b'', final=True)))


def snapshot(path, destination):
    if path.suffix == '.zst':
        if not shutil.which('zstd'):
            raise ValueError('Install zstd to read archived pane logs.')
        with subprocess.Popen(['zstd', '-dcq', '--', str(path)], stdout=subprocess.PIPE) as process:
            copy_text(process.stdout, destination)
            if process.wait():
                raise ValueError('The archived pane log could not be read.')
    else:
        with path.open('rb') as source:
            # Read to the click-time end, even if a busy pane keeps writing.
            copy_text(source, destination, os.fstat(source.fileno()).st_size)


def main():
    if len(sys.argv) != 3:
        print('usage: transcript-view.py DIRECTORY SESSION', file=sys.stderr)
        return 2
    try:
        path = find_log(Path(sys.argv[1]), sys.argv[2])
        if not sys.stdout.isatty():
            snapshot(path, sys.stdout)
            return 0
        pager = shutil.which('less')
        if not pager:
            raise ValueError('Install less to browse pane logs.')
        with tempfile.NamedTemporaryFile(mode='w+', encoding='utf-8', prefix='kilix-pane-log-') as output:
            output.write('Pane log — all retained output at opening time\n'
                         'g: first line · G: last line · /: search · q: close\n'
                         'Older output may have expired under your session logging limit.\n\n')
            snapshot(path, output)
            output.flush()
            env = dict(os.environ, LESS='', LESSOPEN='', LESSCLOSE='',
                       LESSSECURE='1', LESSHISTFILE='/dev/null')
            return subprocess.call([pager, '-i', '-M', '+G', '--', output.name], env=env)
    except (OSError, ValueError) as error:
        print(f'Pane log: {error}', file=sys.stderr)
        if sys.stdin.isatty():
            input('Press Enter to close this tab. ')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
