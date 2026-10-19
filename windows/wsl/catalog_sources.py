"""Pinned public source acquisition; no credentials, models or working trees."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def download(url, target, expected):
    if not re.fullmatch('[0-9a-fA-F]{64}', expected):
        raise ValueError('A SHA256 from a trusted publisher is required')
    if target.is_file() and digest(target) == expected.lower():
        return
    if not url.startswith('https://'):
        raise ValueError('Downloads require HTTPS')
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.download-', dir=target.parent)
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'Kilix/0.2.2'})
        with os.fdopen(fd, 'wb') as output, urllib.request.urlopen(request, timeout=120) as response:
            if not response.url.startswith('https://'):
                raise ValueError('Refusing a non-HTTPS redirect')
            shutil.copyfileobj(response, output)
        if digest(temporary) != expected.lower():
            raise ValueError('Download SHA256 mismatch: ' + url)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def repository(prefix):
    return {'games': 'kilix-games', 'needle': 'kilix-needle'}.get(prefix, prefix.rsplit('/', 1)[-1])


def revisions(prefixes):
    pins = json.loads((HERE / 'source-revisions.json').read_text())
    return {path: pin for path, pin in pins.items()
            if any(path == prefix or path.startswith(prefix + '/') for prefix in prefixes)}


def materialize(prefixes, destination, cache):
    """Extract the exact reviewed closure, including explicit submodule pins."""
    hashes = json.loads((HERE / 'source-archives.json').read_text())
    pins = revisions(prefixes)
    if not all(prefix in pins for prefix in prefixes):
        raise ValueError('Source prefix has no reviewed revision')
    for prefix, pin in pins.items():
        repo = repository(prefix)
        archive = cache / (repo + '-' + pin + '.tar.gz')
        download(f'https://codeload.github.com/itsmygithubacct/{repo}/tar.gz/{pin}',
                 archive, hashes[repo + '/' + pin])
        target = destination / prefix
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, 'r:gz') as source:
            members = source.getmembers()
            top = repo + '-' + pin
            for member in members:
                if member.name == top:
                    member.name = '.'
                elif member.name.startswith(top + '/'):
                    member.name = member.name[len(top) + 1:]
                else:
                    raise ValueError('Unexpected source archive root')
                if member.islnk():
                    if not member.linkname.startswith(top + '/'):
                        raise ValueError('Unexpected source archive hard link')
                    member.linkname = member.linkname[len(top) + 1:]
            source.extractall(target, members=members, filter='data')
    return pins
