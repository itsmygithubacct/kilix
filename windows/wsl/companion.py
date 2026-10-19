#!/usr/bin/env python3
"""Linux tools and authenticated native-window control for Kilix WSL."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
from games import game_path, installed_games

ROOT = Path(__file__).resolve().parents[1]
STATE = Path.home() / '.local/share/kilix-wsl'
INSTALL = (STATE / 'current').resolve() if (STATE / 'current').is_symlink() else STATE
BRIDGE = ROOT / 'wsl-bin/kilix'
KITTEN = ROOT / 'kitty/launcher/kitten.exe'
PYTHON = INSTALL / 'bin/python3'


def environment():
    env = os.environ.copy()
    paths = [str(INSTALL / 'bin'), str(ROOT / 'wsl-bin'), *env.get('PATH', '').split(':')]
    env['PATH'] = ':'.join(dict.fromkeys(paths))
    env['KILIX_NEEDLE_KILIX'] = str(BRIDGE)
    env['KILIX_KITTEN'] = str(ROOT / 'wsl-bin/kitten')
    env['KILIX_CONTENT_ROOT'] = str((STATE / 'content').resolve())
    # The Windows renderer cannot open Linux /dev/shm objects.
    env['KITTYFB_TRANSPORT'] = 'inline'
    return env


def require_control():
    if (not re.fullmatch(r'tcp:127\.0\.0\.1:[0-9]+', os.environ.get('KITTY_LISTEN_ON', ''))
            or not os.environ.get('KITTY_RC_PASSWORD')
            or not re.fullmatch(r'[1-9][0-9]*', os.environ.get('KITTY_WINDOW_ID', ''))):
        raise ValueError('Open a new Kilix WSL window with WSL control enabled first.')


def parse_launch(args):
    """Separate supported options from literal child argv, including help."""
    options, program = [], []
    flags = {'--hold', '--keep-focus', '--copy-colors', '--allow-remote-control',
             '--stdin-add-formatting', '--stdin-add-line-wrap-markers', '--no-response', '--self',
             '--wait-for-child-to-exit', '--hold-after-ssh', '--help', '-h'}
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
            if sep:
                raise ValueError(f'{key} does not take a value')
            options.append((key, None))
        elif key in values:
            if not sep:
                i += 1
                if i == len(args):
                    raise ValueError(f'{key} needs a value')
                value = args[i]
            options.append((key, value))
        else:
            raise ValueError(f'WSL launch does not support {key}')
        i += 1
    return options, program


def translate_launch(args):
    """Preserve remote options, but start Linux argv through this distro's WSL."""
    parsed, program = parse_launch(args)
    options = []
    linux_env = {'PATH': environment()['PATH'], 'KITTYFB_TRANSPORT': 'inline'}
    cwd = os.getcwd()
    for key, value in parsed:
        if key == '--cwd':
            if value in ('oldest', 'last_reported', 'root'):
                raise ValueError(f'WSL launch cannot resolve --cwd={value}; use a Linux path or current')
            if value != 'current':
                cwd = os.path.abspath(os.path.expanduser(value))
        elif key == '--env':
            name, assigned, content = value.partition('=')
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name):
                raise ValueError(f'Invalid environment variable name: {name}')
            linux_env[name] = content if assigned else None
        else:
            if key == '--type' and value not in ('window', 'tab', 'os-window', 'overlay', 'overlay-main', 'background'):
                raise ValueError(f'WSL launch does not support --type={value}')
            options.append(key if value is None else key + '=' + value)
    distro = os.environ.get('WSL_DISTRO_NAME')
    if not distro:
        raise ValueError('WSL_DISTRO_NAME is missing')
    user = pwd.getpwuid(os.getuid())
    command = ['wsl.exe', '--distribution', distro, '--user', user.pw_name, '--cd', cwd]
    # Explicit argv keeps Linux values out of both Windows and Linux shell parsing.
    command += ['--exec', '/usr/bin/env']
    for name, value in linux_env.items():
        if value is None:
            command += ['-u', name]
    command += ['--', *(f'{name}={value}' for name, value in linux_env.items() if value is not None)]
    command += program or [user.pw_shell or '/bin/sh', '-l']
    return options + ['--', *command]


