#!/usr/bin/env python3
"""Headless tmux control through the selected kilix-tmux implementation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys


HELP = """usage: kilix tmux --socket /absolute/path [--json] VERB ...

Control only the server at the explicitly supplied socket.
Verbs: list, new, read, send, type, key, rename, close.
Use --help after a verb for its arguments. Targets are exact session names,
session IDs ($N), pane IDs (%N), or numeric NAME:WINDOW.PANE for I/O.
send writes literal text without Enter; type sends text then separate Enter.
Submission does not establish command completion. Reads are bounded.

KILIX_TMUX_CLI selects an executable. KILIX_TMUX_MODULE_ROOT selects an
import root. Neither selects a socket. No server discovery or installation.
With no arguments, kilix tmux opens the interactive Tmux Manager.
"""


def fail(message: str, args: list[str], exit_code: int = 2) -> int:
    if '--json' in args:
        print(json.dumps({'schema': 'kilix.tmux/v1', 'ok': False,
                          'code': 'EUSAGE' if exit_code == 2 else 'ETMUX',
                          'error': message, 'exit': exit_code}))
    else:
        print(f'kilix tmux: {message}', file=sys.stderr)
    return exit_code


def main(args: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if args is None else args)
    if args in (['--help'], ['-h'], []):
        print(HELP, end='')
        return 0
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False,
                                     exit_on_error=False)
    parser.add_argument('--socket')
    try:
        selected, _ = parser.parse_known_args(args)
    except (argparse.ArgumentError, SystemExit):
        return fail('--socket requires an absolute path', args)
    socket = selected.socket
    if not socket or not os.path.isabs(socket) or '\0' in socket:
        return fail('an explicit absolute --socket is required', args)

    # Overrides select one implementation, never a fallback server. An invalid
    # explicit selection is an error instead of trying a different executable.
    cli = os.environ.get('KILIX_TMUX_CLI')
    root = os.environ.get('KILIX_TMUX_MODULE_ROOT')
    if cli:
        executable = shutil.which(cli)
        if executable is None:
            return fail('KILIX_TMUX_CLI is not executable', args, 7)
        os.execv(executable, [executable, *args])
    if root:
        module_root = Path(root)
    else:
        module_root = Path(__file__).resolve().parent
    if (module_root / 'kilix_tmux' / '__main__.py').is_file():
        env = dict(os.environ)
        env['PYTHONPATH'] = str(module_root.resolve()) + (
            os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        os.execve(sys.executable, [sys.executable, '-P', '-B', '-m', 'kilix_tmux', *args], env)
    if root:
        return fail('KILIX_TMUX_MODULE_ROOT does not contain kilix_tmux', args, 7)
    return fail('kilix-tmux is unavailable; select KILIX_TMUX_CLI or '
                'KILIX_TMUX_MODULE_ROOT', args, 7)


if __name__ == '__main__':
    raise SystemExit(main())
