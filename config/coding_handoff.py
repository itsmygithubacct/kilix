"""Prepare a coding conversation handoff without terminating its current owner."""
from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from threading import Thread

from coding_launch import PROVIDERS, identity, private_root
from coding_resume import clean_environment, log_identity, resume_argv


def read_private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 1048576):
            raise ValueError('handoff metadata is not an owned private regular file')
        return json.load(stream)


def write(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2); stream.write('\n')
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def live(process):
    try:
        return identity(process['pid']) == process
    except FileNotFoundError:
        return False
    except ProcessLookupError:
        return False


def process_environment(pid):
    data = (Path('/proc') / str(pid) / 'environ').read_bytes()
    if len(data) > 1048576:
        raise ValueError('oversized process environment')
    return dict(os.fsdecode(item).split('=', 1) for item in data.split(b'\0') if b'=' in item)


def resolver():
    # Match the host installer's configured toolkit lookup, without installing.
    source = os.environ.get('GPU_TERMINAL_SOURCE_HOME') or str(
        Path(os.environ.get('GPU_TERMINAL_HOME', str(Path.home() / '.local/gpu_terminal'))) / 'sources')
    roots = []
    if configured := os.environ.get('KILIX_TUI_UTILS_DIR'):
        roots.append(Path(configured) / 'src')
    roots.append(Path(source) / 'kilix-desktops/kilix-tui-utils/src')
    for root in roots:
        if (root / 'kilix_rollout/pane_log.py').is_file():
            sys.path.insert(0, str(root))
            module = importlib.import_module('kilix_rollout.pane_log')
            if not Path(module.__file__).resolve().is_relative_to(root.resolve()):
                raise ValueError('rollout resolver comes from another installation')
            return module
    raise ValueError('installed rollout resolver unavailable; original pane retained')


def broker_binding(session, processes):
    # Only the launcher's explicit broker configuration is authoritative.
    import re
    executable = os.environ.get('KITTY_PTY_BROKER_EXECUTABLE', '')
    runtime = os.environ.get('KITTY_PTY_BROKER_RUNTIME', '')
    if (not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', session)
            or session in ('.', '..') or not os.path.isabs(executable)
            or not os.path.isabs(runtime)):
        raise ValueError('identity-bound broker required; original pane retained')
    result = subprocess.run([executable, '--runtime-dir', runtime, '--timeout', '2',
                             'status', session, '--json'], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=3, check=True)
    status = json.loads(result.stdout)
    if (not isinstance(status, dict) or status.get('id') != session
            or type(status.get('started_millis')) is not int or status['started_millis'] <= 0):
        raise ValueError('broker start identity unavailable; original pane retained')
    if not processes:
        raise ValueError('coding writer identities required')
    child_pid = status.get('child_pid')
    broker_pid = status.get('broker_pid')
    if any(type(pid) is not int or pid <= 0 for pid in (child_pid, broker_pid)):
        raise ValueError('broker process identity unavailable')
    child = identity(child_pid)
    broker_process = identity(broker_pid)
    for process in processes:
        pid = process['pid']
        visited = set()
        while pid != child_pid:
            if pid <= 1 or pid in visited or len(visited) >= 256:
                raise ValueError('coding writer is outside the bound broker child domain')
            visited.add(pid)
            fields = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(') ', 1)[1].split()
            pid = int(fields[1])
        if not live(process):
            raise ValueError('coding writer changed while binding broker')
    if not live(child) or not live(broker_process):
        raise ValueError('broker process changed during binding')
    return {'executable': executable, 'runtime': runtime, 'session': session,
            'started_millis': status['started_millis'], 'child': child,
            'process': broker_process}


def cessation_key(record):
    binding = record['broker_binding']
    if (binding['session'] != record['broker']
            or type(binding['started_millis']) is not int or binding['started_millis'] <= 0):
        raise ValueError('broker cessation binding changed')
    return {'schema': 'kilix.coding-cessation/v1', 'broker_binding': binding,
            'receipt': record['receipt']}


def drain_broker(record):
    proof = cessation_key(record)
    binding = record['broker_binding']
    # Only DRAIN success authorizes transfer. Never retry with legacy kill,
    # infer success from disappearance, or substitute another broker identity.
    subprocess.run([binding['executable'], '--runtime-dir', binding['runtime'],
                    '--timeout', '5', 'drain', binding['session'], '--expect-started',
                    str(binding['started_millis'])], stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=6, check=True)
    return proof


