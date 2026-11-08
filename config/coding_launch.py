"""Private, process-bound launch evidence for coding-pane ownership transfer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile

# The host owns client identities for both managed launch and engine close UI.
CLIENTS = json.loads(Path(__file__).with_name('coding_clients.json').read_text())['clients']
PROVIDERS = {alias: client['id'] for client in CLIENTS for alias in client['launch_aliases']}


def identity(pid):
    path = Path('/proc') / str(pid)
    fields = (path / 'stat').read_text().rsplit(') ', 1)[1].split()
    if fields[0] == 'Z':
        raise ProcessLookupError(pid)
    return {'pid': pid, 'start': int(fields[19]), 'uid': path.stat().st_uid}


def private_root():
    root = Path.home() / '.local/state/kilix/coding-launches'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('coding launch directory must be private and owned')
    return root


def wrapper(provider, argv, *, prompt=None):
    """Prepare argv without writing state or changing a dry-run command."""
    if provider not in PROVIDERS or not sys.platform.startswith('linux'):
        return argv
    # prompt is a separate literal final argument from managed launch adapters.
    count = 1 if prompt is not None else 0
    return [sys.executable, str(Path(__file__).resolve()), provider, str(count), '--', *argv]


def capture(provider, argv, prompt_count):
    if provider not in PROVIDERS or prompt_count not in (0, 1) or not argv:
        raise ValueError('invalid coding launch')
    if prompt_count and len(argv) < 2:
        raise ValueError('missing initial prompt')
    flags = list(argv[:-1] if prompt_count else argv)
    if flags[-1:] == ['--']:
        flags.pop()
    descriptor, name = tempfile.mkstemp(prefix='launch-', suffix='.json', dir=private_root())
    record = {'schema': 'kilix.coding-launch/v1', 'provider': PROVIDERS[provider],
              'process': identity(os.getpid()), 'cwd': os.getcwd(), 'argv_without_prompt': flags,
              'broker': os.environ.get('KITTY_PTY_BROKER_SESSION', '')}
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(record, stream); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    return name


def main():
    provider, count, separator, *argv = sys.argv[1:]
    if separator != '--':
        raise ValueError('missing command separator')
    name = capture(provider, argv, int(count))
    env = dict(os.environ, KILIX_CODING_LAUNCH_RECORD=name)
    os.execvpe(argv[0], argv, env)


if __name__ == '__main__':
    main()
