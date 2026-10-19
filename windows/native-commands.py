#!/usr/bin/env python3
"""Native Windows command interface, run by the bundled engine's Python."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
SHARED = ROOT / 'wsl-bin'
sys.path[:0] = [str(SHARED / 'config'), str(ROOT / 'wsl')]
KITTEN = ROOT / 'kitty/launcher/kitten.exe'

REMOTE_VERBS = ('ls', 'pane', 'tab', 'focus', 'watch', 'new-pane', 'split', 'new-tab', 'new-page')
CATALOG_HELP = '''Catalog: apps (alias app), games
  list, info, check, install, update, enable, disable, launch, run, play, choose, export
  Use apps --help or games --help. Availability is reported per runtime.'''
HELP = '''Kilix native control commands (no WSL required):
  ls [--panes]                 List native tabs or panes
  pane list|right|left|up|down|above|below|quad|close|focus|read|send
  tab list|new|left|right|close|focus|rename|move
  focus TARGET                 Focus an exact pane or tab
  watch PANE --once             Read live pane text; omit --once to watch
  new-pane, split [DIRECTION] [-- PROGRAM ARGS]
  new-tab, new-page [OPTIONS] [-- PROGRAM ARGS]
  launch, --launch PROGRAM [ARGS]  Launch literal native argv in a new tab
  fullscreen                   Toggle the invoking native window
  desktop, windows OPERATION   Windows desktop apps; desktop capabilities is offline
  @ COMMAND [ARGS]             Authenticated engine control; @ --help lists verbs
  settings [--path|--print|--set OPTION=VALUE|--edit|--reload]
  transcript list|path|show|view|clean|capture
  status [--json]              Inspect this installation and control availability
  setup [ACTION] [OPTIONS]     Install, WSL setup, rollback, recover, status, diagnose
  update [ACTION] [OPTIONS]    Check/apply published releases or manage scheduled checks
  help [COMMAND ...]          Offline command help
Use COMMAND --help for options. Pane/tab control requires an inherited
authenticated Kilix session. Desktop control uses the current Windows user's
interactive desktop. Native settings edit kitty.conf; new windows load saved changes.
Transcripts capture retained output explicitly; no automatic recording broker.
The WSL companion adds Linux settings, models, agent-control, action and tmux.
Profile launches: powershell, cmd, wsl [distribution]; engine [OPTIONS].
Use kilix engine --help for all engine options. No arguments opens PowerShell.
Aliases: --cmd, --wsl, --settings, --version, -h/--help; version prints the build version.'''


def require_control():
    if (not re.fullmatch(r'tcp:127\.0\.0\.1:[0-9]+', os.environ.get('KITTY_LISTEN_ON', ''))
            or not os.environ.get('KITTY_RC_PASSWORD')
            or not re.fullmatch(r'[1-9][0-9]*', os.environ.get('KITTY_WINDOW_ID', ''))):
        raise ValueError('Run this command inside a Kilix window with authenticated control enabled.')


def refresh_control():
    if os.environ.get('KILIX_NATIVE_SESSION_ID'):
        try:
            from kitty.windows_pty_broker import refresh_environment
        except ImportError as error:
            raise ValueError('The native recovery runtime is missing; reopen the complete package') from error
        refresh_environment()


def configure_tk():
    manifest = ROOT / 'tk-runtime.json'
    if not manifest.is_file():
        return
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Invalid bundled Tk runtime manifest')
    for key, variable in (('tcl', 'TCL_LIBRARY'), ('tk', 'TK_LIBRARY')):
        value = data.get(key)
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            raise ValueError('Invalid bundled Tk runtime path')
        path = (ROOT / value).resolve()
        if not path.is_relative_to(ROOT) or not path.is_dir():
            raise ValueError('Bundled Tk runtime is incomplete; reinstall the full package')
        os.environ[variable] = str(path)


def redact(value):
    if isinstance(value, dict):
        value.pop('KITTY_RC_PASSWORD', None)
        for child in value.values():
            redact(child)
    elif isinstance(value, list):
        for child in value:
            redact(child)


def is_help(args):
    if not args or args in (['--help'], ['-h']) or (
            len(args) == 2 and args[1] in ('--help', '-h')):
        return True
    if not any(arg in ('--help', '-h') for arg in args):
        return False
    # Use the bundled engine's complete parser, without printing or sending a
    # remote request. It knows option values and the literal child boundary:
    # launch --type=tab --help is help; launch -- child --help is a launch.
    from kitty.cli import Options
    from kitty.rc.base import command_for_name, parse_subcommand_cli
    printing = Options.do_print
    try:
        Options.do_print = False
        parse_subcommand_cli(command_for_name(args[0]), args)
    except SystemExit as result:
        if result.code == 0:
            return True
        raise ValueError(str(result.code)) from result
    finally:
        Options.do_print = printing
    return False


def remote(args, *, capture=False, timeout=None):
    help_requested = is_help(args)
    if help_requested:
        command = [str(KITTEN), '@', *(args or ['--help'])]
    else:
        require_control()
        if args[0].startswith('-'):
            raise ValueError('Endpoint overrides are not supported; use the inherited Kilix instance.')
        command = [str(KITTEN), '@', '--use-password', 'always', *args]
    if capture or args[:1] == ['ls'] and not help_requested:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
        if not result.returncode and args[:1] == ['ls'] and not help_requested:
            data = json.loads(result.stdout)
            redact(data)
            result.stdout = json.dumps(data)
        if capture:
            return result
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    return subprocess.call(command, timeout=timeout)


def read_live(pane, extent):
    result = remote(['get-text', '--match=id:' + pane, '--extent=' + extent], capture=True, timeout=15)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Cannot read pane')
    return result.stdout.encode('utf-8')


def maintenance(verb, args):
    actions = ({'install': 'Install', 'wsl': 'Wsl', 'rollback': 'Rollback',
                'recover': 'Recover', 'status': 'Status', 'diagnose': 'Diagnose'} if verb == 'setup' else
               {'check': 'Check', 'apply': 'Apply', 'enable-schedule': 'EnableSchedule',
                'disable-schedule': 'DisableSchedule'})
    if args in (['--help'], ['-h']) or verb == 'setup' and not args:
        print(f'Usage: kilix {verb} [' + '|'.join(actions) + '] [POWERSHELL OPTIONS]\n'
              'Arguments are passed literally to the packaged ' + verb + '.ps1 script.\n'
              'Common options: -Destination DIR, -WslDistribution NAME, -WslUser USER, -NoShortcuts.\n'
              + ('Install: -Archive ZIP -Sha256 HASH [-SignerThumbprint VALUE].\n'
                 'WSL: -CompanionArchive FILE -CompanionSha256 HASH [-InstallWslDependencies].\n'
                 'Diagnose: -DiagnosticOutput FILE.' if verb == 'setup' else
                 'Updates: -Channel stable|preview (default stable), -SignerThumbprint VALUE.\n'
                 'Schedule: -DailyAt HH:mm, -ScheduleAction Check|Apply. No arguments checks only.') +
              '\nUse Windows paths. Shipped scripts run with process-scoped ExecutionPolicy Bypass; '
              'user/machine policy and ordinary shells are unchanged.')
        return 0
    if args and not args[0].startswith('-'):
        action = actions.get(args[0].lower())
        if action is None:
            raise ValueError(f'Unknown {verb} action: {args[0]}')
        args = ['-Action', action, *args[1:]]
    script = ROOT / (verb + '.ps1')
    if not script.is_file():
        raise ValueError(f'{verb}.ps1 is missing; reinstall the complete native package')
    return subprocess.call(['powershell.exe', '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                            '-File', str(script), *args])


def config_path():
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        raise ValueError('LOCALAPPDATA is unavailable')
    return Path(local) / 'Kilix/kitty.conf'


@contextmanager
def settings_lock(path):
    with path.with_suffix('.conf.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            if not lock.tell():
                lock.write(b'\0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def settings(args):
    parser = argparse.ArgumentParser(prog='kilix settings',
        description='Read or edit native user kitty.conf. Saved changes apply to new windows; '
                    '--reload applies configuration to the current authenticated instance.')
    parser.add_argument('--path', action='store_true', help='print the native user configuration path')
    parser.add_argument('--print', dest='show', action='store_true', help='print saved user configuration')
    parser.add_argument('--set', action='append', default=[], metavar='OPTION=VALUE',
                        help='replace a scalar kitty.conf option; values are literal, never shell commands')
    parser.add_argument('--edit', action='store_true', help='open user configuration in Notepad')
    parser.add_argument('--reload', action='store_true', help='reload configuration in the current instance')
    ns = parser.parse_args(args)
    path = config_path()
    if not args:
        parser.print_help()
        return 0
    changes = {}
    for assignment in ns.set:
        key, sep, value = assignment.partition('=')
        if (not sep or not re.fullmatch(r'[a-z][a-z0-9_]*', key)
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            parser.error('--set needs OPTION=VALUE without control characters')
        if key in ('include', 'globinclude', 'envinclude', 'geninclude', 'map', 'mouse_map', 'env'):
            parser.error('use --edit for includes, mappings and repeatable options')
        # The bundled engine supplies the exact scalar setting vocabulary.
        from kitty.options.parse import parse_conf_item
        from kitty.options.types import defaults
        if hasattr(defaults, key) and isinstance(getattr(defaults, key), (dict, list, tuple, set)):
            parser.error(f'use --edit for the compound or repeatable option: {key}')
        parsed = {}
        if not parse_conf_item(key, value, parsed):
            parser.error(f'unknown engine option: {key}')
        changes[key] = value
    if changes or ns.edit:
        path.parent.mkdir(parents=True, exist_ok=True)
        with settings_lock(path):
            text = path.read_text(encoding='utf-8-sig') if path.exists() else ''
            if changes:
                lines = [line for line in text.splitlines()
                         if (line.strip().split(None, 1) or [''])[0] not in changes]
                lines.extend(f'{key} {value}' for key, value in changes.items())
                fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.kitty-', suffix='.conf')
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as output:
                        output.write('\n'.join(lines) + '\n')
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            elif not path.exists():
                path.touch()
        if ns.edit:
            return subprocess.call(['notepad.exe', str(path)])
    if ns.path:
        print(path)
    if ns.show and path.exists():
        sys.stdout.write(path.read_text(encoding='utf-8-sig'))
    if ns.reload:
        return remote(['load-config'])
    return 0


def main(args):
    if args[:1] == ['help'] and len(args) > 1:
        return main([*args[1:], '--help'])
    if args and args[0] in ('desktop', 'windows'):
        from kilix_sdk import windows
        return windows.main(args[1:], root=ROOT, prog='kilix ' + args[0])
    # Freeze the inherited route for this invocation. Refreshing again between
    # resolving a pane ID and sending its command could address a replacement
    # instance with an ID from the old one.
    refresh_control()
    if not args or args in (['help'], ['--help'], ['-h']):
        print(HELP)
        if (ROOT / 'wsl/catalog.py').is_file():
            print(CATALOG_HELP)
        if (ROOT / 'kitty/windows_pty_broker.py').is_file():
            print('Persistent native sessions: session (alias sessions)\n'
                  '  list, new, attach, detach, kill, restore. Use session --help.')
        return 0
    verb, rest = args[0], args[1:]
    if verb == '@':
        return remote(rest)
    if verb in ('apps', 'app', 'games'):
        if not (ROOT / 'wsl/catalog.py').is_file():
            raise ValueError('Catalog component is missing; reinstall the complete native package')
        configure_tk()
        import catalog
        return catalog.main(rest, kind='games' if verb == 'games' else 'apps', runtime='native')
    if verb in ('setup', 'update'):
        return maintenance(verb, rest)
    if verb in ('session', 'sessions'):
        try:
            from kitty.windows_pty_broker import main as sessions
        except ImportError as error:
            raise ValueError('Native persistent sessions are unavailable in this engine build') from error
        return sessions(rest, remote=remote)
    if verb in ('settings', '--settings'):
        return settings(rest)
    if verb in ('powershell', 'cmd', '--cmd', 'wsl', '--wsl', 'engine', 'version', '--version') and rest == ['--help']:
        print('Usage: kilix powershell|cmd [ENGINE OPTIONS]\n'
              '       kilix wsl [DISTRIBUTION] [ENGINE OPTIONS]\n'
              '       kilix engine [ENGINE OPTIONS] [PROGRAM ARGS]\n'
              '       kilix version\n'
              'Use kilix engine --help for the complete engine option reference.')
        return 0
    if verb == 'status':
        parser = argparse.ArgumentParser(prog='kilix status', description='Inspect native installation; no mutation')
        parser.add_argument('--json', action='store_true')
        ns = parser.parse_args(rest)
        try:
            require_control()
            response = remote(['ls'], capture=True, timeout=5)
            available = response.returncode == 0
        except (ValueError, OSError, subprocess.TimeoutExpired):
            available = False
        result = {'runtime': 'native-windows', 'installation': str(ROOT),
                  'control_available': available, 'wsl_required': False,
                  'settings': str(config_path())}
        print(json.dumps(result, indent=None if ns.json else 2))
        return 0
    if verb in ('launch', '--launch'):
        if not rest or rest in (['--help'], ['-h']):
            print('Usage: kilix launch PROGRAM [ARGS]\nRun literal native argv in a new tab. For options use kilix @ launch --help.')
            return 0 if rest else 2
        if rest[:1] == ['--']:
            rest = rest[1:]
        if not rest:
            raise ValueError('launch requires a program')
        if (ROOT / 'wsl/catalog.py').is_file():
            from catalog_store import Catalog, entries
            matches = [spec for spec in entries() if rest[0] in [spec['name'], *spec.get('aliases', [])]]
            if len(matches) > 1:
                raise ValueError('Ambiguous catalog program: ' + rest[0])
            if matches:
                # Known but disabled/unsupported entries must not silently
                # fall through to a different executable on PATH.
                rest = [*Catalog(runtime='native').argv(matches[0]), *rest[1:]]
        return remote(['launch', '--type=tab', '--', *rest])
    if verb == 'fullscreen':
        argparse.ArgumentParser(prog='kilix fullscreen', description='Toggle the invoking native window').parse_args(rest)
        require_control()
        return remote(['resize-os-window', '--match=id:' + os.environ['KITTY_WINDOW_ID'],
                       '--action=toggle-fullscreen'])
    if verb in ('transcript', 'transcripts', 'session-log', 'session-logs'):
        import transcripts
        return transcripts.main(rest, shared=SHARED, read_live=read_live)
    if verb in REMOTE_VERBS:
        from kilix_sdk import panes
        import remote as shared_remote

        def run(argv, **kwargs):
            return remote(list(argv), capture=True, timeout=kwargs.get('timeout'))

        panes._run = run
        return shared_remote.main([verb, *rest])
    raise ValueError(f'Unsupported native command: {verb}. Run kilix help.')


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ValueError, OSError, ImportError, subprocess.TimeoutExpired) as error:
        print(f'kilix: {error}', file=sys.stderr)
        raise SystemExit(2)
