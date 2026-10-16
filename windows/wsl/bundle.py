#!/usr/bin/env python3
"""Export pinned companion sources and optionally the owner's installed model."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile


def bundle(args):
    pins = json.loads(Path(__file__).with_name('source-revisions.json').read_text())
    roots = {'needle': args.needle, 'games': args.games}
    with tarfile.open(args.output, 'w:gz') as archive:
        for prefix, revision in pins.items():
            name, _, relative = prefix.partition('/')
            repo = roots[name] / relative
            data = subprocess.check_output(['git', '-C', str(repo), 'archive', revision])
            with tarfile.open(fileobj=io.BytesIO(data)) as source:
                for member in source:
                    contents = source.extractfile(member) if member.isfile() else None
                    member.name = prefix + '/' + member.name
                    archive.addfile(member, contents)
        if args.needle_model:
            for item in sorted(args.needle_model.rglob('*')):
                if item.is_symlink() or not (item.is_file() or item.is_dir()):
                    raise ValueError('Model data must contain only regular files/directories')
                archive.add(item, arcname='content/assets/needle2/' +
                            item.relative_to(args.needle_model).as_posix(), recursive=False)
        for receipt in args.receipt:
            data = json.loads(receipt.read_text())
            if data.get('license_id') != 'needle2':
                raise ValueError('Only existing needle2 acceptance receipts may be imported')
            archive.add(receipt, arcname='receipts/' + receipt.name, recursive=False)
        archive.add(Path(__file__).with_name('source-revisions.json'), arcname='source-revisions.json')
    digest = hashlib.sha256()
    with args.output.open('rb') as output:
        for chunk in iter(lambda: output.read(1024 * 1024), b''):
            digest.update(chunk)
    print(digest.hexdigest(), args.output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--needle', type=Path, required=True, help='Initialized kilix-needle checkout')
    parser.add_argument('--games', type=Path, required=True, help='Initialized kilix-games checkout')
    parser.add_argument('--needle-model', type=Path, help='Already installed needle2 asset directory')
    parser.add_argument('--receipt', type=Path, action='append', default=[],
                        help='Existing owner acceptance receipt; makes this a personal bundle')
    parser.add_argument('--output', type=Path, required=True)
    bundle(parser.parse_args())
