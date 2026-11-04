"""Give resolver fixtures a coherent caller-owned broker ancestry.

Route-helper /proc fixtures remain separate so changing a helper does not
silently change the owner proof. Ownership-specific tests override this tree.
"""
import os
from unittest import mock

from kilix_sdk import caller_context


def bind_test_caller(case, executable, runtime, session):
    proc = case.root / 'caller-proc'
    proc.mkdir()
    caller = os.getpid()
    broker = caller + 1000000
    for pid, parent, name, argv in (
            (caller, broker, 'python3', ['python3', 'fixture']),
            (broker, 1, 'kitty-pty-broke', [str(executable), '--runtime-dir',
             str(runtime), 'run', '--id', session, '--', 'fixture'])):
        path = proc / str(pid)
        path.mkdir()
        (path / 'exe').symlink_to(executable)
        (path / 'comm').write_text(name + '\n')
        (path / 'stat').write_text(f'{pid} (fixture) ' +
                                  ' '.join(['S', str(parent)] + ['0'] * 17 + ['777']))
        (path / 'cmdline').write_bytes(b'\0'.join(os.fsencode(a) for a in argv) + b'\0')
    # The caller itself is not a broker executable.
    (proc / str(caller) / 'exe').unlink()
    (proc / str(caller) / 'exe').symlink_to('/usr/bin/python3')
    patcher = mock.patch.object(caller_context, '_PROC', proc)
    patcher.start()
    case.addCleanup(patcher.stop)
