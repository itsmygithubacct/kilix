"""Create a new note and screenshot using Kilix's Windows desktop SDK.

Run using the packaged Python. Creates its own directory under Windows Temp;
the note and PNG are retained and their paths are printed on success.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--application', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--text', default='Hello from Kilix. Café, 字 and 😀 are literal Unicode text.')
    args = parser.parse_args()
    root = args.application.resolve()
    sys.path.insert(0, str(root / 'wsl-bin/config'))
    from kilix_sdk.windows import DesktopClient

    client = DesktopClient(root=root)
    directory = Path(tempfile.gettempdir()) / ('Kilix example ' + uuid.uuid4().hex)
    directory.mkdir()
    note = directory / 'Kilix example.txt'
    note.write_text('', encoding='utf-8')
    launched = client.call('launch', params={
        'argv': [str(Path(os.environ['SystemRoot']) / 'system32/notepad.exe'), str(note)]})
    client.wait('visible', app='notepad.exe', pid=launched['pid'], title=note.stem, wait_ms=10000)
    target = client.resolve_window(app='notepad.exe', pid=launched['pid'], title=note.stem)
    client.call('focus', target=target)
    client.call('type', target=target, params={'text': args.text})
    client.call('key', target=target, params={'keys': ['CTRL', 'S']})
    deadline = time.monotonic() + 10
    while note.read_text(encoding='utf-8-sig') != args.text:
        if time.monotonic() >= deadline:
            raise RuntimeError('The note was not saved as expected; the window has been left open.')
        time.sleep(.1)
    capture = client.capture_to_local(directory / 'note.png', target=target)
    client.call('close', target=target)
    client.wait('closed', target=target, wait_ms=10000)
    print(json.dumps({'note': str(note), 'screenshot': capture}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
