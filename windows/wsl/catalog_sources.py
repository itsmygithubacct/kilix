"""Pinned public source acquisition; no credentials, models or working trees."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.request

HERE = Path(__file__).resolve().parent


class PublicArchive(tarfile.TarFile):
    def chown(self, tarinfo, targetpath, numeric_owner):
        # The embedded Windows engine supplies geteuid() for Unix-only callers.
        # That shim must not make tarfile attempt nonexistent Unix ownership APIs.
        if sys.platform != 'win32':
            super().chown(tarinfo, targetpath, numeric_owner)


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


def _playalong_carrier(destination, revision):
    """Ask Content's single source-carrier authority for the complete closure."""
    try:
        from kilix_content.source_carrier import materialize_source
    except ModuleNotFoundError as error:
        if error.name not in ('kilix_content', 'kilix_content.source_carrier'):
            raise
        if (os.environ.get('KILIX_SOURCE_CARRIER_MANIFEST') or
                os.environ.get('KILIX_SOURCE_CARRIER_SHA256') or
                Path('/usr/share/plebian-os/source-carrier/manifest.json').exists()):
            raise ValueError('Configured source carrier requires the Content source-carrier engine') from error
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    return materialize_source('https://github.com/itsmygithubacct/kilix-playalong.git',
                              revision, destination)


def materialize(prefixes, destination, cache):
    """Extract the exact reviewed closure, including explicit submodule pins."""
    hashes = json.loads((HERE / 'source-archives.json').read_text())
    pins = revisions(prefixes)
    if not all(prefix in pins for prefix in prefixes):
        raise ValueError('Source prefix has no reviewed revision')
    app = 'apps/kilix-playalong'
    carrier_done = False
    if app in pins:
        carrier_done = _playalong_carrier(destination / app, pins[app])
    for prefix, pin in pins.items():
        if carrier_done and (prefix == app or prefix.startswith(app + '/')):
            continue  # The authority materialized the complete verified gitlink closure.
        repo = repository(prefix)
        archive = cache / (repo + '-' + pin + '.tar.gz')
        if repo + '/' + pin not in hashes:
            raise ValueError('Exact source requires a configured verified source carrier: ' + prefix)
        download(f'https://codeload.github.com/itsmygithubacct/{repo}/tar.gz/{pin}',
                 archive, hashes[repo + '/' + pin])
        target = destination / prefix
        target.mkdir(parents=True, exist_ok=True)
        with PublicArchive.open(archive, 'r:gz') as source:
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
