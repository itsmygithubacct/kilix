#!/usr/bin/env python3
"""Create a public, model-free companion bundle from checksum-pinned sources."""
import argparse
import gzip
import json
from pathlib import Path
import os
import shutil
import subprocess
import tarfile
import tempfile

from catalog_sources import HERE, digest, materialize
from needle_catalog import patch_needle


def add_kitten(tree, directory):
    """Add the static Linux kitten (windows/build-kitten.sh) for this engine pin."""
    record = json.loads((directory / 'kitten.json').read_text())
    engine = subprocess.run(['git', '-C', str(HERE), 'rev-parse', 'HEAD:src'], capture_output=True,
                            text=True, check=True).stdout.strip()
    if record.get('engine') != engine:
        raise ValueError('kitten was built from engine ' + str(record.get('engine')) + ', not the pinned ' + engine)
    if record.get('target') != 'linux/amd64' or digest(directory / 'kitten') != record.get('sha256'):
        raise ValueError('kitten does not match its build record')
    target = tree / 'kitten'
    target.mkdir()
    for name, mode in (('kitten', 0o755), ('kitten.json', 0o644), ('LICENSE', 0o644)):
        shutil.copyfile(directory / name, target / name)
        (target / name).chmod(mode)


def provision(output, cache, with_apps=False, kitten=None):
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
        if kitten:
            add_kitten(tree, kitten)
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
    parser.add_argument('--kitten', type=Path, help='Output directory of windows/build-kitten.sh for kilix image')
    args = parser.parse_args()
    provision(args.output, args.cache, args.with_apps, args.kitten)
