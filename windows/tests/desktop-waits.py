"""Live qualification of condition polling and ambiguous UIA selections."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import uuid


def run(root):
    sys.path.insert(0, str(root / 'wsl-bin/config'))
    from kilix_sdk.windows import DesktopClient, DesktopError
    client = DesktopClient(root=root)
    directory = Path(tempfile.gettempdir()) / ('Kilix waits ' + uuid.uuid4().hex)
    directory.mkdir()
    state = directory / 'state.json'
    target = None
    checks = []

    def check(value, name):
        if not value:
            raise RuntimeError(name)
        checks.append(name)
        print('PASS ' + name, flush=True)

    def refused(code, action):
        try:
            action()
        except DesktopError as error:
            check(error.code == code, code + ' reported')
        else:
            raise RuntimeError('Expected ' + code)

    try:
        launched = client.call('launch', params={'argv': [
            str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
            '-NoLogo', '-NoProfile', '-STA', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass',
            '-File', str(root / 'tests/desktop-wait-fixture.ps1'), '-StatePath', str(state)]})
        client.wait('visible', app='powershell.exe', pid=launched['pid'], title='Kilix condition wait fixture')
        target = client.resolve_window(app='powershell.exe', pid=launched['pid'], title='Kilix condition wait fixture')
        client.wait('visible', target=target)
        selector = {'automation_id': 'delayedTarget', 'control_type': 'Button'}
        refused('wait_timeout', lambda: client.wait('enabled', target=target, wait_ms=250, timeout=10, **selector))
        client.call('invoke', target=target, params={'selector': {'automation_id': 'enableLater'}})
        result = client.wait('enabled', target=target, wait_ms=10000, timeout=15, **selector)
        check(result['polls'] > 1 and result['elapsed_ms'] >= 500, 'one native request waits for delayed enablement')
        check(json.loads(state.read_text())['enabled'], 'fixture confirms the observed enabled state')
        refused('ambiguous_element', lambda: client.call('invoke', target=target, params={'selector': {'name': 'Duplicate'}}))
        check(json.loads(state.read_text())['count'] == 0, 'ambiguous selection invokes neither control')
        refused('wait_timeout', lambda: client.wait('closed', target=target, automation_id='absent',
                                                    depth=0, limit=1, wait_ms=250, timeout=10))
        client.wait('closed', target=target, automation_id='absent', wait_ms=0, timeout=10)
        check(True, 'complete empty search establishes control absence')
        client.call('minimize', target=target)
        client.wait('present', target=target, wait_ms=0, timeout=10)
        refused('wait_timeout', lambda: client.wait('visible', target=target, wait_ms=250, timeout=10))
        refused('wait_timeout', lambda: client.wait('closed', target=target, wait_ms=250, timeout=10))
        client.call('restore', target=target)
        client.call('invoke', target=target, params={'selector': {'automation_id': 'closeLater'}})
        result = client.wait('closed', target=target, wait_ms=10000, timeout=15)
        check(result['polls'] > 1 and result['elapsed_ms'] >= 500, 'one native request waits for delayed close')
        check(json.loads(state.read_text())['closed'], 'fixture confirms window closure')
        client.wait('closed', target=target, wait_ms=0, timeout=10)
        target = None
        return {'ok': True, 'checks': checks}
    finally:
        if target is not None:
            try:
                client.call('close', target=target)
            except DesktopError:
                pass
        if state.exists():
            state.unlink()
        directory.rmdir()


if __name__ == '__main__':
    print(json.dumps(run(Path(sys.argv[1]).resolve()), ensure_ascii=True))
