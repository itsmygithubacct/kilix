"""Functional checks on explicitly installed catalog apps and Needle sources.

No network, model downloads, audio devices or native windows are used. Install
the desired apps first, then pass --apps names and/or --needle PATH.
"""
import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'wsl'))
from catalog_store import Catalog, find
from catalog_build import command


def check_needle(path):
    sys.path.insert(0, str(path))
    apps = importlib.import_module('apps')
    checks = 0
    for name, canonical in (('plant grower', 'pleb-plant-grower'), ('tic-tac-toe', 'tictactoe-tui')):
        for verb in ('open', 'play', 'disable', 'enable'):
            request = verb + ' ' + name
            actions = apps.interpret(request, apps.propose(request))
            assert len(actions) == 1 and isinstance(actions[0], apps.Action), (request, actions)
            assert actions[0].args.get('app', actions[0].args.get('game')) == canonical
            checks += 1
        for request in ("don't open " + name, 'my friend said open ' + name,
                        'open ' + name + '. actually cancel that', 'should I open ' + name + '?'):
            actions = apps.interpret(request, apps.propose(request))
            assert not any(isinstance(action, apps.Action) for action in actions), (request, actions)
            checks += 1
    return checks


def check_app(catalog, name, output, env):
    spec = find(name, 'apps')
    argv = catalog.argv(spec)
    source = catalog.generation(spec) / spec['source']
    if spec['recipe'] == 'python':
        example = next((source / 'examples').glob('*.dot'))
        target = output / 'graph.svg'
        subprocess.run(argv + ['draw', str(example), '-o', str(target)], env=env, check=True, timeout=30)
        assert '<svg' in target.read_text()
    elif spec['recipe'] == 'voice':
        target = output / 'speech.wav'
        subprocess.run(argv + ['--speak', 'Hello from Kilix catalog.', '--output', str(target)],
                       env=env, check=True, timeout=30)
        with wave.open(str(target)) as stream:
            assert stream.getnframes() > 1000
    elif spec['recipe'] == 'pdf':
        target = output / 'page.png'
        subprocess.run(argv + ['--render-page', str(source / 'tests/minimal.pdf'), str(target)],
                       env=env, check=True, timeout=30)
        assert target.read_bytes().startswith(b'\x89PNG')
    elif spec['recipe'] == 'chawan':
        html = output / 'browser.html'
        html.write_text('<h1>Catalog browser proof</h1><p>Local document rendered successfully.</p>')
        result = subprocess.check_output(argv + ['--dump', str(html)], env=env, text=True, timeout=30)
        assert 'Catalog browser proof' in result
        (output / 'browser.txt').write_text(result)
    elif spec['recipe'] == 'mpv':
        # The source's protocol test decodes actual Kitty packets from its own
        # bounded PTYs and exercises input; it opens no desktop or sound device.
        subprocess.run([catalog.python, '-B', str(source / 'kilix/test-protocol.py'),
                        command(spec, catalog.generation(spec), probe=True)[0]],
                       env=env, check=True, timeout=180)
    else:
        raise ValueError('No functional check for ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--apps', nargs='*', default=[])
    parser.add_argument('--needle', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(root=args.root)
    results = {}
    with tempfile.TemporaryDirectory(prefix='kilix-catalog-check-') as profile:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('KILIX_', 'GPU_TERMINAL_', 'XDG_'))}
        env.update(HOME=profile, XDG_CONFIG_HOME=profile, XDG_DATA_HOME=profile, XDG_STATE_HOME=profile,
                   KITTYFB_TRANSPORT='inline')
        for name in args.apps:
            check_app(catalog, name, args.output, env)
            results[name] = 'passed'
        if args.needle:
            results['needle-vocabulary-checks'] = check_needle(args.needle.resolve())
    (args.output / 'checks.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results))


if __name__ == '__main__':
    main()
