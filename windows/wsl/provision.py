#!/usr/bin/env python3
"""Create a public, model-free companion bundle from checksum-pinned sources."""
import argparse
import gzip
import json
from pathlib import Path
import os
import tarfile
import tempfile

from catalog_sources import HERE, digest, materialize
from needle_catalog import patch_needle


def provision(output, cache, with_apps=False):
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    pins = json.loads((HERE / 'source-revisions.json').read_text())
    prefixes = [key for key in pins if '/' not in key]
    if with_apps:
        prefixes += [key for key in pins if key.startswith('apps/') and key.count('/') == 1]
    with tempfile.TemporaryDirectory(prefix='.companion-', dir=output.parent) as directory:
        work = Path(directory)
        tree = work / 'source'
        tree.mkdir()
        selected = materialize(prefixes, tree, cache)
        patch_needle(tree)
        (tree / 'source-revisions.json').write_text(json.dumps(selected, indent=2) + '\n')
        (tree / 'README-bundle.txt').write_text(
            'Public pinned companion source. No models or acceptance receipts.\n'
            'Needle apps.py includes the host reviewed Plant Grower/Tic-tac-toe vocabulary delta.\n')
        archive = work / 'companion.tar.gz'
        def public_metadata(member):
            member.uid = member.gid = member.mtime = 0
            member.uname = member.gname = ''
            member.pax_headers = {}
            return member
        with archive.open('wb') as raw, gzip.GzipFile(fileobj=raw, filename='', mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as bundle:
                for child in sorted(tree.iterdir()):
                    bundle.add(child, arcname=child.name, filter=public_metadata)
        os.replace(archive, output)
    checksum = digest(output)
    output.with_suffix(output.suffix + '.sha256').write_text(checksum + '  ' + output.name + '\n')
    print(checksum, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=Path.home() / '.cache/kilix-wsl/sources')
    parser.add_argument('--with-apps', action='store_true', help='Include implemented app sources; setup still selects what to build')
    args = parser.parse_args()
    provision(args.output, args.cache, args.with_apps)
