"""Exec GCC with a private per-invocation diagnostic file (no extra child)."""
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile

from multiplexer_search import request_search


def discovery_arguments(arguments):
    """Change output mode only; preserve every actual search/language option."""
    result = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in ('-o', '-MF', '-MT', '-MQ'):
            if index + 1 == len(arguments):
                raise ValueError('compiler output option has no argument')
            index += 2
        elif token in ('-MD', '-MMD', '-MP', '-c', '-S'):
            index += 1
        elif token.startswith(('-o', '-MF', '-MT', '-MQ')):
            index += 1
        else:
            result.append(token)
            index += 1
    return [*result, '-E', '-o', '/dev/null']


def main():
    compiler, directory, endpoint, *arguments = sys.argv[1:]
    root = Path(directory)
    if root != root.resolve(strict=True) or root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
        raise ValueError('compiler diagnostic directory must be private and canonical')
    fd, name = tempfile.mkstemp(prefix='gcc-', suffix='.log', dir=root)
    try:
        # The existing output-file reader already refuses artifacts over 16MiB.
        # Apply the same ceiling before GCC can write; the owning build command
        # additionally polls a 4MiB aggregate diagnostic bound while GCC runs.
        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        limit = min(value for value in (16 * 1024**2, soft, hard) if value != resource.RLIM_INFINITY)
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
        probe_fd, _probe_name = tempfile.mkstemp(prefix='search-', suffix='.log', dir=root.parent / 'search')
        try:
            # Only output/dependency-writing controls are removed for this
            # preprocessing-only pass. Search/language options remain exact;
            # make recipes run once and no probe output authorizes a binary.
            result = subprocess.run([compiler, *discovery_arguments(arguments)],
                                    stdout=subprocess.DEVNULL, stderr=probe_fd, check=False)
            if os.fstat(probe_fd).st_size > 65536:
                raise ValueError('compiler search discovery exceeds bound')
            os.lseek(probe_fd, 0, os.SEEK_SET)
            trace = os.read(probe_fd, 65537)
            if result.returncode:
                raise ValueError('compiler search discovery failed: ' + trace[-4000:].decode('utf-8', 'replace'))
            request_search(endpoint, Path(name).name, trace)
        finally:
            os.close(probe_fd)
        os.dup2(fd, 2)
    finally:
        os.close(fd)
    # Exec preserves the inherited directory/named-lock guards. There is no
    # second supervisor, background reader, or premature guard release.
    os.execv(compiler, [compiler, *arguments])


if __name__ == '__main__':
    main()
