"""Adapters for the public applications' existing build and command surfaces."""
import compileall
import os
from pathlib import Path
import subprocess
import sys

from catalog_runtime import python_argv


UTF8PROC = '0075ed7d0adba45682ee6bf7a83b10f8fd110163'


def dependency_pins(spec):
    return {'utf8proc': UTF8PROC} if spec.get('adapter') == 'voicegen' else {}


def build(spec, generation, cache, python=None):
    source = generation / spec['source']
    env = dict(os.environ, KITTYFB_TRANSPORT='inline')

    def run(argv):
        subprocess.run([str(value) for value in argv], cwd=source, env=env, check=True)

    if spec['adapter'] in ('soundgen', 'tmux-browse'):
        if not compileall.compile_dir(source, quiet=1):
            raise ValueError('Python application compilation failed')
    elif spec['adapter'] == 'voicegen':
        # Upstream CMake requires the exact clean git tree, not an unversioned
        # tar extraction. Fetch only its public pinned Unicode dependency.
        unicode_source = source / 'third_party/utf8proc'
        git_env = dict(env, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_CONFIG_NOSYSTEM='1')
        def git(*args):
            subprocess.run(['git', '-c', 'credential.helper=', '-c', 'core.hooksPath=/dev/null',
                            *map(str, args)], check=True, env=git_env)
        git('init', '--template=', unicode_source)
        git('-C', unicode_source, 'fetch', '--depth=1', 'https://github.com/JuliaStrings/utf8proc.git', UTF8PROC)
        git('-C', unicode_source, 'checkout', '--detach', UTF8PROC)
        run(['cmake', '-S', source, '-B', source / 'build', '-DCMAKE_BUILD_TYPE=Release',
             '-DBUILD_TESTING=OFF', '-DCMAKE_INSTALL_LIBDIR=lib',
             '-DKGV_UTF8PROC_SOURCE_DIR=' + str(unicode_source)])
        run(['cmake', '--build', source / 'build', '-j2'])
        run(['cmake', '--install', source / 'build', '--prefix', source / 'runtime'])
    else:
        run(['make', '-j2', *spec.get('make_targets', ['all'])])


def command(spec, generation, python=None, probe=False):
    source = generation / spec['source']
    if probe and spec['adapter'] not in ('soundgen', 'tmux-browse'):
        return [str(source / spec['executable'])]
    return python_argv(Path(__file__).with_name('catalog_launch.py'),
                       [spec['name'], str(source)], python=python or sys.executable)


def smoke(spec, generation, python=None):
    subprocess.run(command(spec, generation, python, probe=True) + spec['probe'],
                   check=True, stdout=subprocess.DEVNULL, timeout=60)
    if spec['adapter'] == 'amp':
        subprocess.run(command(spec, generation, python) + ['--help'],
                       check=True, stdout=subprocess.DEVNULL, timeout=60)


def readiness(spec, generation, python=None):
    adapter = spec.get('adapter')
    capabilities = []
    if adapter == 'amp':
        capabilities = [
            dict(name='terminal-player', ready=True, reason='Shared Music UI and Amp backend; requires working WSL audio.'),
            dict(name='desktop-editor', ready=bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')),
                 reason='--desktop opens the SDL editor in WSLg/X11; it is a Linux window.'),
            dict(name='optional-formats', ready=None,
                 reason='MP3 requires libsndfile 1.1+ (Ubuntu 22.04 ships 1.0); MIDI needs an owner-supplied SoundFont. Use mpv for additional formats.'),
        ]
    elif adapter == 'soundgen':
        capabilities = [
            dict(name='procedural-audio', ready=True, reason='Standard-library providers generate WAV files without models.'),
            dict(name='recording-transforms', ready=None,
                 reason='NumPy/SciPy providers require those packages in the selected interpreter; use soundgen doctor for per-provider availability.'),
        ]
    elif adapter == 'voicegen':
        capabilities = [
            dict(name='lexical-frontend', ready=True, reason='Native UTF-8/NFC lexical trace and strict package verification tools.'),
            dict(name='speech-synthesis', ready=False,
                 reason='Upstream has no release-approved production voice. This build excludes the optional ONNX research backend and downloads no models.'),
        ]
    return dict(status='limited', capabilities=capabilities,
                reason=spec['description']) if capabilities else {}
