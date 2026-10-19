"""Exercise desktop automation with owned Notepad, Explorer and browser windows.

Run with the packaged Python on an unlocked Windows desktop. The test creates
its own documents and browser profile, verifies saved contents and UI state,
and closes only windows it discovers through those unique test identities.
Requires Windows 10's Notepad and Explorer, plus Chrome or Edge.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid


def run(root, evidence):
    sys.path.insert(0, str(root / 'wsl-bin/config'))
    from kilix_sdk.windows import DesktopClient, DesktopError
    client = DesktopClient(root=root)
    checks, owned = [], []
    succeeded = False
    work = Path(tempfile.gettempdir()) / ('Kilix apps ' + uuid.uuid4().hex)
    work.mkdir()  # Inherit the user's Temp ACL for the interactive worker.
    evidence.mkdir(parents=True, exist_ok=True)

    def check(value, name):
        if not value:
            raise RuntimeError(name)
        checks.append(name)
        print('PASS ' + name, flush=True)

    def call(operation, target=None, **params):
        return client.call(operation, target=target, params=params, timeout=30)

    def window(**filters):
        call('wait', state='visible', wait_ms=15000, **filters)
        rows = call('windows', **filters)['windows']
        if len(rows) != 1:
            raise RuntimeError('Expected one owned window: ' + repr(filters))
        target = rows[0]['target']
        if target not in owned:
            owned.append(target)
        return target

    def close(target):
        call('close', target)
        call('wait', target, state='closed', wait_ms=10000)
        owned.remove(target)

    def screenshot(target, name):
        result = client.capture_to_local(evidence / (name + '.png'), target=target)
        check(Path(result['path']).is_file(), name + ' screenshot saved')

    browser = next((p for p in (
        Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
        Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
    ) if p.is_file()), None)
    if browser is None:
        raise RuntimeError('Install Chrome or Edge to qualify the browser workflow.')

    try:
        status = call('status')
        check(status['interactive'] and not status['locked'], 'desktop is interactive and unlocked')

        # Save As exercises a real modal window, a named control, Unicode input,
        # and an independent disk read after the dialog has actually closed.
        source, saved = work / 'source.txt', work / 'saved café.txt'
        source.write_text('Original test document', encoding='utf-8')
        launch = call('launch', argv=[str(Path(os.environ['SystemRoot']) / 'system32/notepad.exe'), str(source)])
        editor = window(app='notepad.exe', pid=launch['pid'], title='source')
        call('focus', editor)
        call('key', editor, keys=['CTRL', 'A'])
        text = 'Kilix desktop café 字 😀 — literal & $(text)'
        call('type', editor, text=text)
        call('key', editor, keys=['CTRL', 'SHIFT', 'S'])
        dialog = window(app='notepad.exe', pid=launch['pid'], title='Save As')
        filename = {'automation_id': '1001', 'control_type': 'Edit'}
        call('wait', dialog, state='enabled', selector=filename, wait_ms=10000)
        values = call('inspect', dialog, depth=8, limit=500)['nodes']
        # Locate the edit through UIA, then enter the filename with keyboard
        # input so the common dialog receives its normal edit notifications.
        field = next(n for n in values if n.get('automation_id') == '1001' and n.get('control_type') == 'Edit')
        bounds = call('windows', pid=launch['pid'], title='Save As')['windows'][0]['rect']
        call('focus', dialog)
        call('click', dialog, x=int(field['rect']['x'] + field['rect']['width'] / 2 - bounds['x']),
             y=int(field['rect']['y'] + field['rect']['height'] / 2 - bounds['y']))
        call('key', dialog, keys=['CTRL', 'A'])
        call('type', dialog, text=str(saved))
        call('key', dialog, keys=['TAB'])
        values = call('inspect', dialog, depth=8, limit=500)['nodes']
        check(any(n.get('automation_id') == '1001' and n.get('control_type') == 'Edit'
                  and n.get('value') == str(saved) for n in values), 'Save As filename control holds the requested path')
        call('invoke', dialog, selector={'automation_id': '1', 'control_type': 'Button'})
        call('wait', dialog, state='closed', wait_ms=10000)
        owned.remove(dialog)
        deadline = time.monotonic() + 10
        while not saved.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        check(saved.exists(), 'Save As creates the requested file; observed files: ' + repr([p.name for p in work.iterdir()]))
        check(saved.read_text(encoding='utf-8-sig') == text, 'Notepad Save As preserves exact Unicode and literal text')
        check(source.read_text(encoding='utf-8') == 'Original test document', 'Save As leaves the source document unchanged')
        call('wait', state='visible', app='notepad.exe', pid=launch['pid'], title=saved.stem, wait_ms=10000)
        screenshot(editor, 'notepad')
        close(editor)

        # Explorer delegates to the shell process, so select the uniquely named
        # directory instead of assuming the short-lived launch PID owns its UI.
        folder = work / ('Kilix Explorer ' + uuid.uuid4().hex[:10])
        folder.mkdir()
        opened = folder / ('Opened ' + uuid.uuid4().hex[:10] + '.txt')
        opened.write_text('Document opened through Explorer', encoding='utf-8')
        call('launch', argv=[str(Path(os.environ['SystemRoot']) / 'explorer.exe'), str(folder)])
        explorer = window(app='explorer.exe', title=folder.name)
        call('focus', explorer)
        call('key', explorer, keys=['CTRL', 'L'])
        call('type', explorer, text=str(opened))
        call('key', explorer, keys=['ENTER'])
        editor = window(app='notepad.exe', title=opened.stem)
        check(opened.read_text(encoding='utf-8') == 'Document opened through Explorer', 'Explorer opens the owned text document')
        close(editor)
        call('focus', explorer)
        screenshot(explorer, 'explorer')
        close(explorer)

        # A new profile keeps the test independent of user tabs and credentials.
        title = 'Kilix Browser ' + uuid.uuid4().hex[:10]
        page = work / 'form.html'
        profile = work / 'browser-profile'
        profile.mkdir()
        page.write_text('''<!doctype html><html lang="en"><meta charset="utf-8">
<title>''' + title + '''</title><style>body{font:24px sans-serif;margin:48px}input,button{font:inherit}</style>
<label for="message">Kilix message</label><input id="message">
<button onclick="document.getElementById('result').textContent=document.getElementById('message').value;document.title='''
                        + "'" + title + " applied'" + '''">Apply message</button>
<p id="result" role="status" aria-live="polite">Waiting</p></html>''', encoding='utf-8')
        call('launch', argv=[str(browser), '--user-data-dir=' + str(profile), '--no-first-run',
                            '--no-default-browser-check', '--force-renderer-accessibility',
                            '--disable-background-networking', '--new-window', page.as_uri()])
        web = window(app=browser.name, title=title)
        call('focus', web)
        message = {'name': 'Kilix message', 'control_type': 'Edit'}
        call('wait', web, state='enabled', selector=message, wait_ms=15000)
        call('set-value', web, selector=message, text=text)
        call('invoke', web, selector={'name': 'Apply message', 'control_type': 'Button'})
        call('wait', state='visible', app=browser.name, title=title + ' applied', wait_ms=10000)
        result = call('find', web, selector={'name': text})
        check(bool(result.get('elements')) and not result.get('incomplete'),
              'browser form action exposes the exact submitted text')
        screenshot(web, 'browser')
        close(web)
        check(not owned, 'all owned application windows closed')
        result = {'ok': True, 'checks': checks, 'browser': browser.name}
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        succeeded = True
        return result
    finally:
        for target in reversed(owned):
            try:
                call('close', target)
            except DesktopError:
                pass
        # Retain failed-test files for diagnosis. Chromium may finish writing its
        # disposable profile after WM_CLOSE on an otherwise successful run.
        for attempt in range(40 if succeeded else 0):
            try:
                shutil.rmtree(work)
                break
            except OSError:
                if attempt == 39:
                    print('Test files retained for diagnosis: ' + str(work), file=sys.stderr)
                time.sleep(.25)
        if not succeeded:
            print('Test files retained for diagnosis: ' + str(work), file=sys.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', type=Path)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.application.resolve(), args.evidence.resolve()), ensure_ascii=True))