def cessation_verified(path, record):
    try:
        return read_private(path.parent / 'cessation.json') == cessation_key(record)
    except (OSError, ValueError, KeyError):
        return False


def inspect_window(window, *, background):
    logs = resolver()
    pane = logs.pane_from_window(window, str(window['id']))
    found = logs.resolve(pane)
    if found.kind not in PROVIDERS.values():
        raise ValueError('pane is not a supported coding conversation')
    agent = logs._agent_processes(pane)
    if agent is None:
        raise ValueError('coding process evidence disappeared')
    processes = [identity(pid) for pid in sorted(logs._descendants(
        {item.pid for item in agent[1] if item.pid > 0}, '/proc'))]
    if not processes:
        raise ValueError('coding process identity unavailable')
    sid = log_identity(found.kind, found.path)
    result = {'provider': found.kind, 'session_id': sid, 'rollout_path': found.path,
              'pane': window['id'], 'broker': pane.broker_session,
              'broker_binding': broker_binding(pane.broker_session, processes),
              'processes': processes, 'cwd': pane.cwd, 'mode': 'background' if background else 'close'}
    if background:
        records = {}
        for process in processes:
            env = process_environment(process['pid'])
            name = env.get('KILIX_CODING_LAUNCH_RECORD')
            if name:
                records[name] = env
        if len(records) != 1:
            raise ValueError('exact original launch flags unavailable; cannot safely background this session')
        name, env = next(iter(records.items()))
        record = read_private(name)
        if (record.get('schema') != 'kilix.coding-launch/v1' or record.get('provider') != found.kind
                or record.get('broker') != pane.broker_session or not live(record['process'])
                or record['process'] not in processes):
            raise ValueError('launch evidence does not belong to the current coding process')
        result['argv'] = resume_argv(found.kind, record['argv_without_prompt'], sid)
        result['cwd'] = record['cwd']
        result['environment'] = clean_environment(process_environment(record['process']['pid']))
    if not all(live(item) for item in processes):
        raise ValueError('coding process changed during inspection')
    return result


def tmux_call(record, *arguments):
    return subprocess.run([record['tmux'], '-S', record['socket'], *arguments],
                          env=clean_environment(os.environ), text=True, capture_output=True,
                          timeout=10, check=True)


def prepare(window, background):
    record = inspect_window(window, background=background)
    key = hashlib.sha256((record['provider'] + ':' + record['session_id']).encode()).hexdigest()
    lock_path = private_root() / ('conversation-' + key + '.lock')
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The marker is retained for completed transfers, preventing repeat writers.
        marker = private_root() / ('conversation-' + key + '.json')
        if marker.exists():
            previous = read_private(marker)
            state = read_private(Path(previous['receipt']))
            if state['state'] not in ('cancelled', 'preparation-failed'):
                raise ValueError('conversation already has a handoff receipt: ' + previous['receipt'])
        directory = Path(tempfile.mkdtemp(prefix='handoff-', dir=private_root()))
        receipt = directory / 'receipt.json'
        record.update(state='preparing', receipt=str(receipt))
        if background:
            tmux = shutil.which('tmux')
            if not tmux:
                raise ValueError('tmux is not installed; original session retained')
            record.update(tmux=tmux, socket=str(directory / 'tmux.sock'),
                          tmux_session=record['provider'] + '-' + record['session_id'][:32])
            record['attach'] = shlex.join([tmux, '-S', record['socket'], 'attach-session', '-t', record['tmux_session']])
        write(receipt, record)
        write(marker, {'receipt': str(receipt)})
        try:
            if background:
                # tmux receives several command arguments and executes them directly.
                tmux_call(record, '-f', '/dev/null', 'new-session', '-d', '-s', record['tmux_session'],
                          '-c', record['cwd'], '--', sys.executable, str(Path(__file__).resolve()),
                          'wait', str(receipt))
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if read_private(receipt)['state'] == 'waiting':
                        break
                    time.sleep(.05)
                else:
                    raise ValueError('replacement worker did not become ready')
            else:
                record['state'] = 'waiting'; write(receipt, record)
            return {'receipt': str(receipt), 'processes': record['processes'],
                    'broker': record['broker'], 'pane': record['pane']}
        except Exception:
            record['state'] = 'preparation-failed'; write(receipt, record)
            if background:
                try:
                    tmux_call(record, 'kill-server')
                except Exception:
                    pass
            raise
    finally:
        os.close(fd)


