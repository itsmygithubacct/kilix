"""Private run storage, using the host's pinned-directory and receipt helpers."""
from contextlib import contextmanager
import datetime as dt
import fcntl
import os
from pathlib import Path
import re
import stat
import uuid

from agent_delivery import Ledger
from agent_skills import directory
from kilix_sdk.paths import storage_home

SCHEMA = "kilix.session/v1"
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{32}\Z")
TERMINAL = frozenset(("completed", "failed", "interrupted"))


class SessionError(RuntimeError):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def new_id():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ-") + uuid.uuid4().hex


def root_path(root=None):
    value = root or os.environ.get("KILIX_AGENT_RUNS_HOME") or Path(storage_home()) / "agent-runs"
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise SessionError("run storage must be an absolute path without '..'")
    return path


def check_private(info, *, folder=False):
    kind = stat.S_ISDIR if folder else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != (0o700 if folder else 0o600)
            or (not folder and info.st_nlink != 1)):
        raise SessionError("managed storage must be owned, private (0700/0600), and unlinked")


class Files:
    def __init__(self, path, fd):
        self.path, self.fd = path, fd

    def json(self, name):
        return Ledger(self.fd, name, limit=2 * 1024 * 1024).read()

    def save(self, name, data):
        Ledger(self.fd, name, limit=2 * 1024 * 1024).write(data)

    def open(self, name, mode="rb"):
        if "/" in name or name in (".", ".."):
            raise SessionError("invalid managed file name")
        flags = os.O_RDONLY if mode == "rb" else os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self.fd)
        try:
            check_private(os.fstat(fd))
            return os.fdopen(fd, mode)
        except BaseException:
            os.close(fd)
            raise

    @contextmanager
    def lock(self, name):
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=self.fd)
        try:
            check_private(os.fstat(fd))
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


@contextmanager
def storage(root=None, *, create=False):
    path = root_path(root)
    with directory(path, create=create) as fd:
        check_private(os.fstat(fd), folder=True)
        yield Files(path, fd)


@contextmanager
def run_files(root, run_id, *, create=False):
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise SessionError("use the exact managed run ID from the launch receipt")
    with storage(root, create=create) as parent:
        if create:
            os.mkdir(run_id, 0o700, dir_fd=parent.fd)
            os.fsync(parent.fd)
        fd = os.open(run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent.fd)
        try:
            check_private(os.fstat(fd), folder=True)
            yield Files(parent.path / run_id, fd)
        finally:
            os.close(fd)


def process_identity(pid):
    """For inspection only. Stop requests never signal a PID from a receipt."""
    try:
        proc = Path("/proc") / str(pid)
        fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] in ("Z", "X") or proc.stat().st_uid != os.getuid():
            return None
        return {"pid": int(pid), "start_ticks": fields[19],
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
    except (OSError, ValueError, IndexError):
        return None
