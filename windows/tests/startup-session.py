import json
import os
import time
from pathlib import Path
from kitty.fast_data_types import add_timer

root = Path(os.environ['KILIX_TEST_RESULT_DIR'])
profile = os.environ.get('KILIX_TEST_PROFILE', 'powershell')
started = False
phases = set()

def command():
    if profile == 'powershell':
        return b"Write-Output ('KILIX_POLICY_' + (Get-ExecutionPolicy)); kilix --version; echo KILIX_STARTUP_READY\r"
    if profile == 'cmd':
        return b"kilix --version & echo KILIX_STARTUP_READY\r"
    return b"kilix --version; echo KILIX_STARTUP_READY\r"

def on_resize(boss, window, data):
    global started
    if started:
        return
    started = True
    (root / 'engine-pid.txt').write_text(str(os.getpid()))
    beginning = time.monotonic()
    def tick(timer):
        elapsed = time.monotonic() - beginning
        try:
            windows = list(boss.all_windows)
            with (root / 'interactive.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps({'seconds': elapsed, 'windows': [
                    {'id': w.id, 'text': w.as_text().strip(), 'argv': w.child.argv,
                     'cwd': w.child.cwd, 'title': w.title}
                    for w in windows]}) + '\n')
            if elapsed >= 3 and 'input' not in phases:
                phases.add('input')
                window.write_to_child(command())
            if elapsed >= 5 and 'split' not in phases:
                phases.add('split')
                boss.new_window()
            if elapsed >= 9 and 'input2' not in phases:
                phases.add('input2')
                for w in windows:
                    w.write_to_child(command())
            if elapsed >= 18 and 'close' not in phases:
                phases.add('close')
                boss.close_windows_no_confirm(windows)
        except Exception as e:
            with (root / 'interactive.jsonl').open('a') as f:
                f.write(json.dumps({'error': repr(e)}) + '\n')
    add_timer(tick, 1, True)
