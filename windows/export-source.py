#!/usr/bin/env python3
"""Export committed Windows build sources, exact revisions and archive hashes."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def export(output):
    host = Path(__file__).resolve().parents[1]
    engine = host / 'src'
    revisions = {name: git(path, 'rev-parse', 'HEAD').decode().strip()
                 for name, path in [('host', host), ('engine', engine)]}
    if git(host, 'rev-parse', 'HEAD:src').decode().strip() != revisions['engine']:
        raise ValueError('The engine checkout does not match the committed gitlink')
    output.mkdir(parents=True, exist_ok=True)
    destination = output / 'kilix-0.2.2-windows-source.tar.gz'
    with tarfile.open(destination, 'w:gz') as archive:
        for repo, prefix in [(host, ''), (engine, 'src/')]:
            with tarfile.open(fileobj=io.BytesIO(git(repo, 'archive', 'HEAD'))) as source:
                for member in source:
                    content = source.extractfile(member) if member.isfile() else None
                    member.name = prefix + member.name
                    archive.addfile(member, content)
    (output / 'source-revisions.json').write_text(json.dumps(revisions, indent=2) + '\n')
    archives = [destination, output / 'kilix-0.2.2-windows-x64-dev.zip']
    lines = []
    for path in archives:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        lines.append(f'{digest.hexdigest()}  {path.name}\n')
    (output / 'SHA256SUMS').write_text(''.join(lines))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    export(parser.parse_args().output)
