"""kilix video --neuronte: play a video drawn by Neuronte's nets.

Finds a Neuronte checkout that has the player and its built library, then runs
tools/neuronte-play.py there with the remaining arguments. Nothing is installed
or built here; a missing piece is reported with what to do about it.
"""
import os
import subprocess
import sys
from pathlib import Path

USAGE = 'usage: kilix video --neuronte FILE_OR_URL [player options]'
# The modes the player switches on; written back if it dies without doing so.
RESTORE = '\x1b[?2026l\x1b[0m\x1b[?25h\x1b[?1049l'


def neuronte_home(environ):
    """NEURONTE_HOME, or where the other sources are installed."""
    explicit = environ.get('NEURONTE_HOME')
    if explicit:
        return Path(explicit)
    sources = environ.get('GPU_TERMINAL_SOURCE_HOME') or Path(environ.get('HOME', '/')) / '.local/gpu_terminal/sources'
    return Path(sources) / 'neuronte'


def player_command(arguments, environ):
    """The command to run, or a message saying what is missing."""
    home = neuronte_home(environ)
    player = home / 'tools/neuronte-play.py'
    library = Path(environ.get('NEURONTE_LIB') or home / 'build/libneuronte.so')
    if not player.is_file():
        return None, (f'Neuronte\'s video player was not found at {player}.\n'
                      'Set NEURONTE_HOME to a Neuronte checkout that has it.')
    if not library.is_file():
        return None, (f'Neuronte\'s library was not found at {library}.\n'
                      f'Build it with: make -C {home} build/libneuronte.so')
    return [sys.executable, '-B', str(player), '--library', str(library), *arguments], None


def main(arguments=None, environ=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    environ = os.environ if environ is None else environ
    if not arguments:
        print(USAGE, file=sys.stderr)
        return 2
    command, problem = player_command(arguments, environ)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    status = subprocess.run(command).returncode
    if status < 0 or status >= 128:
        # Killed before its own cleanup ran: give the terminal back.
        try:
            with open('/dev/tty', 'w') as tty:
                tty.write(RESTORE)
        except OSError:
            pass
        return 128 - status if status < 0 else status
    return status


if __name__ == '__main__':
    sys.exit(main())