def release(receipt):
    path = Path(receipt); record = read_private(path)
    if record['state'] != 'waiting' or not all(live(item) for item in record['processes']):
        raise ValueError('original coding process changed; handoff refused')
    if record['mode'] == 'background':
        tmux_call(record, 'has-session', '-t', record['tmux_session'])
    # This grants the replacement permission to wait, never permission to run
    # while the original process identities remain alive.
    write(path.parent / 'armed.json', {'armed': True})


def commit(receipt):
    path = Path(receipt); record = read_private(path)
    if record['state'] != 'waiting' or not (path.parent / 'armed.json').is_file():
        raise ValueError('handoff is not armed')
    if record['mode'] == 'background':
        tmux_call(record, 'has-session', '-t', record['tmux_session'])
    try:
        proof = drain_broker(record)
    except Exception:
        write(path.parent / 'cessation-unknown.json', {'status': 'unconfirmed',
              'error': 'Broker cessation was not confirmed; no replacement authorized. Original may already have stopped.'})
        raise
    write(path.parent / 'cessation.json', proof)
    write(path.parent / 'release.json', {'released': True})
    if record['mode'] == 'close':
        record['state'] = 'close-requested'; write(path, record)


def cancel(receipt):
    path = Path(receipt); record = read_private(path)
    if (path.parent / 'release.json').exists():
        raise ValueError('handoff already released; see its recovery receipt')
    record['state'] = 'cancelled'; write(path, record)
    if record['mode'] == 'background':
        tmux_call(record, 'kill-server')


def wait(receipt):
    path = Path(receipt); record = read_private(path)
    record['state'] = 'waiting'; write(path, record)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if read_private(path)['state'] == 'cancelled':
            return
        if ((path.parent / 'release.json').exists() and cessation_verified(path, record)
                and not any(live(item) for item in record['processes'])):
            break
        time.sleep(.1)
    else:
        record.update(state='failed', error='original process exit was not verified; no replacement started')
        write(path, record); return
    record['state'] = 'starting'; write(path, record)
    try:
        child = subprocess.Popen(record['argv'], cwd=record['cwd'], env=record['environment'])
        record.update(state='running', replacement=identity(child.pid)); write(path, record)
        code = child.wait()
        record.update(state='exited' if code == 0 else 'failed', exit_code=code)
    except Exception as error:
        record.update(state='failed', error=str(error))
    write(path, record)
    print('Coding conversation ended. Recovery receipt:', path, flush=True)
    print('Exit status:', record.get('exit_code', record.get('error')), flush=True)
    # Keep the replacement terminal and failure details available for attachment.
    subprocess.run([os.environ.get('SHELL', '/bin/sh')], env=clean_environment(os.environ))


def display(receipt):
    path = Path(receipt); record = read_private(path)
    for key in ('provider', 'session_id', 'rollout_path', 'cwd', 'tmux_session', 'attach'):
        if key in record:
            print(key + ': ' + record[key])
    print('Recovery receipt:', path, flush=True)
    print('This terminal remains available. Inspect the receipt for current status.', flush=True)
    write(path.parent / 'display-ready.json', {'ready': True})
    def monitor():
        previous = None
        while True:
            try:
                current = read_private(path)
                state = current['state']
                if state != previous:
                    print('Handoff status:', state, current.get('error', ''), flush=True)
                    previous = state
                if (path.parent / 'commit-failure.txt').is_file():
                    print('Handoff commit could not be confirmed. Recovery receipt:', path, flush=True)
                    return
                if state in ('failed', 'cancelled', 'exited', 'close-requested'):
                    return
                if current['mode'] == 'background':
                    try:
                        tmux_call(current, 'has-session', '-t', current['tmux_session'])
                    except Exception:
                        write(path.parent / 'tmux-lost.json', {'status': 'unknown',
                              'error': 'tmux session unavailable; inspect receipt before resuming'})
                        print('Tmux session lost. Use the recovery receipt; continuation is unconfirmed.', flush=True)
                        return
            except Exception as error:
                print('Cannot read handoff status:', error, flush=True); return
            time.sleep(.25)
    Thread(target=monitor, daemon=True).start()
    subprocess.run([os.environ.get('SHELL', '/bin/sh')], env=clean_environment(os.environ))


def main():
    action = sys.argv[1]
    if action == 'prepare':
        request = json.load(sys.stdin)
        print(json.dumps(prepare(request['window'], request['background'])))
    elif action in ('release', 'commit', 'cancel', 'wait', 'display'):
        globals()[action](sys.argv[2])
    else:
        raise ValueError('unknown handoff action')


if __name__ == '__main__':
    main()
