#!/usr/bin/env python3
"""Build a companion generation, then atomically activate it."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile


def write_game_launchers(generation, python, catalog):
    bindir = generation / 'bin'
    bindir.mkdir(exist_ok=True)
    interpreter = bindir / 'python3'
    if interpreter.is_symlink():
        interpreter.unlink()
    if not interpreter.exists():
        interpreter.symlink_to(python)
    for game in catalog:
        wrapper = bindir / game['name']
        wrapper.write_text('#!/bin/sh\nset -eu\n'
                           'export KITTYFB_TRANSPORT=inline\n'
                           'bindir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                           'export PATH="$bindir:$PATH"\n'
                           'cd -- "$bindir/../"' + shlex.quote(game['source']) + '\n'
                           'exec ' + shlex.quote('./' + game['executable']) + ' "$@"\n')
        wrapper.chmod(0o755)
        for alias in game['aliases']:
            link = bindir / alias
            if link.is_symlink():
                link.unlink()
            link.symlink_to(game['name'])


def build_games(generation, python, names=None):
    catalog = json.loads(Path(__file__).with_name('game-catalog.json').read_text())
    if names is not None:
        unknown = set(names) - {game['name'] for game in catalog}
        if unknown:
            raise ValueError('Unknown games: ' + ', '.join(sorted(unknown)))
        catalog = [game for game in catalog if game['name'] in names]
    bindir = generation / 'bin'
    bindir.mkdir(exist_ok=True)
    interpreter = bindir / 'python3'
    if not interpreter.exists():
        interpreter.symlink_to(python)
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ.get('PATH', ''))
    for game in catalog:
        source = generation / game['source']
        print('Building ' + game['name'], flush=True)
        subprocess.run(['make', '-C', str(source), '-j2', 'all', 'PYTHON=' + python],
                       env=env, check=True)
        binary = source / game['executable']
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError('Game build did not produce ' + game['name'])
    write_game_launchers(generation, python, catalog)
    for game in catalog:
        subprocess.run([str(bindir / game['name']), '--version'], env=env, stdout=subprocess.DEVNULL,
                       timeout=30, check=True)
    (generation / 'game-catalog.json').write_text(json.dumps(catalog, indent=2) + '\n')


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def check_kitten(generation):
    """A bundled static Linux kitten (kilix image) must match its build record and run."""
    binary = generation / 'kitten/kitten'
    if not binary.exists() and not binary.is_symlink():
        return
    record = json.loads((generation / 'kitten/kitten.json').read_text())
    if binary.is_symlink() or not binary.is_file() or digest(binary) != record.get('sha256'):
        raise ValueError('The bundled kitten does not match its build record')
    binary.chmod(0o755)
    # The kitten is linux/amd64; on another architecture kilix image says so.
    if platform.machine() == 'x86_64':
        subprocess.run([str(binary), '--version'], stdout=subprocess.DEVNULL, timeout=30, check=True)


def import_plan(source, destination):
    """Check all existing data before copying anything; never overwrite it."""
    plan = []
    if source.exists():
        for item in source.rglob('*'):
            if item.is_symlink() or not (item.is_dir() or item.is_file()):
                raise ValueError(f'Import requires regular files: {item.name}')
            if item.is_dir():
                continue
            target = destination / item.relative_to(source)
            for parent in target.parents:
                if parent == destination:
                    break
                if parent.is_symlink():
                    raise ValueError(f'Import directory must not be a symlink: {parent}')
            if target.exists() or target.is_symlink():
                if target.is_symlink() or not target.is_file() or digest(item) != digest(target):
                    raise ValueError(f'Existing data differs; retained unchanged: {target}')
            else:
                plan.append((item, target))
    return plan


def install(archive, expected, root=None):
    archive = Path(archive).resolve()
    if digest(archive) != expected.lower():
        raise ValueError('Companion bundle hash mismatch')
    if sys.version_info < (3, 12):
        raise ValueError('Run setup.sh to use the managed Python 3.12 interpreter')
    root = root or Path.home() / '.local/share/kilix-wsl'
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.setup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sources = root / 'sources'
        sources.mkdir(exist_ok=True)
        generation = Path(tempfile.mkdtemp(prefix='generation-', dir=sources))
        activated = False
        link = root / '.current-next'
        try:
            with tarfile.open(archive, 'r:gz') as bundle:
                bundle.extractall(generation, filter='data')
            check_kitten(generation)
            # Do not point a new generation back through the mutable current link.
            python = str(Path(sys.executable).resolve())
            build_games(generation, python)
            if (generation / 'needle/apps.py').is_file():
                from needle_catalog import patch_needle
                patch_needle(generation)
            if (generation / 'kitty-pty-broker').is_dir():
                from recovery import build_backend
                build_backend(generation)
            subprocess.run([python, str(generation / 'needle/needle_cli.py'), '--help'],
                           stdout=subprocess.DEVNULL, check=True)

            # A source-only upgrade must retain the owner's installed model.
            content = (root / 'content').resolve()
            receipts = Path.home() / '.local/gpu_terminal/license-receipts'
            for item in (generation / 'receipts').glob('*.json'):
                if item.is_symlink() or json.loads(item.read_text()).get('license_id') != 'needle2':
                    raise ValueError('Only existing needle2 acceptance receipts may be imported')
            plan = import_plan(generation / 'content', content)
            plan += import_plan(generation / 'receipts', receipts)
            content.mkdir(parents=True, exist_ok=True)
            receipts.mkdir(parents=True, exist_ok=True, mode=0o700)
            for source, target in plan:
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix='.import-', dir=target.parent)
                try:
                    with os.fdopen(fd, 'wb') as output, source.open('rb') as input_file:
                        shutil.copyfileobj(input_file, output)
                    os.chmod(temporary, 0o600 if target.is_relative_to(receipts) else source.stat().st_mode & 0o777)
                    # Publish complete bytes only and refuse concurrent replacement.
                    os.link(temporary, target)
                finally:
                    os.unlink(temporary)
            if link.is_symlink():
                link.unlink()
            link.symlink_to(generation)
            os.replace(link, root / 'current')
            activated = True
        finally:
            if link.is_symlink():
                link.unlink()
            if not activated:
                shutil.rmtree(generation)
    print('WSL companion activated. Existing models and previous generations retained.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('sha256')
    args = parser.parse_args()
    try:
        install(args.archive, args.sha256)
    except (ValueError, OSError, tarfile.TarError, subprocess.SubprocessError) as error:
        parser.exit(1, f'WSL setup: {error}\n')