def remote(args):
    launch_help = (args[:1] == ['launch'] and
                   any(key in ('--help', '-h') for key, _ in parse_launch(args[1:])[0]))
    if not args or args in (['--help'], ['-h']) or (len(args) == 2 and args[1] in ('--help', '-h')) or launch_help:
        return subprocess.call([str(KITTEN), '@', *(args or ['--help'])], env=environment())
    require_control()
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
  launch, --launch PROGRAM [ARGS]  Run a Linux app in a new native Kilix tab
  apps [--json]                List installed WSL apps
  games list | play NAME [ARGS]  List or run an installed game in this pane
  ls [--panes]                  List native tabs/panes
  pane, tab, focus, watch       Control the current native Kilix window
  fullscreen                   Toggle native fullscreen
  split, new-pane               Start Linux shells/programs in native panes
  new-tab, new-page             Start a Linux tab
  @ COMMAND [ARGS]              Authenticated native remote control
  help, version                 WSL help / native build version
  native [ARGS]                 Native launcher commands (native help lists them)
  engine, powershell, cmd, wsl  Native launcher shortcuts
Aliases: -h/--help, --version, --cmd, --wsl. Use kilix @ --help for remote verbs.
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
        if not PYTHON.exists() or not (INSTALL / 'needle/needle_cli.py').exists():
            raise ValueError('WSL companion dependencies are not installed; run the WSL setup script.')
        os.execve(str(PYTHON), [str(PYTHON), '-B', str(ROOT / 'wsl/needle-run.py'),
                              str(INSTALL / 'needle/needle_cli.py'), *rest], environment())
    if verb == 'games' and (rest in (['help'], ['--help'], ['-h']) or
                            (len(rest) == 2 and rest[0] in ('list', 'play') and rest[1] in ('--help', '-h'))):
        print('''Usage: kilix games list [--json]
       kilix games play NAME [ARGS]
Lists installed public catalog games or runs one in the current pane.
Names and aliases from the catalog are accepted, such as pong, chess,
solitaire and lander. Use kilix --launch NAME [ARGS] for a new tab.''')
        return 0
    if verb == 'apps' or (verb == 'games' and (not rest or rest[0] == 'list')):
        if (rest if verb == 'apps' else rest[1:]) not in ([], ['--json']):
            raise ValueError('Use kilix apps [--json] or kilix games list [--json]')
        names = installed_games(INSTALL)
        print('\n'.join(names) if '--json' not in rest else json.dumps(names))
        return 0
    if verb == 'games' and rest[:1] == ['play']:
        if len(rest) < 2:
            raise ValueError('Use kilix games play NAME')
        app = game_path(rest[1], INSTALL)
        if app is None:
            raise ValueError(f'Game is not installed: {rest[1]}. Run kilix games list.')
        os.execve(str(app), [str(app), *rest[2:]], environment())
    if verb in ('--launch', 'launch'):
        if not rest:
            raise ValueError('Use kilix --launch PROGRAM [ARGS]')
        game = game_path(rest[0], INSTALL)
        program = str(game) if game else shutil.which(rest[0], path=environment()['PATH'])
        if not program:
            raise ValueError(f'Linux program is not installed: {rest[0]}')
        return remote(['launch', '--type=tab', '--tab-title=' + Path(program).name,
                       '--', program, *rest[1:]])
    if verb == 'fullscreen':
        parser = argparse.ArgumentParser(prog='kilix fullscreen',
                                         description='Toggle fullscreen for the invoking native Kilix window')
        parser.parse_args(rest)
        require_control()
        # The Linux SDK uses its controlling tty here, which cannot address the
        # Windows renderer. Match the invoking pane over authenticated control.
        return remote(['resize-os-window', '--match=id:' + os.environ['KITTY_WINDOW_ID'],
                       '--action=toggle-fullscreen'])
    if verb in ('ls', 'pane', 'tab', 'focus', 'watch', 'new-pane', 'split', 'new-tab', 'new-page'):
        # Let the SDK parse help without a connection. Actual operations return
        # through our kitten bridge, where remote() still requires control.
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
