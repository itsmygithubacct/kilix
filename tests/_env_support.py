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
