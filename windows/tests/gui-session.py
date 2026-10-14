import json
import os
import time
from pathlib import Path
from kitty.fast_data_types import add_timer

root = Path(os.environ['KILIX_TEST_RESULT_DIR'])
started = False
start = time.monotonic()
phases = set()

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
        except Exception as e:
            write({'error': repr(e)})
    add_timer(sample, 1, True)

def on_close(boss, window, data):
    write({'closed': window.id, 'text': window.as_text().strip()})
