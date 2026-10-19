"""Bounded game logic/input/audio checks against an installed WSL generation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

CHECKS = {
    'solitaire-tui': ['--simulate', 'neural', '--seed', '7', '--games', '1'],
    'kilix-lander': ['--input-test'],
    'joustix': ['--rules-test'],
    'kilix-brokeout': ['--input-test'],
    'bashed-earth': ['--selftest', '1337', '1'],
    'kilix-lights': ['--input-test'],
    'kilix-jpak': ['--input-test'],
    'kilix-pong': ['--rules-test'],
    'chess-bash': ['--rules-test'],
    'kilix-rancher': ['--selftest', '1337', '120'],
    'kilix-fishtank': ['--selftest', '1337', '120'],
    'tictactoe-tui': ['--rules-test'],
    'super-kilix': ['--input-test'],
    'pleb-plant-grower': ['--rules-test'],
    'kilix-land': ['--selftest'],
}
SOUND = {'kilix-lander', 'joustix', 'kilix-brokeout', 'kilix-lights', 'kilix-jpak',
         'kilix-pong', 'chess-bash', 'kilix-fishtank', 'super-kilix', 'pleb-plant-grower'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installation', type=Path, default=Path.home() / '.local/share/kilix-wsl/current')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    install = args.installation.resolve()
    catalog = json.loads((install / 'game-catalog.json').read_text())
    assert set(CHECKS) == {g['name'] for g in catalog}, 'Every catalog game needs a check'
    results = []
    with tempfile.TemporaryDirectory(prefix='kilix game checks ') as home:
        env = dict(os.environ, HOME=home, XDG_DATA_HOME=home + '/data',
                   XDG_CONFIG_HOME=home + '/config', XDG_STATE_HOME=home + '/state')
        for name, check in CHECKS.items():
            commands = [check]
            if name in SOUND:
                commands.append(['--sound-test'])
            if name == 'kilix-land':
                commands.append(['--audio-test'])
            for command in commands:
                try:
                    p = subprocess.run([str(install / 'bin' / name), *command],
                                       env=env, cwd=home, capture_output=True, text=True, timeout=40)
                    result = dict(game=name, args=command, status=p.returncode,
                                  stdout=p.stdout[-8000:], stderr=p.stderr[-2000:])
                except subprocess.TimeoutExpired:
                    result = dict(game=name, args=command, status=124, stderr='Check timed out')
                results.append(result)
                print(name, command[0], result['status'], flush=True)
    args.output.write_text(json.dumps(results, indent=2))
    return int(any(r['status'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
