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


def source_closure(host):
    """Export the exact source closure of native code and the shared command adapters."""
    selected = ['src']
    if (host / 'windows/stage-commands.py').exists():
        selected += ['third_party/kilix-content', 'third_party/kilix-transcript-clean']
    result = [(host, '', git(host, 'rev-parse', 'HEAD').decode().strip())]

    def add(path, prefix, expected):
        if not (path / '.git').exists():
            raise ValueError(f'Initialize the pinned source dependency: {prefix}')
        actual = git(path, 'rev-parse', 'HEAD').decode().strip()
        if actual != expected:
            raise ValueError(f'Source checkout does not match committed gitlink: {prefix}')
        result.append((path, prefix + '/', actual))
        for row in git(path, 'ls-tree', '-r', '-z', 'HEAD').split(b'\0'):
            if row.startswith(b'160000 '):
                metadata, name = row.split(b'\t', 1)
                name = name.decode()
                add(path / name, prefix + '/' + name, metadata.split()[2].decode())

    for name in selected:
        add(host / name, name, git(host, 'rev-parse', 'HEAD:' + name).decode().strip())
    for path, prefix, _ in result:
        dirty = git(path, 'status', '--porcelain', '--untracked-files=normal', '--ignore-submodules=none')
        if dirty:
            raise ValueError(f'Source export requires a clean checkout: {prefix or "host"}\n'
                             + dirty.decode(errors='replace'))
    return result


def export(output, host=None):
    host = host or Path(__file__).resolve().parents[1]
    closure = source_closure(host)
    revisions = {prefix.rstrip('/') or 'host': revision for _, prefix, revision in closure}
    revisions['engine'] = revisions['src'] # Preserve the original consumer key.
    output.mkdir(parents=True, exist_ok=True)
    destination = output / 'kilix-0.2.2-windows-source.tar.gz'
    with tarfile.open(destination, 'w:gz') as archive:
        for repo, prefix, revision in closure:
            with tarfile.open(fileobj=io.BytesIO(git(repo, 'archive', revision))) as source:
                for member in source:
                    content = source.extractfile(member) if member.isfile() else None
                    member.name = prefix + member.name
                    archive.addfile(member, content)
    (output / 'source-revisions.json').write_text(json.dumps(revisions, indent=2) + '\n')
    archives = [destination, output / 'kilix-0.2.2-windows-x64-dev.zip']
    # Every shipped metadata/companion asset is covered, including signing status.
    archives += [output / name for name in ('source-revisions.json', 'toolchain-packages.txt',
                 'companion.tar.gz', 'companion.tar.gz.sha256', 'signing.json', 'release.json', 'RELEASE-NOTES.md')
                 if (output / name).is_file()]
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
