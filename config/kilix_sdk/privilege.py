"""Consent-bearing handoff to fixed, root-owned OS helpers."""
from pathlib import Path
import os
import stat
import subprocess
import sys

WAYDROID_SETUP = "/usr/lib/plebian-os/waydroid/plebian-os-waydroid-setup"


def _validate(helper):
    if helper != WAYDROID_SETUP:
        raise ValueError("unsupported privileged helper")
    path = Path(helper)
    for item in (path, *path.parents):
        info = item.lstat()
        expected = stat.S_ISREG(info.st_mode) if item == path else stat.S_ISDIR(info.st_mode)
        if not expected or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("unsafe root-owned helper path")
    if not os.access(path, os.X_OK):
        raise RuntimeError("OS setup helper is not executable")


def _terminal(helper, title):
    _validate(helper)
    print(title, flush=True)
    print("This installs Android images and system packages and enables the Waydroid container service.", flush=True)
    try:
        answer = input("Continue? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        return 1
    if answer.strip().lower() not in ("y", "yes"):
        return 1
    # No shell, arbitrary arguments, environment forwarding, or automatic yes.
    return subprocess.run(["/usr/bin/sudo", "--", helper], check=False).returncode


def run_helper(helper, *, title):
    _validate(helper)
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _terminal(helper, title)
    if not os.environ.get("DISPLAY"):
        raise RuntimeError("setup requires a terminal or X display for consent")
    return subprocess.run([
        "/usr/bin/xterm", "-T", title, "-e", sys.executable,
        str(Path(__file__).resolve()), helper, title,
    ], check=False).returncode


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(2)
    raise SystemExit(_terminal(sys.argv[1], sys.argv[2]))
