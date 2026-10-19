"""Hash-pinned private CMake for source recipes on older WSL distributions.

The wheels are the Kitware-maintained cmake distribution on PyPI. They supply
the actual CMake executable and data; no pip or global installation is used.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import platform
import shutil
import subprocess
import zipfile

from catalog_sources import download


def pinned():
    data = json.loads(Path(__file__).with_name('cmake-toolchains.json').read_text())
    spec = data['linux'].get(platform.machine().lower())
    if platform.system() != 'Linux' or not spec:
        raise ValueError('The private CMake toolchain supports Linux x86_64 and aarch64')
    return dict(spec, version=data['version'])


@contextmanager
def toolchain(generation, cache):
    spec = pinned()
    archive = cache / (spec['sha256'] + '.whl')
    download(spec['url'], archive, spec['sha256'])
    root = generation / '.cmake-build-tool'
    root.mkdir()
    try:
        with zipfile.ZipFile(archive) as package:
            package.extractall(root)
        binary = root / 'cmake/data/bin/cmake'
        for name in ('cmake', 'cpack', 'ctest'):
            (binary.parent / name).chmod(0o755)
        subprocess.run([str(binary), '--version'], check=True, timeout=10)
        notices = generation / 'dependencies/cmake-notices'
        shutil.copytree(root / 'cmake/data/doc', notices / 'upstream')
        shutil.copytree(root / ('cmake-' + spec['version'] + '.dist-info/licenses'), notices / 'distribution')
        yield binary
    finally:
        # Installed applications do not need the build tool or its Python
        # package. Keep upstream notices and the locked download in the cache.
        shutil.rmtree(root)
