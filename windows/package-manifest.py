#!/usr/bin/env python3
"""Record the complete portable runtime, without machine-specific paths."""
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
for path in root.rglob('*'):
    if path.is_symlink():
        raise ValueError(f'Portable packages cannot contain symlinks: {path.relative_to(root)}')
files = {
    path.relative_to(root).as_posix(): {
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'size': path.stat().st_size,
    }
    for path in sorted(root.rglob('*'))
    if path.is_file() and path not in (root / 'manifest.json', root / 'manifest-signature.ps1')
}
(root / 'manifest.json').write_text(
    json.dumps({'version': '0.2.2-windows-dev', 'files': files}, indent=2) + '\n',
    encoding='utf-8',
)
