"""Exec GCC with a private per-invocation diagnostic file (no extra child)."""
import os
from pathlib import Path
import resource
import sys
import tempfile


def main():
    compiler, directory, *arguments = sys.argv[1:]
    root = Path(directory)
    if root != root.resolve(strict=True) or root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise ValueError('compiler diagnostic directory must be private and canonical')
    fd, _name = tempfile.mkstemp(prefix='gcc-', suffix='.log', dir=root)
    try:
        # The existing output-file reader already refuses artifacts over 16MiB.
        # Apply the same ceiling before GCC can write; the owning build command
        # additionally polls a 4MiB aggregate diagnostic bound while GCC runs.
        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        limit = min(value for value in (16 * 1024**2, soft, hard) if value != resource.RLIM_INFINITY)
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
        os.dup2(fd, 2)
    finally:
        os.close(fd)
    # Exec preserves the inherited directory/named-lock guards. There is no
    # second supervisor, background reader, or premature guard release.
    os.execv(compiler, [compiler, *arguments])


if __name__ == '__main__':
    main()
