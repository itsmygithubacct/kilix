"""Live command checks for a coordinator-owned, otherwise unused WSL window."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

if os.environ.get('KILIX_TEST_ISOLATED') != '1':
    raise SystemExit('Use only an isolated test instance with KILIX_TEST_ISOLATED=1')
root = Path(os.environ['KILIX_TEST_APP_WSL'])
output = Path(os.environ['KILIX_TEST_RESULT_WSL']) / 'command-results.json'
cli = str(root / 'wsl-bin/kilix')
owned, passed = [], []
result = {'passed': passed}


def run(args, *, data=None, env=None):
    process = subprocess.run([cli, *args], input=data, env=env, capture_output=True,
                             text=True, timeout=30)
    if process.returncode:
        raise RuntimeError(f'{args[:2]} failed ({process.returncode}): {process.stderr[:1000]} {process.stdout[:1000]}')
    return process.stdout


def panes():
    return json.loads(run(['agent-control', 'list']))['panes']


def identity(pane_id):
    pane = next(pane for pane in panes() if pane['pane_id'] == pane_id)
    return {key: pane[key] for key in ('pane_id', 'broker')}


try:
    with tempfile.TemporaryDirectory(prefix='kilix command fixture ') as tmp:
        scratch = Path(tmp)
        env = dict(os.environ, GPU_TERMINAL_SETTINGS_FILE=str(scratch / 'settings.conf'),
                   XDG_STATE_HOME=str(scratch / 'state'), KILIX_TRANSCRIPT_DIR=str(scratch / 'transcripts'))
        offline = dict(env)
        for key in ('KITTY_LISTEN_ON', 'KITTY_WINDOW_ID', 'KITTY_RC_PASSWORD', 'KILIX_WSL_SESSION'):
            offline.pop(key, None)
        for verb in ('settings', 'models', 'agent-control', 'action', 'tmux', 'transcript'):
            assert 'usage:' in run([verb, '--help'], env=offline).lower()
        passed.append('offline shared command help')
        run(['settings', '--set', 'pane_memory=off', '--print'], env=env)
        run(['models', 'list'], env=env)
        passed.append('shared settings and read-only model catalog')

        # This is our own shell. Explicit OSC 133 marks make readiness
        # independent of the user's shell profiles and installed dotfiles.
        pane = int(run(['@', 'launch', '--type=tab', '--keep-focus',
                        '--env=PS1=\033]133;A\007OWNED> ', '--env=PS0=\033]133;C\007',
                        '--', '/bin/bash', '--noprofile', '--norc', '-i']))
        owned.append(pane)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = next(p for p in panes() if p['pane_id'] == pane)
            if current['at_prompt']:
                break
            time.sleep(0.1)
        assert current['at_prompt'], 'owned shell prompt readiness not reported'
        token = identity(pane)['broker']
        marker = 'KILIX_OWNED_' + uuid.uuid4().hex
        run(['agent-control', 'send', str(pane), '--expect-broker', token,
             '--text', "printf '%s\\n' " + marker, '--submit'], env=env)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            text = run(['@', 'get-text', '--match=id:' + str(pane)])
            if marker in text.splitlines():
                break
            time.sleep(0.1)
        assert marker in text.splitlines(), 'owned shell did not execute submitted literal text'
        passed.append('authenticated agent-control input into owned ready shell')

        source = identity(int(os.environ['KITTY_WINDOW_ID']))
        target = identity(pane)
        literal = ['', 'a b', 'quote " and \'', 'backslash\\', 'café 日本語', '$HOME;$(false)', '--help']
        report = scratch / 'argv.json'
        code = ('import json,sys,time; from pathlib import Path; '
                'Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:])); time.sleep(30)')
        request = {'schema': 'kilix.actions/v1', 'operation_id': 'fixture-' + uuid.uuid4().hex,
                   'operation': 'pane.open', 'source': source, 'target': target,
                   'params': {'argv': ['/usr/bin/python3', '-c', code, str(report), *literal],
                              'cwd': tmp, 'title': 'Owned command fixture', 'placement': 'new-tab'}}
        receipt = json.loads(run(['action', '--request-json', '-'], data=json.dumps(request), env=env))
        created = receipt['evidence']['pane']['pane_id']
        owned.append(created)
        assert receipt['pane_created_verified']
        duplicate = json.loads(run(['action', '--request-json', '-'], data=json.dumps(request), env=env))
        assert duplicate['duplicate'] and duplicate['evidence'] == receipt['evidence']
        deadline = time.monotonic() + 5
        while not report.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert json.loads(report.read_text()) == literal
        passed.append('structured literal argv launch and duplicate receipt')

        saved = json.loads(run(['transcript', 'capture', '--pane', str(pane)], env=env))
        assert marker in run(['transcript', 'clean', saved['session']], env=env)
        passed.append('retained-output transcript capture and replay')
except Exception as error:
    result['error'] = str(error)
finally:
    for pane in reversed(owned):
        try:
            run(['@', 'close-window', '--match=id:' + str(pane)])
        except Exception as error:
            result.setdefault('cleanup_errors', []).append(str(error))
    output.write_text(json.dumps(result, indent=2))

if result.get('error') or result.get('cleanup_errors'):
    raise SystemExit(1)
print('COMMAND_TEST_PASS')
