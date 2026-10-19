"""Native watcher for the isolated WSL game test (never attach to a user session)."""
import json
import os
from pathlib import Path
import shlex
import time
from kitty.fast_data_types import add_timer

started = False


def on_resize(boss, window, data):
    global started
    if started or os.environ.get('KILIX_TEST_ISOLATED') != '1':
        return
    started = True
    output = Path(os.environ['KILIX_TEST_RESULT_DIR'])
    output.mkdir(parents=True, exist_ok=True)
    (output / 'engine-pid.txt').write_text(str(os.getpid()))
    beginning = time.monotonic()
    launched = False

    def tick(timer):
        nonlocal launched
        windows = list(boss.all_windows)
        with (output / 'games-samples.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'windows': [
                {'id': w.id, 'text': w.as_text().strip(),
                 'images': w.screen.grman.image_count, 'uploads': w.screen.grman.gpu_upload_stats}
                for w in windows]}) + '\n')
        if not launched and time.monotonic() - beginning > 3:
            launched = True
            script = os.environ['KILIX_TEST_SCRIPT_WSL']
            window.write_to_child(('python3 ' + shlex.quote(script) + '\r').encode())
        if (output / 'games-results.json').exists() or time.monotonic() - beginning > 600:
            boss.close_windows_no_confirm(windows)

    add_timer(tick, 1, True)
