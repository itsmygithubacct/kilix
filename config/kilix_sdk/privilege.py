"""Consent-bearing handoff to fixed, root-owned OS helpers."""
from pathlib import Path
import os
import stat
import subprocess
import sys
import tempfile

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


def _read_result(path):
    """A closed terminal without a complete child result is not success."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077):
                return 1
            data = stream.read(32)
        code = int(data)
        if not -255 <= code <= 255 or data != f"{code}\n".encode("ascii"):
            return 1
        return code
    except (OSError, ValueError):
        return 1


def run_helper(helper, *, title):
    _validate(helper)
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _terminal(helper, title)
    if not os.environ.get("DISPLAY"):
        raise RuntimeError("setup requires a terminal or X display for consent")
    # xterm can return zero even when its child cancels or fails. The private
    # result belongs to the consent-bearing child, not the window process.
    with tempfile.TemporaryDirectory(prefix="kilix-setup-") as directory:
        result = Path(directory) / "status"
        code = subprocess.run([
            "/usr/bin/xterm", "-T", title, "-e", sys.executable,
            str(Path(__file__).resolve()), "--result-file", str(result), helper, title,
        ], check=False).returncode
        return code if code else _read_result(result)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    result = None
    if len(args) == 4 and args[0] == "--result-file":
        result, args = args[1], args[2:]
    if len(args) != 2:
        return 2
    code = _terminal(*args)
    if result is not None:
        fd = os.open(result, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(f"{code}\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
