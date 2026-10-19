"""Use shared agent validation/receipts with authenticated ConPTY identities.

The schema's `broker` field holds an opaque Windows instance/pane token here.
It is not a persistent PTY session and cannot survive a native restart.
No process or foreground agent is inferred from a Windows wsl.exe process.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

import agent_control as control
import companion

BaseClient = control.Client
base_describe = control.describe
TOKEN_MATCH = 'env:KITTY_PTY_BROKER_SESSION='


def foreground(pane_ids, environment, *, proc=Path('/proc'), deadline=None):
    """Observe this distro/user's exact terminal foreground groups.

    Windows can report only wsl.exe. A Linux process is attributable to a pane
    only when its inherited authenticated route matches, and its process group
    is foreground on a real controlling tty. Multiple ttys (for example an
    untracked nested multiplexer) are ambiguous and yield no inferred agent.
    Nothing is executed and no process argument beyond the first two is kept.
    """
    if not environment.get('WSL_DISTRO_NAME'):
        return {}
    deadline = min(deadline if deadline is not None else float('inf'), time.monotonic() + 1)
    groups = {}
    try:
        entries = proc.iterdir()
        for entry in entries:
            if time.monotonic() >= deadline:
                return {}
            if not entry.name.isdecimal():
                continue
            try:
                if entry.stat().st_uid != os.getuid():
                    continue
                # comm can contain spaces and parentheses; stat's numeric
                # fields begin after its final closing parenthesis.
                before = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                pgrp, tty, foreground_group = map(int, (before[2], before[4], before[5]))
                if not tty or pgrp <= 0 or pgrp != foreground_group:
                    continue
                with (entry / 'environ').open('rb') as source:
                    raw = source.read(65537)
                if len(raw) > 65536:
                    continue
                values = dict(item.split(b'=', 1) for item in raw.split(b'\0') if b'=' in item)
                pane = values.get(b'KITTY_WINDOW_ID', b'').decode('ascii')
                if pane not in pane_ids or any(values.get(name.encode(), b'') != environment.get(name, '').encode()
                        for name in ('KITTY_LISTEN_ON', 'KITTY_RC_PASSWORD', 'KITTY_PUBLIC_KEY', 'WSL_DISTRO_NAME')):
                    continue
                with (entry / 'cmdline').open('rb') as source:
                    command = source.read(4096).split(b'\0')[:2]
                after = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                if before[19] != after[19] or before[2:6] != after[2:6]:
                    continue
                groups.setdefault(pane, {}).setdefault((tty, pgrp), []).append({
                    'pid': int(entry.name), 'cmdline': [os.fsdecode(item) for item in command if item]})
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        return {}
    return {pane: next(iter(group.values())) for pane, group in groups.items() if len(group) == 1}


class Client(BaseClient):
    def __init__(self, caller_pane=None):
        self.command_env = companion.environment()
        try:
            companion.require_control()
        except ValueError as error:
            raise control.ControlError(str(error)) from error
        inherited = int(os.environ['KITTY_WINDOW_ID'])
        if caller_pane is not None and caller_pane != inherited:
            raise control.ControlError('--caller-pane conflicts with the inherited caller identity')
        self.caller = inherited
        # Recovery can refresh the route during either preparation call.
        # Capture caller, credentials and interop together after validation.
        for name in ('KITTY_LISTEN_ON', 'KITTY_WINDOW_ID', 'KITTY_RC_PASSWORD',
                     'KITTY_PUBLIC_KEY', 'WSL_INTEROP'):
            if name in os.environ:
                self.command_env[name] = os.environ[name]
            else:
                self.command_env.pop(name, None)
        self.command = [str(companion.KITTEN), '@', '--to', os.environ['KITTY_LISTEN_ON'],
                        '--use-password', 'always']
        # The random instance credential binds tokens even when a Windows
        # process/port or pane number is reused. Only a digest is published.
        self.scope = json.dumps([os.environ['KITTY_LISTEN_ON'],
                                 os.environ.get('KITTY_PUBLIC_KEY', ''),
                                 os.environ['KITTY_RC_PASSWORD']]).encode()

    def snapshot(self):
        panes = super().snapshot()
        processes = foreground({str(pane['id']) for pane in panes}, self.command_env,
                               deadline=getattr(self, 'deadline', None))
        for pane in panes:
            token = hashlib.sha256(self.scope + json.dumps(
                [pane['id'], pane.get('pid')], separators=(',', ':')).encode()).hexdigest()
            pane['env'] = {'KITTY_PTY_BROKER_SESSION': token}
            if str(pane['id']) in processes:
                pane['foreground_processes'] = processes[str(pane['id'])]
        return panes

    def resolve(self, pane_id, expected=None, input_target=False):
        pane = super().resolve(pane_id, expected, input_target)
        if input_target and not (pane.get('at_prompt') is True or
                                 base_describe(pane)['agent'] in control.AGENTS):
            raise control.ControlError('target readiness is unavailable: input requires a reported '
                                       'shell prompt or a recognized foreground coding agent')
        return pane

    def run(self, args, payload=None):
        args = list(args)
        # Only rewrite remote options. Child argv, including a literal token
        # selector string, belongs entirely to the child process.
        boundary = args.index('--') if '--' in args else len(args)
        for index in range(1, boundary):
            item = args[index]
            prefix = '--match=' if item.startswith('--match=') else ''
            match = item[len(prefix):]
            if (prefix or args[index - 1] == '--match') and match.startswith(TOKEN_MATCH):
                token = match[len(TOKEN_MATCH):]
                panes = [pane for pane in self.snapshot()
                         if pane['env']['KITTY_PTY_BROKER_SESSION'] == token]
                if len(panes) != 1:
                    raise control.ControlError('pane identity changed; take a new snapshot')
                pane = self.resolve(panes[0]['id'], token, input_target=args[0] == 'send-text')
                args[index] = prefix + 'id:' + str(pane['id'])
        if args[:1] == ['launch']:
            parsed, _ = companion.parse_launch(args[1:])
            if not any(key in ('--help', '-h') for key, _ in parsed):
                args = ['launch', *companion.translate_launch(args[1:])]
        return super().run(args, payload)


def describe(pane):
    return {**base_describe(pane), 'identity_kind': 'windows-instance-pane',
            'at_prompt': pane.get('at_prompt') is True}


def install():
    control.Client = Client
    control.describe = describe
