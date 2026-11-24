"""The environment a test hands to a child process.

A Kilix session exports upwards of sixty variables -- storage roots, state and
cache directories, chrome settings, Kitty control channels. A test that copies
``os.environ`` and overrides only the handful it cares about hands the child a
sandbox path for those and the operator's live session for everything else. The
product then refuses the mixture, correctly, and the suite reports a failure
that belongs to the machine rather than to the code.

So strip the whole family first and put back only what the test names.
"""

import os
import subprocess
import sys
import tempfile

#: Every variable this stack uses to locate itself. ``KILIX`` deliberately has
#: no trailing underscore: it must also catch ``KILIX95_*``. ``PLEB`` is here
#: because the five modules this helper replaced stripped ``PLEB_*`` inline,
#: and a shared helper that dropped it would have narrowed them: measured
#: against a live 120-variable session, the inline tuple stripped 51 names and
#: this tuple strips 69, missing none of the 51.
STACK_PREFIXES = ("KILIX", "GPU_TERMINAL", "KITTY", "PLEB")

#: The minimal PATH a test gives a child: the host's base system plus where its
#: package manager puts bash and python3. On Debian that is /usr/bin:/bin; on
#: FreeBSD bash and python3 are packages, installed under /usr/local/bin.
SYSTEM_PATH = ("/usr/local/bin:/usr/bin:/bin" if sys.platform.startswith("freebsd")
               else "/usr/bin:/bin")

#: The host's own interpreters, as the product resolves them (kilix_system_python).
SYSTEM_PYTHON = ("/usr/local/bin/python3" if sys.platform.startswith("freebsd")
                 else "/usr/bin/python3")
SYSTEM_BASH = "/usr/local/bin/bash" if sys.platform.startswith("freebsd") else "/bin/bash"


def sandbox_env(**overrides):
    """The parent environment minus every stack variable, plus *overrides*.

    Overrides are applied last, so a test can name a variable whose prefix was
    just stripped -- which is the usual case, since the point is to point the
    child at a sandbox rather than at the session running the tests.
    """
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(STACK_PREFIXES)
    }
    env.update(overrides)
    return env


def run_interactive_bash(arguments, script, env, timeout=30):
    """Feed *script* to an interactive bash (``bash ... -i``) and capture both streams.

    Interactive bash adopts stderr as its terminal and stops itself with SIGTTIN
    while ``tcgetpgrp()`` on that descriptor names another process group. On
    Linux a pipe fails that call with ENOTTY; FreeBSD pipes answer it, so a
    piped stderr leaves the shell stopped until the timeout. A temporary file
    answers ENOTTY on every host, and reads back the same text.
    """
    with tempfile.TemporaryFile() as stderr:
        result = subprocess.run(["bash", *arguments, "-i"], input=script, env=env,
                                stdout=subprocess.PIPE, stderr=stderr,
                                text=True, timeout=timeout)
        stderr.seek(0)
        result.stderr = stderr.read().decode("utf-8", "replace")
    return result


def running_executable(pid: int) -> str:
    """Resolved executable of PID: /proc on Linux, procstat(1) on FreeBSD."""
    if sys.platform.startswith("freebsd"):
        result = subprocess.run(["procstat", "-h", "-b", str(pid)],
                                capture_output=True, text=True)
        fields = result.stdout.split(None, 3)
        return fields[3].strip() if len(fields) == 4 else ""
    try:
        return os.path.realpath(f"/proc/{pid}/exe")
    except OSError:
        return ""


def open_fd_count() -> int:
    """How many descriptors this process holds, for leak checks.

    /proc/self/fd on Linux; elsewhere (FreeBSD mounts no fdescfs by default)
    each descriptor number up to the soft limit is probed with F_GETFD.
    """
    try:
        return len(os.listdir("/proc/self/fd"))
    except FileNotFoundError:
        pass
    import fcntl
    import resource
    limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if limit == resource.RLIM_INFINITY or limit > 1 << 20:
        limit = 1 << 20
    count = 0
    for descriptor in range(limit):
        try:
            fcntl.fcntl(descriptor, fcntl.F_GETFD)
        except OSError:
            continue
        count += 1
    return count


def take_posix_shm(name: str) -> bytes:
    """Read a POSIX shared-memory object's bytes, then unlink it.

    Linux exposes these objects as files under /dev/shm; FreeBSD keeps them out
    of the file system, so there they are opened with shm_open(3).
    """
    if os.path.isdir("/dev/shm") and sys.platform.startswith("linux"):
        path = "/dev/shm/" + name.lstrip("/")
        with open(path, "rb") as stream:
            data = stream.read()
        os.unlink(path)
        return data
    import ctypes
    import ctypes.util
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    libc.shm_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
    libc.shm_unlink.argtypes = [ctypes.c_char_p]
    encoded = ("/" + name.lstrip("/")).encode()
    descriptor = libc.shm_open(encoded, os.O_RDONLY, 0)
    if descriptor < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), name)
    try:
        size = os.fstat(descriptor).st_size
        chunks, remaining = [], size
        while remaining:
            chunk = os.pread(descriptor, remaining, size - remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    if libc.shm_unlink(encoded) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), name)
    return b"".join(chunks)
