#!/usr/bin/env python3
"""WSL application recipes and launch adapters; no implicit runtime provisioning.

The catalog owns source pins, archive verification and generation activation.
These recipes consume that source closure and preserve its licenses and assets.
Playalong's optional base Python runtime has a separate explicit setup command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from catalog_runtime import python_argv


EXECUTABLES = {
    'dosbox-kilix': 'src/dosbox-x',
    'kilix-land-agent': 'kilix-land-agent',
    'kilix-multiplexer': 'build/kmx-serve',
    'kilix-playalong': 'build/native/kilix-playalong-native',
    'kilix-nvr': 'build/kilix-nvr',
}
NVR_GATE = 'kilix-nvr-model-required'
NVR_HELPERS = {
    'kilix-look-detect': 'third_party/kilix-object-detect/tools/kilix-look-detect',
    'kilix-listen-classify': 'third_party/kilix-sound-detect/tools/kilix-listen-classify',
    'kilix-sound-fetch-model': 'third_party/kilix-sound-detect/tools/kilix-sound-fetch-model',
}


def source_path(spec, generation):
    if spec['name'] not in EXECUTABLES:
        raise ValueError('Unknown runtime application: ' + spec['name'])
    root = Path(generation).resolve()
    source = (root / spec['source']).resolve()
    if not source.is_relative_to(root) or source == root:
        raise ValueError('Application source must be inside its generation')
    return source


def run(argv, *, cwd=None, env=None, timeout=60, **kwargs):
    return subprocess.run([str(arg) for arg in argv], cwd=cwd, env=env,
                          check=True, timeout=timeout, **kwargs)


def build(spec, generation, cache, python=None):
    """Build native cores only. No global install, models, services or sessions."""
    source = source_path(spec, generation)
    name = spec['name']
    env = dict(os.environ, KITTYFB_TRANSPORT='inline')
    if name == 'dosbox-kilix':
        run(['sh', 'autogen.sh'], cwd=source, env=env, timeout=180)
        run(['./configure', '--enable-sdl2', '--enable-terminal', '--disable-x11',
             '--disable-opengl', '--disable-sdlnet', '--disable-libslirp',
             '--disable-libfluidsynth', '--disable-avcodec'], cwd=source, env=env, timeout=180)
        run(['make', '-j2'], cwd=source, env=env, timeout=1200)
    elif name == 'kilix-playalong':
        run(['make', '-j2', '-f', 'Makefile.native', 'native'], cwd=source, env=env, timeout=300)
    else:
        options = ['ENCODEC=0'] if name == 'kilix-multiplexer' else []
        run(['make', '-j2', *options, 'all'], cwd=source, env=env, timeout=300)
        if name == 'kilix-nvr':
            # Keep the module-owned tools next to the installed command, as its
            # upstream resolver expects. The adapter gates default model use.
            for target, original in NVR_HELPERS.items():
                shutil.copy2(source / original, source / 'build' / target)
            gate = source / 'build' / NVR_GATE
            gate.write_text('#!/bin/sh\n'
                            'echo "Kilix NVR: select an approved local detector/classifier '
                            'with KILIX_OBJECT_DETECTOR or KILIX_SOUND_CLASSIFIER; '
                            'models are not installed automatically." >&2\nexit 78\n', encoding='utf-8')
            gate.chmod(0o755)
    if not (source / EXECUTABLES[name]).is_file() or not os.access(source / EXECUTABLES[name], os.X_OK):
        raise ValueError('Build did not produce ' + EXECUTABLES[name])


def command(spec, generation, python=None, probe=False):
    return python_argv(Path(__file__).resolve(),
                       ['check' if probe else 'run', spec['name'], str(source_path(spec, generation))],
                       python=python)


def _pipeline_location(source, environ=None):
    env = os.environ if environ is None else environ
    data = Path(env.get('XDG_DATA_HOME') or (Path(env.get('HOME', Path.home())) / '.local/share'))
    identity = hashlib.sha256(os.fsencode(source.resolve())).hexdigest()
    # Interpreter links and wheel origin metadata stay outside exported source
    # generations. A relocated/updated generation gets an independent runtime.
    return data / 'kilix-wsl/runtimes/playalong' / identity


def _pipeline_ready(source, environ=None):
    runtime = _pipeline_location(source, environ)
    marker = runtime / '.catalog-pipeline.json'
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    # uv environments are local to a machine/path. Relocated native bundles
    # remain usable; their optional Python runtime must be prepared again.
    return (data == {'schema': 1, 'source': runtime.name, 'providers': False}
            and (runtime / 'bin/python').is_file() and os.access(runtime / 'bin/python', os.X_OK)
            and (source / 'third_party/kilix-state/build/libkilix-state.so').is_file())


def readiness(spec, generation, python=None):
    """Read-only capability report, without opening devices or probing providers."""
    source = source_path(spec, generation)
    name = spec['name']
    required = [EXECUTABLES[name]]
    if name == 'kilix-multiplexer':
        required += ['build/kmx-attach', 'build/kmx-bench']
    if name == 'kilix-nvr':
        required += ['build/' + NVR_GATE, *('build/' + item for item in NVR_HELPERS)]
    missing = [item for item in required if not (source / item).is_file() or not os.access(source / item, os.X_OK)]
    resources = {'dosbox-kilix': ['scripts/run-dosbox-terminal.sh'],
                 'kilix-land-agent': ['kilix-land-agentd', 'kilix_land_agent/resident.py', 'assets/graphics/SHA256SUMS']}
    missing += [item for item in resources.get(name, []) if not (source / item).is_file()]
    if missing:
        return dict(ready=False, status='incomplete', reason='Reinstall; missing runtime files: ' + ', '.join(missing))
    capabilities = {}
    if name == 'dosbox-kilix':
        capabilities = {'dos-runtime': 'available', 'kitty-terminal': 'requires a TTY',
                        'guest-media': 'user supplied', 'audio': 'requires WSL audio output'}
        reason = 'Terminal-enabled DOSBox; supply your own licensed DOS programs. TTY and WSL audio required for interactive play.'
    elif name == 'kilix-land-agent':
        capabilities = {'studio': 'available', 'deterministic-resident': 'available',
                        'plant-handoff': 'unsupported by the WSL/native control bridge',
                        'model-provider': 'requires an explicitly approved SSH/Ollama host',
                        'speech': 'off by default; requires an installed speech provider'}
        reason = 'Studio and deterministic resident available; assets are CC BY-NC-SA 4.0. Plant handoff requires upstream Linux terminal ownership; model/speech providers are separate.'
    elif name == 'kilix-multiplexer':
        capabilities = {'local-sessions': 'available', 'remote-sessions': 'require explicit endpoint/authentication',
                        'encodec': 'not built; PCM available', 'live-native-pane-tap': 'not integrated'}
        reason = 'Use serve, attach or bench. Standalone WSL sessions supported; native pane capture and EnCodec are not provided by this build.'
    elif name == 'kilix-playalong':
        capabilities = {'native-player': 'available', 'offline-render': 'available',
                        'python-base': 'installed' if _pipeline_ready(source) else 'run setup-pipeline',
                        'model-pipeline': 'requires separately provisioned ML providers, approved weights and media rights',
                        'audio-device': 'not probed'}
        reason = 'Native player/render for existing projects. setup-pipeline installs only the locked Python 3.10 base; ML providers/models and media-rights decisions remain explicit.'
    else:
        capabilities = {'recorder-core': 'available', 'ffmpeg': 'available' if shutil.which('ffmpeg') else 'missing',
                        'cameras': 'not inspected or connected', 'object-detection': 'requires an explicitly selected provider',
                        'sound-detection': 'requires an explicitly selected provider', 'service-install': 'not supported by this adapter'}
        reason = 'NVR core ready; recording needs FFmpeg and your camera configuration. Default model loading is blocked; select approved detector/classifier commands explicitly. No service is installed.'
    return dict(ready=True, status='limited', reason=reason, capabilities=capabilities)


def launch_plan(name, source, arguments, python=None, environ=None):
    """Return literal argv/environment; inspecting a plan performs no actions."""
    source = Path(source).resolve()
    binary = source / EXECUTABLES[name]
    arguments = list(arguments)
    env = dict(os.environ if environ is None else environ)
    env['KITTYFB_TRANSPORT'] = 'inline'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    if name == 'dosbox-kilix':
        env['DOSBOX_X_BINARY'] = str(binary)
        return ['sh', str(source / 'scripts/run-dosbox-terminal.sh'), *arguments], env
    if name == 'kilix-land-agent':
        env['KILIX_LAND_AGENT_ASSETS'] = str(source)
        if arguments[:1] == ['resident']:
            arguments.pop(0)
            if arguments[:1] == ['--enable-speech']:
                arguments.pop(0)
            elif '--no-speech' not in arguments:
                arguments.insert(0, '--no-speech')
            return [str(python or sys.executable), str(source / 'kilix-land-agentd'), *arguments], env
        if arguments[:1] == ['studio']:
            arguments.pop(0)
        return [str(binary), *(arguments or ['--test-room'])], env
    if name == 'kilix-multiplexer':
        if not arguments or arguments[0] not in ('serve', 'attach', 'bench'):
            raise ValueError('Use kilix apps launch kilix-multiplexer serve|attach|bench [arguments]. No session starts by default.')
        executable = source / 'build' / ('kmx-' + arguments.pop(0))
        return [str(executable), *arguments], env
    if name == 'kilix-playalong':
        if arguments[:1] == ['pipeline']:
            if not _pipeline_ready(source, env):
                raise ValueError('Run kilix apps launch kilix-playalong setup-pipeline first (uv 0.12.x and Python 3.10 required).')
            env['KILIX_STATE_LIBRARY'] = str(source / 'third_party/kilix-state/build/libkilix-state.so')
            env.pop('PYTHONPATH', None)
            env.pop('PYTHONHOME', None)
            return [str(_pipeline_location(source, env) / 'bin/python'), '-c',
                    'from kilix_playalong.cli import main; raise SystemExit(main())', *(arguments[1:] or ['list'])], env
        if arguments[:1] == ['native']:
            arguments.pop(0)
        return [str(binary), *(arguments or ['--list'])], env
    if name == 'kilix-nvr':
        if arguments[:1] == ['service']:
            raise ValueError('Use foreground run; upstream service installation bypasses the WSL provider gate and is not supported here.')
        env['PATH'] = str(source / 'build') + os.pathsep + env.get('PATH', os.defpath)
        # Upstream YOLO(name) may fetch weights. A camera's detection toggle is
        # not model/license consent. Only an explicit provider command overrides
        # the gate; the adapter never supplies approval or a model download flag.
        for variable, kind, maximum in (('KILIX_OBJECT_DETECTOR', 'object', 13),
                                        ('KILIX_SOUND_CLASSIFIER', 'sound', 15)):
            value = env.get(variable, '')
            if not value.strip():
                env[variable] = NVR_GATE + ' ' + kind
            else:
                # Both pinned upstream parsers silently select their bundled
                # model helper if the 512-byte/argument limit is exceeded.
                # Their command format is space-separated argv, without quoting.
                words = [word for word in value.split(' ') if word]
                if len(os.fsencode(value)) >= 512 or len(words) > maximum:
                    raise ValueError(variable + ' exceeds the upstream provider command limit; use a short wrapper command')
                if any(char in value for char in '\0\t\r\n') or not shutil.which(words[0], path=env['PATH']):
                    raise ValueError(variable + ' must start with an existing executable; use a wrapper for paths containing spaces')
        return [str(binary), *(arguments or ['cameras'])], env
    raise ValueError('Unknown runtime application: ' + name)


def setup_pipeline(source):
    """Explicit, model-free upstream base setup; never installs ML extras."""
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('Playalong base setup requires uv 0.12.x and an installed Python 3.10 interpreter.')
    runtime = _pipeline_location(source)
    marker = runtime / '.catalog-pipeline.json'
    marker.unlink(missing_ok=True)
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(runtime), UV_PYTHON_DOWNLOADS='never',
               KILIX_STATE_LIBRARY=str(source / 'third_party/kilix-state/build/libkilix-state.so'))
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    run(['make', '-C', source / 'third_party/kilix-state', '-j2'], timeout=120)
    run([uv, 'sync', '--locked', '--no-dev', '--no-editable', '--python', '3.10',
         '--no-python-downloads'], cwd=source, env=env, timeout=300)
    run([runtime / 'bin/python', '-c',
         'import kilix_playalong, kilix_state, mido; kilix_state.default_library()'], cwd=source, env=env)
    marker.write_text(json.dumps({'schema': 1, 'source': runtime.name, 'providers': False}) + '\n', encoding='utf-8')


def _smoke_environment(root):
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT') if key in os.environ}
    env.update(HOME=str(root), XDG_CONFIG_HOME=str(root / 'config'), XDG_DATA_HOME=str(root / 'data'),
               XDG_CACHE_HOME=str(root / 'cache'), XDG_STATE_HOME=str(root / 'state'),
               KILIX_NVR_HOME=str(root / 'nvr'), KITTYFB_TRANSPORT='inline',
               SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy', PYTHONDONTWRITEBYTECODE='1')
    return env


def _mux_smoke(source, root, env, python):
    socket = root / 'mux.sock'
    child = 'import time; print("KILIX_MUX_PROBE",flush=True); time.sleep(3)'
    with subprocess.Popen([str(source / 'build/kmx-serve'), '--socket', str(socket), '--', python, '-c', child],
                          env=env, cwd=root, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as server:
        try:
            deadline = time.monotonic() + 5
            while not socket.exists():
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError('Local multiplexer probe did not start')
                time.sleep(0.02)
            result = run([source / 'build/kmx-attach', '--socket', socket, '--dump', '--seconds', '1'],
                         env=env, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
            if b'KILIX_MUX_PROBE' not in result.stdout:
                raise ValueError('Local multiplexer probe did not deliver its child output')
        finally:
            if server.poll() is None:
                server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def smoke(spec, generation, python=None):
    """Bounded real core operations in disposable state; never user endpoints."""
    source = source_path(spec, generation)
    name = spec['name']
    binary = source / EXECUTABLES[name]
    python = str(python or sys.executable)
    with tempfile.TemporaryDirectory(prefix='kilix-app-') as temporary:
        root = Path(temporary)
        env = _smoke_environment(root)
        if name == 'dosbox-kilix':
            run([binary, '-fastlaunch', '-set', 'sdl output=surface', '-set', 'mixer nosound=true',
                 '-c', 'mount c "' + str(root) + '"', '-c', 'c:',
                 '-c', 'echo KILIX_DOS_PROBE>probe.txt', '-c', 'exit'],
                cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            output = next((path for path in root.iterdir() if path.name.lower() == 'probe.txt'), None)
            if output is None or output.read_text().strip() != 'KILIX_DOS_PROBE':
                raise ValueError('DOS guest did not write its probe file')
        elif name == 'kilix-land-agent':
            run([binary, '--selftest'], cwd=root, env=env, stdout=subprocess.DEVNULL)
            run([python, source / 'kilix-land-agentd', '--provider', 'deterministic', '--headless', '--no-speech'],
                cwd=root, env=env, stdout=subprocess.DEVNULL)
        elif name == 'kilix-multiplexer':
            _mux_smoke(source, root, env, python)
        elif name == 'kilix-playalong':
            run([binary, '--list'], cwd=root, env=env, stdout=subprocess.DEVNULL)
        elif name == 'kilix-nvr':
            run([binary, '--selftest'], cwd=root, env=env, stdout=subprocess.DEVNULL)


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'check'))
    parser.add_argument('name', choices=tuple(EXECUTABLES))
    parser.add_argument('source', type=Path)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    options = parser.parse_args(args)
    source = options.source.resolve()
    arguments = options.arguments
    if arguments[:1] == ['--']:
        arguments = arguments[1:]
    if options.action == 'check':
        if arguments:
            raise ValueError('The isolated check takes no application arguments')
        smoke({'name': options.name, 'source': source.name}, source.parent)
        return 0
    if options.name == 'kilix-playalong' and arguments[:1] == ['setup-pipeline']:
        if len(arguments) != 1:
            raise ValueError('setup-pipeline installs only the locked base; it accepts no model/provider flags')
        setup_pipeline(source)
        return 0
    if options.name == 'kilix-multiplexer' and (not arguments or arguments == ['--help']):
        print('kilix apps launch kilix-multiplexer serve|attach|bench [upstream arguments]\n'
              'Use an explicit --socket PATH (or an authenticated endpoint). No session starts by default.\n'
              'This build provides standalone WSL sessions and PCM; native pane taps and EnCodec are not integrated.')
        return 0
    argv, env = launch_plan(options.name, source, arguments)
    os.execvpe(argv[0], argv, env)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('kilix apps: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
