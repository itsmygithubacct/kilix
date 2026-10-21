#!/usr/bin/env python3
"""Install the session entry from this Kilix source; no downloads or model calls."""
import argparse
import os
from pathlib import Path
import shlex
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
from agent_skills import Conflict, directory, read_file

MARKER = "# kilix-session managed entry v1\n"


def install(prefix):
    prefix = Path(prefix).expanduser()
    if not prefix.is_absolute() or ".." in prefix.parts:
        raise ValueError("prefix must be absolute without '..'")
    source = ROOT / "kilix-session"
    if not source.is_file() or not (ROOT / "config/kilix_session/cli.py").is_file():
        raise ValueError("Kilix session source closure is incomplete")
    payload = "#!/bin/sh\n" + MARKER + "exec python3 -B " + shlex.quote(str(source)) + ' "$@"\n'
    with directory(prefix / "bin", create=True) as fd:
        try:
            data, _ = read_file(fd, "kilix-session")
        except FileNotFoundError:
            pass
        else:
            if not data.startswith(("#!/bin/sh\n" + MARKER).encode()):
                raise ValueError("existing kilix-session is not a managed entry; left unchanged")
            if data == payload.encode() and os.stat("kilix-session", dir_fd=fd).st_mode & stat.S_IXUSR:
                return str(prefix / "bin/kilix-session")
        temporary = ".kilix-session-" + uuid.uuid4().hex
        entry = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755, dir_fd=fd)
        try:
            with os.fdopen(entry, "w") as stream:
                stream.write(payload)
                stream.flush()
                os.fchmod(stream.fileno(), 0o755)
                os.fsync(stream.fileno())
            os.replace(temporary, "kilix-session", src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
    return str(prefix / "bin/kilix-session")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=os.environ.get("KILIX_SESSION_PREFIX") or str(Path.home() / ".local"))
    args = parser.parse_args()
    try:
        print(install(args.prefix))
        return 0
    except (Conflict, OSError, ValueError) as exc:
        print("kilix-session install: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
