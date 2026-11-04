"""Session-scoped GUI command routing, including non-interactive callers."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

from gui_apps import INFRASTRUCTURE, WRAPPERS, SAFE_COMMAND, installed_gui_commands

OBSERVED = set('google-chrome google-chrome-stable chromium chromium-browser firefox firefox-esr mpv mpvk kilix-amp evince xterm uxterm gparted cheese qemu-system-x86_64 qemu-system-aarch64'.split())


def terminal_use(command, args):
    if any(arg in ('--help', '-h', '--version', '-version') for arg in args):
        return True
    if any(arg in ('--headless', '-headless', '--headless=new', '--headless=old') for arg in args):
        return True
    if command.startswith('qemu-system-'):
        if '-nographic' in args or '--nographic' in args:
            return True
        for i, arg in enumerate(args[:-1]):
            if arg in ('-display', '--display') and args[i + 1].split(',')[0] in ('none', 'vnc', 'curses', 'egl-headless'):
                return True
        if '-vnc' in args:
            return True
    if command in ('mpv', 'mpvk'):
        if any(arg in ('--no-video', '--video=no', '--vid=no', '--vo=null', '--vo=kitty', '--vo=tct', '--vo=sixel', '--vo=drm') for arg in args):
            return True
    if command in ('evince',) and any(arg in ('--preview', '--print', '--print-settings') for arg in args):
        return True
    return False


def prepare(parent, kilix):
    parent = Path(parent)
    if not parent.is_absolute() or not Path(kilix).is_absolute():
        raise ValueError('session directory and Kilix command must be absolute')
    if not os.access(kilix, os.X_OK):
        raise ValueError('Kilix launcher is unavailable')
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix='gui-route-', dir=parent))
    excluded = set(os.environ.get('KILIX_RUN_ALIAS_EXCLUDE_APPS', '').split())
    commands = OBSERVED | set(installed_gui_commands()) | set(os.environ.get('KILIX_RUN_ALIAS_APPS', '').split())
    try:
        for name in sorted(commands - excluded - INFRASTRUCTURE - WRAPPERS - {'lxpolkit', 'xfce4-notifyd', 'xfce4-power-manager'}):
            if not SAFE_COMMAND.fullmatch(name):
                continue
            real = shutil.which(name)
            if not real:
                continue
            argv = ['/usr/bin/python3', '-B', str(Path(__file__).resolve()), 'launch', kilix, os.path.abspath(real)]
            entry = destination / name
            entry.write_text('#!/bin/sh\nexec ' + shlex.join(argv) + ' "$@"\n')
            entry.chmod(0o700)
        return str(destination)
    except Exception:
        shutil.rmtree(destination)
        raise


def launch(kilix, executable, args):
    direct = (os.environ.get('KILIX_PRIVATE_XAPP') == '1'
              or os.environ.get('KILIX_RUN_ALIASES', '').lower() in ('0', 'no', 'false', 'off')
              or not os.environ.get('DISPLAY')
              or terminal_use(Path(executable).name, args))
    if not direct and Path(executable).name.startswith('qemu-system-'):
        if any(flag in args for flag in ('-daemonize', '--daemonize')):
            print('Kilix: graphical QEMU runs in the foreground of its tab; '
                  'closing that tab ends the VM. Use KILIX_RUN_ALIASES=0 '
                  'for an external daemon.', file=sys.stderr)
            args = [arg for arg in args if arg not in ('-daemonize', '--daemonize')]
    if not direct:
        # A shell can inherit the desktop's in-place guard. An ordinary app
        # command still requests its own tab; private children bypass above.
        os.environ.pop('KILIX_IN_OVERLAY', None)
    argv = [executable, *args] if direct else [kilix, 'run', executable, *args]
    os.execv(argv[0], argv)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    setup = commands.add_parser('prepare')
    setup.add_argument('directory')
    setup.add_argument('kilix')
    run = commands.add_parser('launch')
    run.add_argument('kilix')
    run.add_argument('executable')
    run.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == 'prepare':
        print(prepare(args.directory, args.kilix))
    else:
        launch(args.kilix, args.executable, args.arguments)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'Kilix GUI routing: {error}', file=sys.stderr)
        raise SystemExit(1)
