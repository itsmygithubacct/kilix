#!/usr/bin/env python3
"""Ask once, at the first desktop login, whether to turn on an SSH server.

Plebian-OS provisioning writes a root-owned record naming the account only on
a fresh interactive installation, never on an automated one or an upgrade.
While that record names this account and this account has not answered, the
first-login chooser asks one yes/no question in its terminal. The default is
No: Enter, end of input, Ctrl-C or anything unanswered counts as No. The answer
is saved before anything happens, so the question is never asked again.

Yes runs the Plebian-OS helper through sudo in the same terminal, which asks
for the account password the way any other sudo command does. Later changes
use the same helper:

    sudo plebian-os-ssh-server enable | disable
    plebian-os-ssh-server status
"""
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys
import tempfile

OFFER = Path('/var/lib/plebian-os/ssh-server-offer')
HELPER = '/usr/local/sbin/plebian-os-ssh-server'
SUDO = '/usr/bin/sudo'
ANSWER_NAME = 'ssh-server-offer.json'
SCHEMA = 'kilix.ssh-server-offer/v1'
PREFIX = 'ssh-server-offer/v1 '

QUESTION = """
Remote login (SSH)

This computer has no SSH server, so nobody can log in to it over the network.
You can turn one on now. If you do, anyone who can reach this computer on the
network and knows your account password can log in as you.

You can change this later with:  sudo plebian-os-ssh-server enable
                                 sudo plebian-os-ssh-server disable
"""


def account():
    return pwd.getpwuid(os.geteuid()).pw_name


def offered_to(user, path=None):
    """True when the provisioning record exists, is root's, and names `user`."""
    path = OFFER if path is None else path
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 or info.st_size > 256):
            return False
        text = stream.read()
    return text == PREFIX + user + '\n'


def answer_path(config):
    return Path(config) / ANSWER_NAME


def answered(config):
    """Any entry at the answer path counts as answered, even one this cannot
    read: a damaged record must not turn into a question at every login."""
    path = answer_path(config)
    return path.exists() or path.is_symlink()


def save_answer(config, answer):
    config = Path(config)
    config.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = config.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise OSError('unsafe settings directory: ' + str(config))
    fd, temporary = tempfile.mkstemp(prefix='.' + ANSWER_NAME + '-', dir=config)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps({'schema': SCHEMA, 'answer': answer}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, answer_path(config))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ask(source, output):
    """Return 'yes' or 'no'. Only y/yes is yes; Enter or end of input is no."""
    print(QUESTION, file=output)
    while True:
        print('Turn on the SSH server now? [y/N] ', end='', file=output, flush=True)
        line = source.readline()
        if not line:
            print('', file=output)
            return 'no'
        reply = line.strip().lower()
        if reply in ('', 'n', 'no'):
            return 'no'
        if reply in ('y', 'yes'):
            return 'yes'
        print('Please answer y or n.', file=output)


def run_helper(output):
    """sudo the helper in this terminal; its output goes to the person, never
    to the chooser's captured stdout. Ctrl-C reaches sudo and the helper, not
    this process."""
    if not os.access(HELPER, os.X_OK):
        print('The SSH server helper is not installed; nothing was changed.', file=output)
        return 1
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        child = subprocess.Popen(
            [SUDO, HELPER, 'enable'], stdout=output, stderr=output,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
        return child.wait()
    except OSError as error:
        print(f'Could not run sudo: {error}', file=output)
        return 1
    finally:
        signal.signal(signal.SIGINT, previous)


def offer_once(config, source=None, output=None, interactive=None):
    """The whole first-login step. Never raises: the desktop must still start."""
    source = sys.stdin if source is None else source
    output = sys.stderr if output is None else output
    try:
        if interactive is None:
            interactive = source.isatty()
        if not interactive:
            return None  # Keep the question for an interactive login.
        if answered(config) or not offered_to(account()):
            return None
        try:
            reply = ask(source, output)
        except KeyboardInterrupt:
            print('', file=output)
            reply = 'no'
        save_answer(config, reply)
        if reply == 'no':
            print('SSH stays off.', file=output)
            return reply
        print('\nsudo asks for your account password to turn SSH on.', file=output)
        status = run_helper(output)
        if status != 0:
            print('\nSSH was not turned on. You can try again later with:\n'
                  '  sudo plebian-os-ssh-server enable', file=output)
        try:
            print('\nPress Enter to continue to your desktop. ', end='', file=output, flush=True)
            source.readline()
        except KeyboardInterrupt:
            pass
        return reply
    except Exception as error:  # noqa: BLE001 - first login must go on
        try:
            print(f'kilix: skipped the SSH server question: {error}', file=output)
        except Exception:  # noqa: BLE001
            pass
        return None
