"""Check installed catalog audio tools with owned profiles and dummy output."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'wsl'))
from catalog_store import Catalog, find


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_wav(path):
    with wave.open(str(path)) as audio:
        require(audio.getnframes() > 1000, 'WAV contains too few samples')
        require(any(audio.readframes(audio.getnframes())), 'WAV is silent')


def check(catalog, name, output):
    spec = find(name)
    source = catalog.generation(spec) / spec['source']
    argv = catalog.argv(spec)
    if name == 'kilix-techno':
        project = output / 'project.ktech'
        subprocess.run(argv + ['--new', '--headless', '--save', str(project)], check=True, timeout=30)
        target = output / 'techno.wav'
        subprocess.run(argv + ['--export', str(project), str(target), '--bars', '1'], check=True, timeout=30)
        check_wav(target)
    elif name == 'python-sound-generator':
        target = output / 'soundgen.wav'
        subprocess.run(argv + ['car-engine', str(target), '--duration', '.2', '--seed', '7'], check=True, timeout=30)
        check_wav(target)
    elif name == 'kilix-voicegen':
        result = json.loads(subprocess.check_output(
            argv + ['frontend', '--profile', 'prose', '--text', 'Hello, cafe\u0301.'], text=True, timeout=30))
        require(result['status'] == 'KGV_OK', 'Lexical frontend failed')
        require([word['normalized'] for word in result['words']] == ['Hello', 'café'], 'NFC normalization failed')
        (output / 'voicegen.json').write_text(json.dumps(result, indent=2) + '\n')
    elif name == 'kilix-amp':
        script = source / 'third_party/kilix-tui-utils/tools/music/main.py'
        module_spec = importlib.util.spec_from_file_location('catalog_music_check', script)
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = module
        module_spec.loader.exec_module(module)
        os.environ['KILIX_AMP'] = str(source / 'kilix-amp')
        backend = module.Backend(str(Path(os.environ['HOME']) / 'amp.sock'))
        try:
            require(backend.start(), backend.error)
            require(backend.version == 2, 'Music/Amp did not negotiate protocol 2')
            require(backend.command('volume', level=37).get('ok'), backend.error)
            state = backend.command('state')
            require(state.get('ok') and state['volume'] == 37, 'Amp state did not retain volume')
            require(not backend.capabilities['encodec'], 'Model-free build unexpectedly enabled EnCodec')
            (output / 'amp.json').write_text(json.dumps(state, indent=2) + '\n')
        finally:
            backend.close()
    else:
        raise ValueError('Unknown audio check: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--apps', nargs='+', default=['kilix-amp', 'kilix-techno', 'python-sound-generator', 'kilix-voicegen'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(root=args.root)
    with tempfile.TemporaryDirectory(prefix='kilix-audio-check-') as profile:
        for key in list(os.environ):
            if key.startswith(('KILIX_', 'GPU_TERMINAL_', 'XDG_')):
                os.environ.pop(key)
        os.environ.update(HOME=profile, XDG_CONFIG_HOME=profile, XDG_DATA_HOME=profile,
                          XDG_STATE_HOME=profile, SDL_AUDIODRIVER='dummy', SDL_VIDEODRIVER='dummy')
        for name in args.apps:
            check(catalog, name, args.output)
            print(name + ': PASS', flush=True)


if __name__ == '__main__':
    main()
