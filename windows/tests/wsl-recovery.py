#!/usr/bin/env python3
"""Owned-window WSL recovery probe; the desktop runner closes/reopens the window.

Set KILIX_TEST_ISOLATED=1 and KILIX_TEST_APP_WSL to the installed application.
In the first isolated WSL pane: python3 wsl-recovery.py start RESULT_DIRECTORY
After ready.json exists, close that native window and observe heartbeat.json
advance with the same PID/token. In a NEW isolated WSL pane:
    python3 wsl-recovery.py attach RESULT_DIRECTORY
The surviving child checks pane list, new-tab and fullscreen with its refreshed
context, writes result.json, then exits. `cleanup RESULT_DIRECTORY` terminates
only this probe's session if an earlier step fails. No test changes WSL settings.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def save(root, name, data):
    target = root / name
    temporary = root / (name + '.tmp')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    os.replace(temporary, target)


def child(root, app):
    sys.path.insert(0, str(app / 'wsl'))
    import recovery

    def context():
        recovery.refresh_environment()
        value = json.dumps([os.environ.get(key) for key in recovery.CONTROL_KEYS])
        return hashlib.sha256(value.encode()).hexdigest()

    identity = {'pid': os.getpid(), 'token': uuid.uuid4().hex}
    original = context()
    save(root, 'ready.json', identity)
    began = time.monotonic()
    ticks = 0

    def command(args):
        result = subprocess.run([sys.executable, str(app / 'wsl/companion.py'), *args],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError(f'{args[0]} failed with status {result.returncode}: {result.stderr[:1000]}')

    try:
        while time.monotonic() - began < 300:
            ticks += 1
            save(root, 'heartbeat.json', dict(identity, ticks=ticks))
            if ticks % 10 == 0:
                print(f'KILIX_RECOVERY_TICK {ticks}', flush=True)
            if ((root / 'verify-request').exists()
                    and recovery.read_attachment(recovery.runtime_directory(), 'probe')
                    and context() != original):
                command(['pane', 'list'])
                command(['new-tab', '--title', 'recovery-check', '--', 'python3', '-c',
                         'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("ok");',
                         str(root / 'new-tab.txt')])
                deadline = time.monotonic() + 10
                while not (root / 'new-tab.txt').exists() and time.monotonic() < deadline:
                    time.sleep(.1)
                if not (root / 'new-tab.txt').exists():
                    raise RuntimeError('The new Linux tab did not execute')
                command(['fullscreen'])
                command(['fullscreen'])
                save(root, 'result.json', dict(identity, ok=True, ticks=ticks,
                     refreshed_control=True, pane_list=True, new_tab=True, fullscreen=True))
                print('KILIX_RECOVERY_PASS', flush=True)
                return 0
            time.sleep(.1)
        raise RuntimeError('Timed out waiting for reattachment')
    except Exception as error:
        save(root, 'result.json', dict(identity, ok=False, error=str(error)))
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'attach', 'cleanup', 'child'))
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    if os.environ.get('KILIX_TEST_ISOLATED') != '1':
        parser.error('Requires KILIX_TEST_ISOLATED=1 in a dedicated native instance')
    app = Path(os.environ['KILIX_TEST_APP_WSL']).resolve()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.action == 'child':
        return child(root, app)
    if args.action == 'start':
        if (root / 'session.json').exists():
            parser.error('Use a fresh result directory')
        settings = {'runtime': tempfile.mkdtemp(prefix='kwr-desktop-'), 'name': 'probe'}
        save(root, 'session.json', settings)
    else:
        settings = json.loads((root / 'session.json').read_text())
    os.environ['KILIX_WSL_SESSION_RUNTIME'] = settings['runtime']
    sys.path.insert(0, str(app / 'wsl'))
    import companion
    if args.action == 'cleanup':
        return companion.main(['session', 'kill', settings['name']])
    if args.action == 'start':
        return companion.main(['session', 'new', settings['name'], '--', sys.executable,
                               str(Path(__file__).resolve()), 'child', str(root)])
    original = json.loads((root / 'ready.json').read_text())
    import recovery
    backend = recovery.Sessions(companion.INSTALL, companion.environment())
    state = backend.query('status', settings['name'], '--json')
    if state['child_pid'] != original['pid'] or state['attached']:
        raise RuntimeError('Expected the original, detached child process')
    (root / 'verify-request').touch()
    status = companion.main(['session', 'attach', settings['name']])
    result = json.loads((root / 'result.json').read_text())
    if status or not result['ok'] or any(result[key] != original[key] for key in ('pid', 'token')):
        raise RuntimeError('Recovery probe failed; inspect result.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
