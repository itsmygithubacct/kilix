"""Small local power, filesystem and committed-update readings for system voice."""
from pathlib import Path
import os
import re
import stat

UPDATE_NOTICE = 'system-voice-update-complete'
LOCAL_FILESYSTEMS = frozenset(('ext2', 'ext3', 'ext4', 'btrfs', 'xfs', 'f2fs',
                             'zfs', 'bcachefs', 'vfat', 'exfat', 'ntfs', 'ntfs3', 'overlay'))


def read(path):
    try:
        return path.read_text(errors='replace').strip()
    except OSError:
        return None


def external_power(root=Path('/')):
    """True for external power, False for battery, None when not known."""
    batteries, supplies = [], []
    for p in (Path(root)/'sys/class/power_supply').glob('*'):
        if read(p/'scope') == 'Device':
            continue
        kind = read(p/'type') or ''
        if kind == 'Battery' and read(p/'present') != '0':
            batteries.append(read(p/'status'))
        elif kind in ('Mains', 'Wireless') or kind.startswith('USB'):
            supplies.append(read(p/'online'))
    if not batteries:
        return None  # A desktop without a battery cannot run on one.
    if '1' in supplies:
        return True
    if supplies and all(value == '0' for value in supplies):
        return False
    if 'Discharging' in batteries:
        return False
    if all(status in ('Charging', 'Full', 'Not charging') for status in batteries):
        return True
    return None


def disk_space(paths, *, mountinfo=Path('/proc/self/mountinfo'), statvfs=os.statvfs):
    """Unique writable local filesystems containing the selected paths.

    Select mounts lexically before statvfs: remote filesystems must never block
    the speech worker. None entries preserve unknown readings during recovery.
    """
    text = read(mountinfo)
    if text is None:
        return None
    mounts = []
    for line in text.splitlines():
        fields = line.split()
        try:
            divider = fields.index('-')
            mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
            mounts.append((mount, fields[2], fields[divider+1]))
        except (IndexError, ValueError):
            continue
    selected = {}
    for path in paths:
        path = os.path.abspath(os.path.expanduser(str(path)))
        candidates = [m for m in mounts if path == m[0] or path.startswith(m[0].rstrip('/')+'/')]
        if candidates:
            mount, device, kind = max(candidates, key=lambda m: len(m[0]))
            if kind in LOCAL_FILESYSTEMS:
                selected.setdefault(device, mount)
    values = []
    for mount in selected.values():
        try:
            info = statvfs(mount)
            if info.f_flag & os.ST_RDONLY:
                continue
            total, available = info.f_blocks*info.f_frsize, info.f_bavail*info.f_frsize
            values.append((total, max(0, available)) if total > 0 else None)
        except OSError:
            values.append(None)
    return values or None


class UpdateNotice:
    """Only announce a new successful update observed by this running session."""
    def __init__(self, path):
        self.path = Path(path)
        self.seen = self.token()

    def token(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                    return None
                token = os.read(fd, 64).decode('ascii').strip()
            finally:
                os.close(fd)
        except (OSError, UnicodeError):
            return None
        return token if re.fullmatch('[0-9a-f]{32}', token) else None

    def poll(self):
        token = self.token()
        if token is None or token == self.seen:
            return False
        self.seen = token
        return True
