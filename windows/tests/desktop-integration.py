"""Live Windows desktop proof using only a newly launched, owned test window.

Run with the packaged Python: kitty-console.exe +runpy
"import runpy,sys;sys.argv=['desktop-integration.py',r'PACKAGE'];runpy.run_path(r'PACKAGE/tests/desktop-integration.py',run_name='__main__')"
Requires an unlocked desktop for the invoking Windows user.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
import struct
import shutil
import sys
import tempfile
import time
import uuid


@contextmanager
def fixture_directory():
    # The MinGW Python build can map mkdtemp's POSIX 0700 to an elevated
    # administrator-only ACL. Inherit the user's ordinary Temp ACL so the
    # limited interactive fixture can write its synthetic test state.
    path = Path(tempfile.gettempdir()) / ('Kilix desktop test ' + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def run(root, evidence=None):
    sys.path.insert(0, str(root / 'wsl-bin/config'))
    from kilix_sdk.windows import DesktopClient, DesktopError
    client = DesktopClient(root=root)
    checks = []

    def check(value, name):
        if not value:
            raise RuntimeError(name)
        checks.append(name)
        print('PASS ' + name, flush=True)

    with fixture_directory() as temporary:
        temporary = Path(temporary)
        state_path = temporary / 'state.json'
        target = None
        launch = client.call('launch', params={'argv': [
            str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
            '-NoLogo', '-NoProfile', '-STA', '-WindowStyle', 'Hidden',
            '-ExecutionPolicy', 'Bypass', '-File', str(root / 'tests/desktop-fixture.ps1'),
            '-StatePath', str(state_path)]})
        print('LAUNCHED ' + json.dumps(launch), flush=True)

        def state():
            for _ in range(20):
                try:
                    return json.loads(state_path.read_text(encoding='utf-8-sig'))
                except (OSError, ValueError):
                    time.sleep(.1)
            raise RuntimeError('Fixture did not write its state')

        def call(operation, **params):
            return client.call(operation, params=params, target=target)

        def wait_state(field, expected):
            deadline = time.monotonic() + 3
            while True:
                current = state()
                if current.get(field) == expected or time.monotonic() >= deadline:
                    return current
                time.sleep(.05)

        try:
            # Window creation can follow process creation; discovery never
            # guesses a window from its title alone.
            for _ in range(6):
                rows = client.call('windows', params={'pid': launch['pid']})['windows']
                matches = [row for row in rows if row['title'] == 'Kilix desktop automation fixture']
                if len(matches) == 1:
                    target = matches[0]['target']
                    break
                time.sleep(.2)
            check(target is not None, 'launch and discover owned window')
            check(target['pid'] == state()['pid'], 'process identity matches fixture')
            call('focus')
            call('type', text='Kilix café 字 😀')
            check(wait_state('text', 'Kilix café 字 😀')['text'] == 'Kilix café 字 😀', 'Unicode SendInput reaches edit control')
            call('key', keys=['CTRL', 'A'])
            call('type', text='literal & $(text) "quote"')
            current = wait_state('text', 'literal & $(text) "quote"')
            check('A:Control' in current['keys'] and current['text'] == 'literal & $(text) "quote"', 'key chord and literal text')
            tree = call('inspect', depth=5, limit=300)
            check('fixture-password-do-not-expose' not in json.dumps(tree), 'password values excluded from inspection')
            nodes = tree['nodes']
            edits = [n for n in nodes if n.get('automation_id') == 'sampleText']
            buttons = [n for n in nodes if n.get('name') == 'Invoke me']
            check(len(edits) == 1 and len(buttons) == 1, 'UI Automation discovers edit and button')
            call('set-value', element=edits[0]['id'], text='UIA value 字')
            check(wait_state('text', 'UIA value 字')['text'] == 'UIA value 字', 'UI Automation sets exact value')
            call('invoke', element=buttons[0]['id'])
            check(wait_state('counter', 1)['counter'] == 1, 'UI Automation invokes exact button')
            call('focus')
            window = client.call('windows', params={'pid': target['pid']})['windows'][0]
            rect = buttons[0]['rect']
            x = int(rect['x'] + rect['width'] // 2 - window['rect']['x'])
            y = int(rect['y'] + rect['height'] // 2 - window['rect']['y'])
            call('click', x=x, y=y)
            check(wait_state('counter', 2)['counter'] == 2, 'window-relative physical mouse input')
            edit_rect = edits[0]['rect']
            edit_x = int(edit_rect['x'] + edit_rect['width'] // 2 - window['rect']['x'])
            edit_y = int(edit_rect['y'] + edit_rect['height'] // 2 - window['rect']['y'])
            call('click', x=edit_x, y=edit_y)
            call('scroll', x=edit_x, y=edit_y, delta=120)
            wheel = wait_state('wheel', 120)['wheel']
            check(wheel == 120, f'targeted mouse wheel input (received {wheel})')
            screenshot = temporary / 'screen 字.png'
            capture = call('screenshot', path=str(screenshot))
            png = screenshot.read_bytes()
            check(png[:8] == b'\x89PNG\r\n\x1a\n' and all(struct.unpack('>II', png[16:24])), 'PNG screenshot with Unicode path')
            if evidence is not None:
                evidence.mkdir(parents=True, exist_ok=True)
                shutil.copy2(screenshot, evidence / 'desktop-fixture.png')
                (evidence / 'inspect.json').write_text(json.dumps(tree, ensure_ascii=True, indent=2), encoding='utf-8')
            try:
                call('screenshot', path=str(screenshot))
            except DesktopError:
                check(screenshot.read_bytes() == png, 'screenshot preserves existing file')
            else:
                raise RuntimeError('Screenshot unexpectedly overwrote existing file')
            requested = {'x': 180, 'y': 150, 'width': 700, 'height': 500}
            movement = call('move', **requested)
            moved = client.call('windows', params={'pid': target['pid']})['windows'][0]['rect']
            check(movement['rect'] == moved and all(abs(moved[k] - v) <= 1 for k, v in requested.items()),
                  'move reports physical bounds including legacy DPI rounding')
            call('minimize')
            rows = client.call('windows', params={'pid': target['pid']})['windows']
            check(len(rows) == 1 and rows[0]['minimized'], 'minimize reports actual state')
            call('restore')
            call('maximize')
            call('restore')
            checks.append('restore and maximize')
            stale = dict(target, started=str(int(target['started']) + 1))
            try:
                client.call('close', target=stale)
            except DesktopError:
                check(not state()['closed'], 'stale process identity refuses mutation')
            else:
                raise RuntimeError('Stale identity was accepted')
            call('close')
            check(wait_state('closed', True)['closed'], 'WM_CLOSE closes only owned fixture')
            target = None
        finally:
            if target is not None:
                try:
                    client.call('close', target=target)
                    wait_state('closed', True)
                except DesktopError:
                    pass
    return {'ok': True, 'checks': checks}


if __name__ == '__main__':
    print(json.dumps(run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]) if len(sys.argv) > 2 else None), ensure_ascii=True))
