"""Run in an isolated WSL test window with games-session.py observing its GPU."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


if os.environ.get('KILIX_TEST_ISOLATED') != '1':
    raise SystemExit('Use an isolated test instance with KILIX_TEST_ISOLATED=1')
root = Path(os.environ['KILIX_TEST_APP_WSL'])
output = Path(os.environ['KILIX_TEST_RESULT_WSL'])
cli = str(root / 'wsl-bin/kilix')
install = (Path.home() / '.local/share/kilix-wsl/current').resolve()
catalog = json.loads((install / 'game-catalog.json').read_text())
report = {'games': [], 'errors': []}


def run(args):
    result = subprocess.run([cli, *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(str(args) + ': ' + result.stderr[-1500:])
    return result.stdout


def ids():
    # The independent native observer avoids starting a Windows executable on
    # every exit poll and records what the renderer actually has open.
    for line in reversed((output / 'games-samples.jsonl').read_text().splitlines()):
        try:
            return {w['id'] for w in json.loads(line)['windows']}
        except json.JSONDecodeError:
            continue
    return set()


def samples(pane):
    lines = (output / 'games-samples.jsonl').read_text().splitlines()
    result = []
    for line in lines:
        try:
            result.extend(w for w in json.loads(line).get('windows', []) if w['id'] == pane)
        except json.JSONDecodeError:
            pass  # The native watcher may be writing the last line.
    return result


try:
    expected = sorted(g['name'] for g in catalog)
    assert json.loads(run(['games', 'list', '--json'])) == expected
    app_catalog = json.loads((root / 'wsl/app-catalog.json').read_text())
    apps = json.loads(run(['apps', '--json']))
    assert sorted(item['name'] for item in apps) == sorted(item['name'] for item in app_catalog)
    assert all(item['category'] == 'app' and 'status' in item and 'ready' in item for item in apps)
    for game in catalog:
        name = game['name']
        item = {'name': name}
        pane = None
        try:
            item['version'] = run(['games', 'play', name, '--version']).strip()
            # Isolate saves/configuration from the owner's normal game profile.
            with tempfile.TemporaryDirectory(prefix='kilix game test ') as home:
                pane = int(run(['@', 'launch', '--type=tab', '--tab-title=' + name,
                                '--env=HOME=' + home, '--env=XDG_DATA_HOME=' + home + '/data',
                                '--env=XDG_STATE_HOME=' + home + '/state',
                                '--env=XDG_CONFIG_HOME=' + home + '/config',
                                '--', str(install / 'bin' / name)]))
                item['pane'] = pane
                time.sleep(3)
                assert pane in ids(), 'Game exited during startup'
                text_game = name in ('solitaire-tui', 'tictactoe-tui')
                before = run(['@', 'get-text', '--match=id:' + str(pane)]) if text_game else ''
                run(['@', 'send-key', '--match=id:' + str(pane), 'enter', 'right'])
                time.sleep(3)
                assert pane in ids(), 'Game exited after keyboard input'
                observed = samples(pane)
                uploads = max((w['uploads']['full'] for w in observed), default=0)
                item['gpu_uploads'] = uploads
                if text_game:
                    after = run(['@', 'get-text', '--match=id:' + str(pane)])
                    assert before.strip() and after != before, 'Text game did not respond to input'
                    item['text_input_changed'] = True
                else:
                    assert uploads > 0, 'No native GPU uploads observed'
                pulse = subprocess.run(['pactl', 'list', 'short', 'sink-inputs'],
                                       capture_output=True, text=True, timeout=5)
                item['pulse_stream'] = bool(pulse.stdout.strip())
                exit_key = {'kilix-lights': 'q', 'kilix-fishtank': 'q'}.get(name, 'ctrl+c')
                if name == 'pleb-plant-grower':
                    # This game's current upstream input map has no quit key;
                    # its host handles terminal closure and termination signals.
                    run(['@', 'close-window', '--match=id:' + str(pane)])
                    item['exit_method'] = 'pane close'
                else:
                    run(['@', 'send-key', '--match=id:' + str(pane), exit_key])
                    item['exit_method'] = exit_key
                for _ in range(15):
                    if pane not in ids():
                        break
                    time.sleep(.2)
                assert pane not in ids(), 'Game did not exit via ' + item['exit_method']
                pane = None
                item['exit_verified'] = True
            item['passed'] = True
        except Exception as error:
            item['error'] = str(error)
            report['errors'].append(name + ': ' + str(error))
        finally:
            if pane is not None:
                try:
                    run(['@', 'close-window', '--match=id:' + str(pane)])
                except Exception:
                    pass
            report['games'].append(item)
            (output / 'games-progress.json').write_text(json.dumps(report, indent=2))
except Exception as error:
    report['errors'].append(str(error))
finally:
    (output / 'games-results.json').write_text(json.dumps(report, indent=2))
print('GAMES_TEST_' + ('FAIL' if report['errors'] else 'PASS'))
raise SystemExit(bool(report['errors']))
