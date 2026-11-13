#!/usr/bin/env python3
"""Choose and remember the desktop used when a Pleb session starts."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


PRIMARY = (
    ('external', 'Kilix 95', 'Classic graphical desktop'),
    ('icewm', 'IceWM', 'Window manager desktop'),
    ('tui', 'Kilix TUI', 'Text desktop'),
    ('zell', 'zell', 'RPG Desktop'),
)
SECONDARY = (
    ('xp', 'Kilix XP', 'Classic desktop with the XP theme'),
    ('cap', 'Kilix Cap', 'Mansion desktop'),
    ('land', 'Kilix Land', 'Walkable desktop'),
    ('builtin', 'Built-in desktop', 'Compatibility desktop'),
    ('none', 'Terminal only', 'Start with a shell'),
    ('auto', 'Automatic', 'Use the available classic desktop'),
)
ALIASES = {'95': 'external', 'kilix95': 'external', 'kilix-95': 'external',
           'kilix-icewm': 'icewm', 'kilix-tui': 'tui', 'kilix-xp': 'xp',
           'kilix-cap': 'cap', 'mansion': 'cap', 'kilix-land': 'land',
           'kilix-land-desktop': 'land', 'zell-neuronte': 'zell', 'kilix-zell': 'zell'}
PROVIDERS = {row[0] for row in PRIMARY + SECONDARY}
SCHEMA = 'kilix.desktop-choice/v1'


def provider(value):
    value = ALIASES.get(value, value)
    if value not in PROVIDERS:
        raise ValueError('unknown desktop: ' + value)
    return value


def read_file(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_size > 1024 * 1024:
            raise ValueError('unsafe desktop settings: ' + str(path))
        return stream.read()


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError('unsafe desktop settings directory: ' + str(path))


def write_file(path, text):
    read_file(path)  # Refuse links and non-regular existing entries.
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(config):
    private_dir(config)
    fd = os.open(config / '.desktop-choice.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError('unsafe desktop choice lock')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def saved(config):
    values = {}
    for line in (read_file(config / 'kilix.env') or '').splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            values[key] = value
    choice = values.get('KILIX_DESKTOP_PROVIDER', 'auto')
    if choice == 'external' and values.get('KILIX_DESKTOP_FLAVOR') == 'xp':
        return 'xp'
    return provider(choice)


def state(config):
    text = read_file(config / 'desktop-choice.json')
    if text is None:
        return None
    value = json.loads(text)
    if not isinstance(value, dict) or value.get('schema') != SCHEMA or type(value.get('complete')) is not bool:
        raise ValueError('invalid desktop choice record')
    return value


def mark(config, complete):
    write_file(config / 'desktop-choice.json', json.dumps({'schema': SCHEMA, 'complete': complete}) + '\n')


def set_choice(config, value):
    value = provider(value)
    text = read_file(config / 'kilix.env') or ''
    lines = [line for line in text.splitlines()
             if not line.startswith(('KILIX_DESKTOP_PROVIDER=', 'KILIX_DESKTOP_FLAVOR='))]
    lines += ['KILIX_DESKTOP_PROVIDER=' + value,
              'KILIX_DESKTOP_FLAVOR=' + ('xp' if value == 'xp' else '95')]
    write_file(config / 'kilix.env', '\n'.join(lines) + '\n')
    mark(config, True)
    return value


def prompt(current, source, output):
    more = False
    while True:
        print('\nChoose your default desktop\n', file=output)
        print('Primary choices', file=output)
        rows = PRIMARY + SECONDARY if more else PRIMARY
        for index, (key, name, description) in enumerate(rows, 1):
            if index == len(PRIMARY) + 1:
                print('\nOther desktops', file=output)
            selected = ' (current)' if key == current else ''
            print(f'  {index}. {name} — {description}{selected}', file=output)
        if not more:
            print('\n  m. More desktops', file=output)
        print('  q. Decide next login\n', file=output)
        print('Choose a number: ', end='', file=output, flush=True)
        answer = source.readline()
        if not answer or answer.strip().lower() == 'q':
            return None
        answer = answer.strip().lower()
        if answer == 'm':
            more = True
        elif answer.isdecimal() and 1 <= int(answer) <= len(rows):
            return rows[int(answer) - 1][0]
        else:
            print('Enter one of the numbers shown.', file=output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('command', choices=('show', 'list', 'set', 'request', 'choose'))
    parser.add_argument('name', nargs='?')
    parser.add_argument('--session-start', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'list':
            print('\n'.join(row[0] for row in PRIMARY + SECONDARY))
            return 0
        if args.command == 'show':
            print(saved(args.config))
            return 0
        with locked(args.config):
            if args.command == 'set':
                if args.name is None:
                    raise ValueError('set needs a desktop name')
                print('default desktop: ' + set_choice(args.config, args.name))
            elif args.command == 'request':
                if state(args.config) is None:
                    mark(args.config, False)
            else:
                record = state(args.config)
                if args.session_start and (record is None or record['complete']):
                    # Old installations keep their configured launch until a
                    # choice has actually been made through this interface.
                    if record is not None:
                        print(saved(args.config))
                    return 0
                if not sys.stdin.isatty():
                    if args.session_start:
                        return 0  # Keep the request for an interactive login.
                    raise ValueError('choose a desktop from an interactive terminal')
                choice = prompt(saved(args.config), sys.stdin, sys.stderr)
                if choice is not None:
                    print(set_choice(args.config, choice))
        return 0
    except (OSError, ValueError) as error:
        print('kilix default-desktop: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
