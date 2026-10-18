import json
import importlib.util
import os
import time
from pathlib import Path
from kitty.fast_data_types import add_timer, is_os_window_fullscreen, toggle_fullscreen

spec = importlib.util.spec_from_file_location('win32_window', Path(__file__).with_name('win32_window.py'))
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)

root = Path(os.environ['KILIX_TEST_RESULT_DIR'])
started = False
start = time.monotonic()
phases = set()
saved = {}


def check_fullscreen(window):
    state = native.snapshot()
    assert is_os_window_fullscreen(window.os_window_id), state
    assert state['rect'] == state['monitor'], state
    assert state['client'][2:] == [state['rect'][2] - state['rect'][0], state['rect'][3] - state['rect'][1]], state
    assert state['visible_captions'] == 0, state
    write({'fullscreen': state})

def write(event):
    with (root / 'desktop-ui-results.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(event, ensure_ascii=False) + '\n')

def on_resize(boss, window, data):
    global started, start
    if started:
        return
    started = True
    start = time.monotonic()
    def sample(timer):
        elapsed = time.monotonic() - start
        try:
            write({'elapsed': round(elapsed, 2), 'tabs': len(tuple(boss.all_tabs)), 'windows': [
                {'id': w.id, 'title': w.title, 'rows': w.screen.lines, 'cols': w.screen.columns,
                 'text': w.as_text().strip(), 'images': w.screen.grman.image_count,
                 'uploads': w.screen.grman.gpu_upload_stats}
                for w in boss.all_windows]})
            if elapsed > 2 and 'split' not in phases and os.environ.get('KILIX_TEST_WSL_DISTRO'):
                phases.add('split')
                boss.new_window('wsl.exe', '--distribution', os.environ['KILIX_TEST_WSL_DISTRO'], '--exec', '/bin/sh', '-c',
                                'printf "KILIX_WSL_READY\\n\\033_Ga=T,f=24,s=1,v=1,i=43;AP8A\\033\\\\"; sleep 7')
            if elapsed > 5 and 'tab' not in phases:
                phases.add('tab')
                boss.new_tab('cmd.exe', '/d', '/c', 'echo KILIX_CMD_READY & ping -n 6 127.0.0.1 >nul')
            if elapsed > 8 and 'fullscreen' not in phases:
                phases.add('fullscreen')
                saved['normal'] = native.snapshot()
                toggle_fullscreen(window.os_window_id)
            if elapsed > 10 and 'restore' not in phases:
                phases.add('restore')
                check_fullscreen(window)
                toggle_fullscreen(window.os_window_id)
            if elapsed > 12 and 'maximize' not in phases:
                phases.add('maximize')
                state = native.snapshot()
                assert not is_os_window_fullscreen(window.os_window_id), state
                assert state['rect'] == saved['normal']['rect'], (saved['normal'], state)
                assert state['visible_captions'] == saved['normal']['visible_captions'], state
                native.maximize()
            if elapsed > 14 and 'max-fullscreen' not in phases:
                phases.add('max-fullscreen')
                saved['maximized'] = native.snapshot()
                assert saved['maximized']['maximized'], saved['maximized']
                toggle_fullscreen(window.os_window_id)
            if elapsed > 16 and 'max-restore' not in phases:
                phases.add('max-restore')
                check_fullscreen(window)
                toggle_fullscreen(window.os_window_id)
            if elapsed > 17 and 'fullscreen-pass' not in phases:
                phases.add('fullscreen-pass')
                state = native.snapshot()
                assert not is_os_window_fullscreen(window.os_window_id), state
                assert state['maximized'], state
                assert state['rect'] == saved['maximized']['rect'], (saved['maximized'], state)
                write({'fullscreen_round_trips': 'passed'})
        except Exception as e:
            write({'error': repr(e)})
    add_timer(sample, 1, True)

def on_close(boss, window, data):
    write({'closed': window.id, 'text': window.as_text().strip()})
