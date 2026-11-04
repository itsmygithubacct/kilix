"""Launch the pane center with the host's verified inherited connection.

Keep provider/process inspection in the toolkit. Connection recovery belongs
to the host, just as it does for agent-control and SDK pane operations.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time

from agent_control import Client, ControlError, recovered_values
from kilix_sdk.caller_context import CallerContextError, require_pane_local


def command() -> list[str]:
    prefix = Path(os.environ.get('KILIX_TUI_UTILS_PREFIX') or Path.home() / '.local')
    installed = prefix / 'bin/kilix-panes'
    if installed.is_file() and os.access(installed, os.X_OK):
        return [str(installed)]
    if executable := shutil.which('kilix-panes'):
        return [executable]
    sources = Path(os.environ.get('GPU_TERMINAL_SOURCE_HOME') or
                   Path.home() / '.local/gpu_terminal/sources')
    roots = [os.environ.get('KILIX_TUI_UTILS_DIR'),
             str(sources / 'kilix-desktops/kilix-tui-utils')]
    for root in roots:
        if root and (script := Path(root) / 'tools/switcher/main.py').is_file():
            return [sys.executable, str(script)]
    raise FileNotFoundError('the pane center is not installed; install kilix-tui-utils')


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if args in (['--help'], ['-h']):
            selected = command()
            os.execvpe(selected[0], [*selected, *args], dict(os.environ))
            return 0
        require_pane_local()
        expected_broker = recovered_values().get('KITTY_PTY_BROKER_SESSION')
        # Client recovers stripped MCP metadata only from matching ancestors,
        # refuses shared-server ancestry, refreshes an orphaned broker's exact
        # attach route, and validates the private credential file.
        client = Client()
        # A reachable socket alone does not prove it is still this pane's
        # frontend. Verify the recovered caller/broker against an authenticated
        # live listing before letting the toolkit inspect any recording.
        client.deadline = time.monotonic() + 3
        client.resolve(client.caller, expected=expected_broker)
        environment = dict(client.command_env)
        environment['KILIX_KITTEN'] = client.command[0]
        # The toolkit reads this file itself. An old plaintext override must
        # not win over the credential belonging to the recovered frontend.
        environment.pop('KILIX_RC_PASSWORD', None)
        selected = command()
        os.execvpe(selected[0], [*selected, *args], environment)
    except (ControlError, CallerContextError, OSError, ValueError) as error:
        if args and args[0] in {'list', 'ls'} and '--json' in args:
            # A bounded, machine-readable refusal. Readers map this code to
            # their own message rather than exposing arbitrary child stderr.
            print(json.dumps({'schema': 'kilix.panes.error/v1',
                              'code': ('caller_context_unverified'
                                       if isinstance(error, CallerContextError)
                                       else 'pane_context_unavailable')}))
        print(f'kilix panes: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
