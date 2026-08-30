"""Small, reviewable boundary for invoking root-owned system installers.

Applications identify one fixed helper.  Kilix validates that the helper and
every directory above it are root-owned and not writable by other users, then
runs it through ``sudo``.  A caller with a terminal uses that terminal; a GUI
application uses the X terminal already shipped by Plebian-OS so password and
progress output remain visible inside the owning application surface.

This module deliberately does not accept helper arguments or shell commands.
The privileged helper owns its complete operation and input closure.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import threading

from ._process import stop_process as _stop_process


class PrivilegedHelperError(RuntimeError):
    """A trusted privileged helper could not be safely invoked."""


def _trusted_metadata(path: Path, *, directory: bool) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PrivilegedHelperError(f"trusted system path is unavailable: {path}") from error
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if path.is_symlink() or not expected(metadata.st_mode):
        kind = "directory" if directory else "helper"
        raise PrivilegedHelperError(f"trusted system {kind} is unsafe: {path}")
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise PrivilegedHelperError(
            f"trusted system path is not exclusively root-controlled: {path}"
        )


def validate_helper(helper: str) -> str:
    """Return a fixed helper path after validating its root-owned ancestry."""
    if not isinstance(helper, str) or not helper or not os.path.isabs(helper):
        raise PrivilegedHelperError("system helper path must be absolute")
    if any(character in helper for character in ("\0", "\n", "\r")):
        raise PrivilegedHelperError("system helper path contains control characters")
    path = Path(helper)
    _trusted_metadata(path, directory=False)
    if not os.access(path, os.X_OK):
        raise PrivilegedHelperError(f"trusted system helper is not executable: {path}")
    parent = path.parent
    while True:
        _trusted_metadata(parent, directory=True)
        if parent == parent.parent:
            break
        parent = parent.parent
    return str(path)


def _interactive_terminal() -> bool:
    return bool(
        getattr(sys.stdin, "isatty", lambda: False)()
        and getattr(sys.stderr, "isatty", lambda: False)()
    )


def run_helper(
    helper: str,
    *,
    title: str = "System installation",
    popen=subprocess.Popen,
) -> int:
    """Run one root-owned, argument-free installer and return its exit status.

    No shell is involved.  The GUI path opens ``sudo`` in ``xterm`` because an
    app hosted by XPane has no controlling terminal of its own.
    """
    trusted = validate_helper(helper)
    if os.geteuid() == 0:
        argv = [trusted]
    else:
        sudo = shutil.which("sudo")
        if not sudo:
            raise PrivilegedHelperError("sudo is required for first-use installation")
        if _interactive_terminal():
            argv = [sudo, "--", trusted]
        else:
            display = os.environ.get("DISPLAY", "")
            xterm = shutil.which("xterm")
            if not display or not xterm:
                raise PrivilegedHelperError(
                    "first-use installation needs a terminal or an X display with xterm"
                )
            clean_title = "".join(
                character for character in str(title)
                if character >= " " and character not in "\x7f\n\r"
            ).strip() or "System installation"
            argv = [xterm, "-T", clean_title, "-e", sudo, "--", trusted]
    process = None
    previous = {}

    def interrupted(signum, _frame):
        raise PrivilegedHelperError(
            f"system installation was interrupted by signal {signum}"
        )

    try:
        # A new process group gives cancellation one bounded target without
        # detaching an interactive sudo process from its controlling terminal.
        # ``start_new_session=True`` would make /dev/tty unavailable even when
        # stdin is a TTY, which prevents sudo from presenting its prompt.
        process = popen(argv, process_group=0)
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, interrupted)
        return int(process.wait())
    except OSError as error:
        raise PrivilegedHelperError(f"could not start system helper: {error}") from error
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if process is not None and process.poll() is None:
            _stop_process(process, process_group=True)


__all__ = ["PrivilegedHelperError", "run_helper", "validate_helper"]
