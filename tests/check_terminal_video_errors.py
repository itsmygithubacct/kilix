#!/usr/bin/env python3
"""Check persistent video errors in a private Xvfb terminal.

Requires Pillow, Xvfb, xdotool, ImageMagick and the Lua-enabled named player.
The resized window receives pointer motion, matching normal interactive use.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from PIL import Image, ImageChops


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('player', type=Path)
    parser.add_argument('terminal', type=Path)
    parser.add_argument('clip', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--transport', choices=('inline', 'shm'), default='inline')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    host = Path(__file__).resolve().parents[1]
    player = args.player.resolve()
    engine = args.terminal.resolve()
    clip = args.clip.resolve()
    transport = args.transport
    private = root / transport
    private.mkdir(exist_ok=True)
    for name in ('home', 'config', 'cache', 'runtime', 'bin'):
        (private / name).mkdir(mode=0o700, exist_ok=True)
    ipc = private / 'mpv.sock'
    ipc.unlink(missing_ok=True)
    options = ['--input-ipc-server=' + str(ipc), '--pause', '--keep-open=yes',
               '--script-opts=osc-visibility=always', '--osd-level=0',
               '--log-file=' + str(private / 'player.log')]
    shim = private / 'bin/kilix-mpv'
    shim.write_text('#!/usr/bin/python3\nimport os, sys\n'
                    f'player = {str(player)!r}\noptions = {options!r}\n'
                    'os.execv(player, [player] + sys.argv[1:-2] + options + sys.argv[-2:])\n')
    shim.chmod(0o700)
    launch = [str(host / 'kilix'), 'video', '--mute', '--transport', transport, str(clip)]
    runner = private / 'run.py'
    runner.write_text('import subprocess, time\n'
                      f'p = subprocess.run({launch!r})\n'
                      'print("HOST_EXITED", p.returncode, flush=True)\ntime.sleep(30)\n')
    r, w = os.pipe()
    xvfb = subprocess.Popen(['Xvfb', '-displayfd', str(w), '-screen', '0',
                             '1000x700x24', '-nolisten', 'tcp'], pass_fds=[w],
                            stdout=subprocess.DEVNULL,
                            stderr=(private / 'xvfb.log').open('wb'))
    os.close(w)
    # Xvfb may write the display number and newline separately. Closing after a
    # short read can give the server EPIPE before it finishes initialization.
    with os.fdopen(r) as display_pipe:
        display = ':' + display_pipe.readline().strip()
    env = {'HOME': str(private / 'home'), 'XDG_CONFIG_HOME': str(private / 'config'),
           'XDG_CACHE_HOME': str(private / 'cache'),
           'XDG_RUNTIME_DIR': str(private / 'runtime'),
           'PATH': str(private / 'bin') + ':/usr/bin:/bin', 'DISPLAY': display,
           'LANG': 'C.UTF-8', 'LIBGL_ALWAYS_SOFTWARE': '1'}
    terminal = None
    connection = None
    replies = None
    try:
        terminal = subprocess.Popen([
            str(engine), '--config', 'NONE', '--class', 'kilix-errors-test',
            '-o', 'initial_window_width=900', '-o', 'initial_window_height=600',
            '-o', 'remember_window_size=no', '-o', 'software_mouse_cursor=none',
            '-o', 'mouse_hide_wait=0', '/usr/bin/python3', str(runner)],
            env=env, stdout=subprocess.DEVNULL,
            stderr=(private / 'terminal.log').open('wb'), start_new_session=True)
        for _ in range(150):
            if ipc.exists(): break
            assert terminal.poll() is None
            time.sleep(.1)
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(3)
        connection.connect(str(ipc))
        replies = connection.makefile('rb')
        request_id = 0

        def command(*items, allow_error=False):
            nonlocal request_id
            request_id += 1
            connection.sendall(json.dumps({'command': items, 'request_id': request_id})
                               .encode() + b'\n')
            for line in replies:
                reply = json.loads(line)
                if reply.get('request_id') == request_id:
                    assert allow_error or reply['error'] == 'success', reply
                    return reply.get('data')
            raise RuntimeError('IPC closed')
        def xdo(*items): return subprocess.check_output(['xdotool', *items], env=env, text=True).strip()
        def screenshot(name):
            path=private/(name+'.png')
            subprocess.run(['import','-display',display,'-window','root',str(path)],env=env,check=True)
            return Image.open(path).convert('RGB')
        time.sleep(1)
        window=xdo('search','--class','kilix-errors-test').splitlines()[-1]
        xdo('windowsize',window,'900','600')
        xdo('windowmap',window)
        xdo('mousemove','990','690')
        time.sleep(1)
        print(xdo('getwindowgeometry',window),flush=True)
        print(command('get_property','osd-dimensions'),flush=True)
        before=screenshot('before')
        for _ in range(30):
            if before.getbbox(): break
            xdo('mousemove','--window',window,'450','300')
            time.sleep(.1)
            before=screenshot('before')
        assert before.getbbox(), 'initial video blank'
        # A real file error from mpv's subtitle loader; playback remains active.
        command('sub-add','/missing-subtitle-for-persistence-test.srt',allow_error=True)
        time.sleep(1)
        first=screenshot('error')
        command('show-text','Temporary seek message',500)
        time.sleep(5)
        later=screenshot('after-timeout')
        assert ImageChops.difference(before,first).getbbox(), 'error never appeared'
        assert not ImageChops.difference(first,later).getbbox(), 'persistent panel changed after timeout'
        command('seek',1,'absolute+exact')
        time.sleep(.5)
        seek=screenshot('after-seek')
        # Top video region contains the diagnostic panel, not the changing video.
        panel = (20, 60, 880, 125)
        assert not ImageChops.difference(first.crop(panel), seek.crop(panel)).getbbox(), \
            'panel lost on seek'
        xdo('windowsize',window,'700','500')
        xdo('mousemove','--window',window,'350','250')
        time.sleep(1)
        resized=screenshot('resized')
        assert resized.getbbox(), 'resized video blank'
        time.sleep(3)
        assert not ImageChops.difference(resized, screenshot('resized-later')).getbbox(), \
            'resized panel timed out'
        command('quit')
        time.sleep(1)
        screenshot('after-exit')
        result = {'transport': transport, 'actual_error': True,
                  'unchanged_after_seconds': 5, 'timed_osd_does_not_replace_panel': True,
                  'seek_preserves_panel_pixels': True, 'resized_panel_persists': True}
        (private/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)
    finally:
        if replies is not None:
            replies.close()
        if connection is not None:
            connection.close()
        if terminal is not None and terminal.poll() is None:
            terminal.terminate()
            try: terminal.wait(timeout=5)
            except subprocess.TimeoutExpired: terminal.kill(); terminal.wait()
        xvfb.terminate(); xvfb.wait(timeout=5)


if __name__ == '__main__':
    main()
