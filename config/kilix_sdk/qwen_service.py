"""Install and control the prepared provider as a desktop user's service."""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile

from . import qwen_provider as provider

UNIT = 'kilix-qwen-provider.service'


def quoted(value):
    # systemd expands specifiers and environment variables even inside quotes.
    value = str(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('service path contains a control character')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def render(generation):
    executable = generation/'bin/kilix-qwen-provider'
    return ('# Managed by kilix tts provider service install\n'
        '[Unit]\nDescription=Kilix local Qwen speech provider\n'
        '[Service]\nType=exec\n'
        'ExecStart=/usr/bin/python3 -I -B '+quoted(executable)+' serve\n'
        'ExecStartPost=/usr/bin/python3 -I -B '+quoted(executable)+' wait-ready ${MAINPID}\n'
        'UMask=0077\nNoNewPrivileges=yes\nKillMode=mixed\n'
        'TimeoutStartSec=300\nTimeoutStopSec=15\nRestart=no\n'
        'StandardInput=null\nStandardOutput=journal\nStandardError=journal\n'
        '[Install]\nWantedBy=default.target\n').encode()


def control(*arguments):
    # Environment-selected model/receipt paths are carried by the bound launcher.
    startup = arguments[0] in {'start','restart','enable'}
    subprocess.run(['/usr/bin/systemctl', '--user', *arguments], check=True,
                   stdin=subprocess.DEVNULL, timeout=335 if startup else 35)


def unit_directory():
    base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
    if not base.is_absolute():
        raise ValueError('XDG_CONFIG_HOME must be absolute')
    return base/'systemd/user'


def ensure_unit_directory(directory):
    if not directory.is_absolute() or '..' in directory.parts:
        raise ValueError('service configuration path must be canonical')
    for path in (*reversed(directory.parents), directory):
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                or info.st_mode & 0o022 and not sticky_root):
            raise ValueError('unsafe service configuration directory')
    if directory.stat().st_uid != os.geteuid():
        raise ValueError('service configuration must belong to the desktop user')


def read_owned(directory_fd, name):
    try:
        fd = os.open(name, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022 or info.st_size > 65536):
            raise ValueError('unsafe provider service configuration')
        return stream.read()


def write_atomic(directory_fd, name, payload):
    directory = Path('/proc/self/fd')/str(directory_fd)
    fd, temporary = tempfile.mkstemp(prefix='.qwen-unit-', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(Path(temporary).name, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(Path(temporary).name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def active():
    result = subprocess.run(['/usr/bin/systemctl','--user','show',UNIT,
                             '--property=ActiveState','--value'],capture_output=True,
                            check=True,text=True,timeout=10)
    return result.stdout.strip() in {'active','activating','reloading','deactivating'}


def installed_generation():
    root = provider.managed_root()
    provider.private_chain(root)
    root_fd = os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        recorded = read_owned(root_fd,'service-unit')
    finally:
        os.close(root_fd)
    if recorded is None:
        raise ValueError('provider service is not installed')
    starts = [line.removeprefix('ExecStart=') for line in recorded.decode().splitlines()
              if line.startswith('ExecStart=')]
    if len(starts)!=1:
        raise ValueError('invalid recorded provider unit')
    arguments = shlex.split(starts[0])
    if len(arguments)!=5 or arguments[:3]!=['/usr/bin/python3','-I','-B'] or arguments[4]!='serve':
        raise ValueError('invalid recorded provider command')
    executable = Path(arguments[3].replace('%%','%').replace('$$','$'))
    generation = provider.validate_generation(root,executable.parent.parent)
    if recorded != render(generation):
        raise ValueError('recorded provider unit changed')
    directory_fd = os.open(unit_directory(),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        if read_owned(directory_fd,UNIT)!=recorded:
            raise ValueError('provider unit has user changes')
    finally:
        os.close(directory_fd)
    return generation


def restore(directory_fd,name,payload):
    if payload is None:
        try:
            os.unlink(name,dir_fd=directory_fd)
            os.fsync(directory_fd)
        except FileNotFoundError:
            pass
    else:
        write_atomic(directory_fd,name,payload)


def install(*, allow_stop=False):
    root = provider.managed_root()
    generation = provider.current_generation(root)
    # Validate the entire immutable generation before publishing a login route.
    executable = generation/'bin/kilix-qwen-provider'
    provider.owned_run(None, ['/usr/bin/python3', '-I', '-B', executable, 'status'],
                       provider.clean_environment(), timeout=300, capture=True)
    directory = unit_directory()
    ensure_unit_directory(directory)
    root_fd = os.open(root, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    directory_fd = os.open(directory, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        fcntl.flock(root_fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        if provider.current_generation(root) != generation:
            raise ValueError('provider selection changed; retry service installation')
        previous = read_owned(directory_fd, UNIT)
        recorded = read_owned(root_fd, 'service-unit')
        if previous is not None and previous != recorded:
            raise ValueError('provider unit has user changes; refusing to replace it')
        payload = render(generation)
        if previous is not None and previous!=payload and active():
            if not allow_stop:
                raise ValueError('stop the service before replacing its generation, or use service enable')
            control('stop',UNIT)
        try:
            write_atomic(directory_fd, UNIT, payload)
            write_atomic(root_fd, 'service-unit', payload)
            control('daemon-reload')
        except BaseException:
            restore(directory_fd,UNIT,previous)
            restore(root_fd,'service-unit',recorded)
            try:
                control('daemon-reload')
            except subprocess.SubprocessError:
                pass  # Disk selection restored; original manager failure is reported.
            raise
    finally:
        os.close(directory_fd)
        os.close(root_fd)
    print(str(directory/UNIT))


def main(action):
    if action in {'install', 'enable'}:
        install(allow_stop=action=='enable')
        if action == 'enable':
            control('restart',UNIT)
            control('enable',UNIT)
    else:
        if action in {'start','restart'}:
            installed_generation()  # Refuse user edits before starting a unit.
        commands = {'start': ('start',), 'stop': ('stop',), 'restart': ('restart',),
                    'disable': ('disable', '--now'),
                    'status': ('show', '--property=LoadState,ActiveState,SubState,UnitFileState,Result,MainPID')}
        control(*commands[action], UNIT)
    if action in {'enable', 'start', 'restart', 'status'}:
        generation = installed_generation()
        executable = generation/'bin/kilix-qwen-provider'
        payload = provider.owned_run(None, ['/usr/bin/python3', '-I', '-B', executable, 'status'],
                                     provider.clean_environment(), timeout=300, capture=True)
        # Unit activity and provider readiness are separate observable facts.
        print(payload.decode().strip())
    return 0
