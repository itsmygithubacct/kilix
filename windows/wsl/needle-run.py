#!/usr/bin/env python3
"""Launch Needle with Linux UAPI constants omitted by older build headers."""
import fcntl
from pathlib import Path
import runpy
import sys

if sys.platform != 'linux':
    raise SystemExit('Needle companion requires Linux under WSL')

# Linux include/uapi/linux/fcntl.h. Portable Python builds can use headers
# older than their runtime kernel. Real fcntl calls still enforce every seal;
# unsupported kernels fail normally rather than running unsealed images.
for name, value in {'F_ADD_SEALS': 1033, 'F_GET_SEALS': 1034,
                    'F_SEAL_SEAL': 1, 'F_SEAL_SHRINK': 2,
                    'F_SEAL_GROW': 4, 'F_SEAL_WRITE': 8}.items():
    if not hasattr(fcntl, name):
        setattr(fcntl, name, value)

entry = Path(sys.argv.pop(1))
sys.path.insert(0, str(entry.parent))
sys.argv[0] = str(entry)
runpy.run_path(str(entry), run_name='__main__')
