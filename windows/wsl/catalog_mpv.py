"""Private, pinned media dependencies compatible with Ubuntu 22.04.

Only the catalog generation is modified. System FFmpeg, libplacebo and Meson
are neither replaced nor required. Source and license files travel with exports.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

from catalog_sources import HERE, download


def dependencies():
    return json.loads((HERE / 'mpv-dependencies.json').read_text())


def extract(name, root, cache):
    spec = dependencies()[name]
    archive = cache / (spec['sha256'] + spec['suffix'])
    download(spec['url'], archive, spec['sha256'])
    target = root / name
    target.mkdir(parents=True)
    if spec['suffix'] == '.whl':
        with zipfile.ZipFile(archive) as package:
            package.extractall(target)
    else:
        with tarfile.open(archive) as package:
            package.extractall(target, filter='data')
        children = list(target.iterdir())
        if len(children) != 1 or not children[0].is_dir():
            raise ValueError('Unexpected dependency source layout: ' + name)
        return children[0]
    return target


def build(source, generation, cache, python):
    root = generation / 'dependencies'
    sources = root / 'src'
    prefix = root / 'prefix'
    scratch = root / 'build'
    scratch.mkdir(parents=True)
    paths = {name: extract(name, sources, cache) for name in dependencies()}
    # libplacebo's generator imports these pure Python source packages. No
    # pip operation, global installation, or mutable dependency resolution.
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(str(path) for path in (
        paths['meson'], paths['jinja2'] / 'src', paths['markupsafe'] / 'src'))
    env['PKG_CONFIG_PATH'] = str(prefix / 'lib/pkgconfig') + os.pathsep + env.get('PKG_CONFIG_PATH', '')
    meson = [python, '-m', 'mesonbuild.mesonmain']

    def run(argv, **kwargs):
        subprocess.run([str(value) for value in argv], env=env, check=True, **kwargs)

    ffmpeg = scratch / 'ffmpeg'
    ffmpeg.mkdir()
    run([paths['ffmpeg'] / 'configure', '--prefix=' + str(prefix), '--libdir=' + str(prefix / 'lib'),
         '--disable-autodetect', '--disable-programs', '--disable-doc', '--disable-debug',
         '--disable-static', '--enable-shared', '--enable-pic', '--disable-avdevice',
         '--disable-postproc', '--disable-encoders', '--disable-muxers',
         '--enable-gnutls', '--enable-zlib'], cwd=ffmpeg)
    run(['make', '-C', ffmpeg, '-j2'])
    run(['make', '-C', ffmpeg, 'install'])

    shutil.copytree(paths['fast_float'] / 'include',
                    paths['libplacebo'] / '3rdparty/fast_float/include')
    # Even the disabled Vulkan backend compiles public-header stubs. Use the
    # exact headers selected by libplacebo rather than Ubuntu's older SDK.
    shutil.copytree(paths['vulkan-headers'] / 'include',
                    paths['libplacebo'] / '3rdparty/Vulkan-Headers/include')
    placebo = scratch / 'libplacebo'
    run(meson + ['setup', placebo, paths['libplacebo'], '--prefix=' + str(prefix), '--libdir=lib',
                 '--buildtype=release', '--wrap-mode=nodownload', '-Ddefault_library=shared',
                 '-Dauto_features=disabled', '-Ddemos=false'])
    run(['ninja', '-C', placebo, '-j2'])
    run(meson + ['install', '-C', placebo, '--no-rebuild'])

    # Internal dependencies must resolve beside each other, including after a
    # binary bundle is imported into a different catalog root.
    for library in (prefix / 'lib').glob('*.so*'):
        if not library.is_symlink():
            run(['patchelf', '--set-rpath', '$ORIGIN', library])
    run(meson + ['setup', source / 'build', source, '--buildtype=release', '--wrap-mode=nodownload',
                 '-Dauto_features=disabled', '-Dzlib=enabled', '-Dgl=disabled',
                 '-Dlibmpv=false', '-Dlua=luajit', '-Dpulse=enabled'])
    run(['ninja', '-C', source / 'build', '-j2'])
    run(['patchelf', '--set-rpath', '$ORIGIN/../../../dependencies/prefix/lib', source / 'build/mpv'])
    # Keep the exact upstream source/notices and private runtime libraries;
    # discard intermediate objects. Retained sources can rebuild the export.
    shutil.rmtree(scratch)
    (root / 'manifest.json').write_text(json.dumps(dependencies(), indent=2) + '\n')
