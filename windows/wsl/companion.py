#!/usr/bin/env python3
"""Linux tools and authenticated native-window control for Kilix WSL."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / '.local/share/kilix-wsl'
BRIDGE = ROOT / 'wsl-bin/kilix'
KITTEN = ROOT / 'kitty/launcher/kitten.exe'
PYTHON = STATE / 'bin/python3'


def environment():
    env = os.environ.copy()
    env['PATH'] = f'{STATE}/bin:{ROOT}/wsl-bin:' + env.get('PATH', '')
    env['KILIX_NEEDLE_KILIX'] = str(BRIDGE)
    env['KILIX_KITTEN'] = str(ROOT / 'wsl-bin/kitten')
    env['KILIX_CONTENT_ROOT'] = str((STATE / 'content').resolve())
    # The Windows renderer cannot open Linux /dev/shm objects.
    env['KITTYFB_TRANSPORT'] = 'inline'
    return env


def require_control():
    if (not re.fullmatch(r'tcp:127\.0\.0\.1:[0-9]+', os.environ.get('KITTY_LISTEN_ON', ''))
            or not os.environ.get('KITTY_RC_PASSWORD') or not os.environ.get('KITTY_WINDOW_ID')):
        raise ValueError('Open a new Kilix WSL window with WSL control enabled first.')


def translate_launch(args):
    """Preserve remote options, but start Linux argv through this distro's WSL."""
    options, program = [], []
    cwd = os.getcwd()
    flags = {'--hold', '--keep-focus', '--copy-colors', '--allow-remote-control',
             '--stdin-add-formatting', '--stdin-add-line-wrap-markers', '--no-response', '--self',
             '--wait-for-child-to-exit', '--hold-after-ssh'}
    values = {'--type', '--location', '--title', '--tab-title', '--match', '--source-window',
              '--next-to', '--bias', '--env', '--var', '--cwd', '--response-timeout',
              '--spacing', '--color', '--stdin-source', '--marker'}
    i = 0
    while i < len(args):
        item = args[i]
        if item == '--':
            program = args[i + 1:]
            break
        if not item.startswith('-'):
            program = args[i:]
            break
        key, sep, value = item.partition('=')
        if key in flags:
            options.append(item)
        elif key in values:
            if not sep:
                i += 1
                if i == len(args):
                    raise ValueError(f'{key} needs a value')
                value = args[i]
            if key == '--cwd':
                if value != 'current':
                    cwd = os.path.abspath(os.path.expanduser(value))
            else:
                options.append(key + '=' + value)
        else:
            raise ValueError(f'WSL launch does not support {key}')
        i += 1
    distro = os.environ.get('WSL_DISTRO_NAME')
    if not distro:
        raise ValueError('WSL_DISTRO_NAME is missing')
    command = ['wsl.exe', '--distribution', distro, '--cd', cwd]
    if program:
        command += ['--exec', 'env', 'PATH=' + environment()['PATH'], 'KITTYFB_TRANSPORT=inline', *program]
    return options + ['--', *command]


def remote(args):
    require_control()
    if not args:
        raise ValueError('Use kilix @ COMMAND')
    if args[0].startswith('-'):
        raise ValueError('WSL control uses the current native instance; endpoint overrides are not supported.')
    if args[0] == 'launch':
        args = ['launch', *translate_launch(args[1:])]
    command = [str(KITTEN), '@', '--use-password', 'always', *args]
    if args[0] == 'ls':
        result = subprocess.run(command, env=environment(), capture_output=True, text=True)
        if result.returncode:
            sys.stderr.write(result.stderr)
            return result.returncode
        data = json.loads(result.stdout)
        def redact(value):
            if isinstance(value, dict):
                value.pop('KITTY_RC_PASSWORD', None)
                for child in value.values():
                    redact(child)
            elif isinstance(value, list):
                for child in value:
                    redact(child)
        redact(data)
        print(json.dumps(data))
        return 0
    return subprocess.call(command, env=environment())


def main(args):
    if not args or args[0] in ('--help', '-h', 'help'):
        print('''Kilix WSL commands:
  needle [ARGS]                 Run Needle's Linux command/model tools
  --launch PROGRAM [ARGS]       Run a Linux app in a new native Kilix tab
  apps                         List installed WSL apps
  games list | play NAME        List or run an installed game
  ls [--panes]                  List native tabs/panes
  pane, tab, focus, watch       Control the current native Kilix window
  fullscreen                   Toggle native fullscreen
  split, new-pane               Start Linux shells/programs in native panes
  new-tab, new-page             Start a Linux tab
  @ COMMAND [ARGS]              Authenticated native remote control
  help, version                 WSL help / native build version
  native [ARGS]                 Native launcher commands (native help lists them)
App commands run in the current WSL distribution. --cwd=current uses the
invoking Linux process's directory. Linux desktop services are not Windows services.''')
        return 0
    verb, rest = args[0], args[1:]
    if verb == 'native':
        return subprocess.call([str(ROOT / 'kilix-cli.exe'), *rest])
    if verb in ('version', '--version', 'engine', 'cmd', 'powershell', 'wsl', '--cmd', '--wsl'):
        return subprocess.call([str(ROOT / 'kilix-cli.exe'), *args])
    if verb == '@':
        return remote(rest)
    if verb == 'needle':
        if not PYTHON.exists() or not (STATE / 'needle/needle_cli.py').exists():
            raise ValueError('WSL companion dependencies are not installed; run the WSL setup script.')
        os.execve(str(PYTHON), [str(PYTHON), '-B', str(ROOT / 'wsl/needle-run.py'), *rest], environment())
    if verb == 'apps' or (verb == 'games' and (not rest or rest == ['list'])):
        names = sorted(p.name for p in (STATE / 'bin').glob('kilix-*') if os.access(p, os.X_OK))
        print('\n'.join(names) if '--json' not in rest else json.dumps(names))
        return 0
    if verb == 'games' and rest[:1] == ['play']:
        if len(rest) < 2:
            raise ValueError('Use kilix games play NAME')
        name = rest[1] if rest[1].startswith('kilix-') else 'kilix-' + rest[1]
        app = STATE / 'bin' / name
        if not app.is_file() or '/' in name:
            raise ValueError(f'Game is not installed: {name}')
        os.execve(str(app), [str(app), *rest[2:]], environment())
    if verb in ('--launch', 'launch'):
        if not rest:
            raise ValueError('Use kilix --launch PROGRAM [ARGS]')
        program = shutil.which(rest[0], path=environment()['PATH'])
        if not program:
            raise ValueError(f'Linux program is not installed: {rest[0]}')
        return remote(['launch', '--type=tab', '--tab-title=' + Path(program).name,
                       '--', program, *rest[1:]])
    if verb in ('ls', 'pane', 'tab', 'focus', 'watch', 'fullscreen', 'new-pane', 'split', 'new-tab', 'new-page'):
        require_control()
        os.execve(str(PYTHON if PYTHON.exists() else shutil.which('python3')),
                  [str(PYTHON if PYTHON.exists() else shutil.which('python3')), '-B',
                   str(ROOT / 'wsl-bin/config/remote.py'), *args], environment())
    raise ValueError(f'Unsupported WSL command: {verb}. Run kilix help.')


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ValueError, OSError) as error:
        print(f'kilix: {error}', file=sys.stderr)
        raise SystemExit(2)
