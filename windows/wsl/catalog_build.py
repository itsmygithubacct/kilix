"""Small, explicit build recipes for the public companion catalog."""
import compileall
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

from catalog_sources import download
from catalog_runtime import python_argv


def run(argv, **kwargs):
    subprocess.run([str(item) for item in argv], check=True, **kwargs)


def missing_build_dependencies(spec):
    missing = [name for name in spec.get('build_commands', []) if not shutil.which(name)]
    if shutil.which('pkg-config'):
        for module in spec.get('build_modules', []):
            try:
                missing_module = subprocess.call(['pkg-config', '--exists', module],
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                 timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                missing_module = True
            if missing_module:
                missing.append(module)
    elif spec.get('build_modules'):
        missing.extend(spec['build_modules'])
    return missing


def require_build_dependencies(spec):
    missing = missing_build_dependencies(spec)
    if missing:
        raise ValueError('Missing or too old build dependencies: ' + ', '.join(missing) +
                         '. Packages: ' + ' '.join(spec.get('packages', [])) + '. ' + spec.get('build_note', ''))


def build(spec, generation, cache, python=None):
    python = python or str(Path(sys.executable).resolve())
    source = generation / spec['source']
    recipe = spec['recipe']
    env = dict(os.environ, KITTYFB_TRANSPORT='inline')
    if recipe == 'game':
        # Same build/launcher as full companion setup, limited to one game.
        from setup import build_games
        build_games(generation, python, [spec['name']])
        return
    if recipe in ('python', 'voice'):
        if not compileall.compile_dir(source, quiet=1):
            raise ValueError('Python application compilation failed')
    elif recipe == 'pdf':
        run(['make', '-C', source, '-j2', 'native'], env=env)
    elif recipe == 'chawan':
        # These are nim-lang.org's published 2.2.10 toolchain checksums,
        # also pinned by the shared Kilix Chawan installer.
        toolchains = {
            'x86_64': ('linux_x64', '0a3a38752e97e9d44aa479b3a7b37336dfe0176daf22ee5b5218ad0991ecd211'),
            'aarch64': ('linux_arm64', 'cd86a6e2bcbf029c4870aa51df5c0169345dbf9959889112fd15d403c13ae33a'),
        }
        if platform.machine().lower() not in toolchains:
            raise ValueError('The browser toolchain supports Linux x86_64 and aarch64')
        arch, checksum = toolchains[platform.machine().lower()]
        archive = cache / ('nim-2.2.10-' + arch + '.tar.xz')
        download('https://nim-lang.org/download/' + archive.name, archive, checksum)
        toolchain = generation / 'toolchain'
        toolchain.mkdir()
        with tarfile.open(archive) as package:
            package.extractall(toolchain, filter='data')
        nim = toolchain / 'nim-2.2.10/bin/nim'
        run(['make', '-C', source, '-j2', 'NIM=' + str(nim), 'CHA_SFTP=0'], env=env)
        # The runtime does not need the compiler. Keep source and notices.
        shutil.rmtree(toolchain)
    elif recipe == 'mpv':
        run(['meson', 'setup', source / 'build', source, '--buildtype=release',
             '-Dauto_features=disabled', '-Dzlib=enabled', '-Dgl=disabled',
             '-Dlibmpv=false', '-Dlua=luajit', '-Dpulse=enabled'], env=env)
        run(['ninja', '-C', source / 'build', '-j2'], env=env)
    else:
        raise ValueError('No build recipe for ' + spec['name'])
    binary = source / spec['executable']
    if not binary.is_file():
        raise ValueError('Build did not produce ' + str(binary))


def command(spec, generation, python=None, probe=False):
    python = python or str(Path(sys.executable).resolve())
    if spec['recipe'] == 'game':
        return [str(generation / 'bin' / spec['name'])]
    binary = generation / spec['source'] / spec['executable']
    argv = python_argv(binary, python=python) if spec['recipe'] in ('python', 'voice') else [str(binary)]
    return argv if probe else argv + spec.get('launch_args', [])


def smoke(spec, generation, python=None):
    env = dict(os.environ, KITTYFB_TRANSPORT='inline')
    run(command(spec, generation, python=python, probe=True) + spec.get('probe', ['--version']),
        env=env, stdout=subprocess.DEVNULL, timeout=60, cwd=generation)


def manifest(spec, generation, runtime, pins):
    data = {'schema': 1, 'name': spec['name'], 'runtime': runtime,
            'architecture': platform.machine().lower(), 'revisions': pins}
    (generation / 'catalog-install.json').write_text(json.dumps(data, indent=2) + '\n')
    return data
