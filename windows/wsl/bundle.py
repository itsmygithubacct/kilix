#!/usr/bin/env python3
"""Export pinned companion sources and optionally the owner's installed model."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import os
import tempfile


def write_bundle(args, output):
    pins = json.loads(Path(__file__).with_name('source-revisions.json').read_text())
    roots = {'needle': args.needle, 'games': args.games,
             'super-kilix': args.super_kilix, 'kilix-land': args.kilix_land,
             'pleb-plant-grower': args.plant_grower,
             'apps': getattr(args, 'apps_root', None),
             'kitty-pty-broker': getattr(args, 'broker', None)}
    with tarfile.open(output, 'w:gz') as archive:
        for prefix, revision in pins.items():
            name, _, relative = prefix.partition('/')
            if roots[name] is None:
                continue
            repo = roots[name] / relative
            # Include each game's assets and notices, and the exact dependency
            # revisions recorded below it. No working-tree build products.
            data = subprocess.check_output(['git', '-C', str(repo), 'archive', revision])
            with tarfile.open(fileobj=io.BytesIO(data)) as source:
                for member in source:
                    contents = source.extractfile(member) if member.isfile() else None
                    member.name = prefix + '/' + member.name
                    archive.addfile(member, contents)
        if args.needle_model:
            if not args.needle_model.is_dir() or args.needle_model.is_symlink():
                raise ValueError('Pass a real installed needle2 asset directory')
            for item in sorted(args.needle_model.rglob('*')):
                if item.is_symlink() or not (item.is_file() or item.is_dir()):
                    raise ValueError('Model data must contain only regular files/directories')
                archive.add(item, arcname='content/assets/needle2/' +
                            item.relative_to(args.needle_model).as_posix(), recursive=False)
        for receipt in args.receipt:
            if receipt.is_symlink() or not receipt.is_file():
                raise ValueError('Receipt must be a regular file')
            data = json.loads(receipt.read_text())
            if data.get('license_id') != 'needle2':
                raise ValueError('Only existing needle2 acceptance receipts may be imported')
            archive.add(receipt, arcname='receipts/' + receipt.name, recursive=False)
        archive.add(Path(__file__).with_name('source-revisions.json'), arcname='source-revisions.json')


def bundle(args):
    # A failed export must not replace a usable bundle with a partial archive.
    fd, temporary = tempfile.mkstemp(prefix='.companion-', dir=args.output.parent)
    os.close(fd)
    try:
        write_bundle(args, temporary)
        os.replace(temporary, args.output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    digest = hashlib.sha256()
    with args.output.open('rb') as output:
        for chunk in iter(lambda: output.read(1024 * 1024), b''):
            digest.update(chunk)
    print(digest.hexdigest(), args.output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--needle', type=Path, required=True, help='Initialized kilix-needle checkout')
    parser.add_argument('--games', type=Path, required=True, help='Initialized kilix-games checkout')
    parser.add_argument('--super-kilix', type=Path, required=True, help='Initialized super-kilix checkout')
    parser.add_argument('--kilix-land', type=Path, required=True, help='Initialized kilix-land checkout')
    parser.add_argument('--plant-grower', type=Path, required=True, help='Initialized pleb-plant-grower checkout')
    parser.add_argument('--apps-root', type=Path, help='Optional initialized public app checkouts')
    parser.add_argument('--broker', type=Path, help='Optional initialized kitty-pty-broker checkout')
    parser.add_argument('--needle-model', type=Path, help='Already installed needle2 asset directory')
    parser.add_argument('--receipt', type=Path, action='append', default=[],
                        help='Existing owner acceptance receipt; makes this a personal bundle')
    parser.add_argument('--output', type=Path, required=True)
    bundle(parser.parse_args())
