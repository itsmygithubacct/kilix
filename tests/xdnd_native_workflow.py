"""Real engine + independent GTK processes on owned authenticated X servers."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import shlex
from urllib.parse import quote

KILIX = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('--kilix', default=str(KILIX))
parser.add_argument('--engine', default=str(KILIX.parent / 'engine'))
parser.add_argument('--evidence', default=str(KILIX.parent.parent / 'evidence'))
parser.add_argument('--case', default='text')
args = parser.parse_args()
kilix_home = Path(args.kilix).resolve()
engine_home = Path(args.engine).resolve()
sys.path.insert(0, str(kilix_home / 'config'))
from kilix_sdk import xapp
from kilix_sdk.clipboard import xauthority_env
from Xlib import X
from Xlib import display as xdisplay
from Xlib.ext import xtest
from Xlib.protocol import event as xevent

folder = Path(args.evidence).resolve() / (args.case + '-' + secrets.token_hex(4))
folder.mkdir(mode=0o700)
payload = ('Καλημέρα · Привет · 你好 · café · שלום · 🙂\nsecond line\r\n').encode()
if args.case == 'large':
    payload *= 10000
types = ['text/plain;charset=utf-8', 'text/plain', 'text/uri-list']
physical = args.case.startswith('physical-') or args.case == 'clipboard'
shell = args.case.endswith('shell') or args.case.startswith('shell-')
moving = args.case in ('move', 'physical-move', 'move-refuse')
files = ['white space.txt', 'é-你好.txt', "quote';$(touch SHOULD_NOT_EXIST).txt", 'percent%back\\slash.txt']
paths = [str(folder / name) for name in files]
control = {'types': types}
if args.case == 'malformed-offer':
    types = ['text/plain injected/offer']
    control = {'types': types}
if args.case in ('uri', 'modifier-copy') or moving or shell:
    types = ['text/uri-list']
    for name in files:
        (folder / name).write_text('synthetic file\n')
    payload = ('# synthetic local paths\r\n' + '\r\n'.join('file://' + quote(path) for path in paths) + '\r\n').encode()
    control = {'types': types, 'move_files': files}
if args.case == 'move-refuse':
    control['refuse_data'] = True
if args.case == 'shell-malformed':
    payload = b'file://remote/tmp/%00invalid\r\n'
if args.case in ('shell-encoded-lf', 'shell-encoded-cr', 'shell-encoded-tab'):
    control_code = {'shell-encoded-lf': '%0A', 'shell-encoded-cr': '%0D', 'shell-encoded-tab': '%09'}[args.case]
    payload = b'file:///tmp/valid\r\nfile:///tmp/refused' + control_code.encode() + b'\r\n'
for case, flag in (('refuse', 'refuse'), ('refuse-data', 'refuse_data'), ('timeout', 'timeout')):
    if args.case == case:
        control[flag] = True
if args.case in ('stale-finished',):
    control['timeout'] = True
(folder / 'payload').write_bytes(payload)
(folder / 'control.json').write_text(json.dumps(control))
sessions = []
processes = []
ownership = []
owned_fds = {}
result = {'case': args.case, 'folder': str(folder), 'checks': {}, 'payload_sha256': hashlib.sha256(payload).hexdigest()}

def events(role):
    path = folder / (role + '.jsonl')
    try:
        return [json.loads(line) for line in path.read_text().splitlines()]
    except FileNotFoundError:
        return []

def wait(predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.015)
    if 'remote' in globals():
        try:
            (folder / 'pane-state.json').write_text(json.dumps(remote('ls'), indent=2))
            for pane in (1, 2):
                (folder / f'pane-{pane}.txt').write_text(command([kitten, '@', '--to', listen, 'get-text', '--match', f'id:{pane}'], desktop_env))
        except Exception:
            pass
    raise AssertionError('native workflow timed out')

def own(process):
    fields = Path('/proc', str(process.pid), 'stat').read_text().rsplit(')', 1)[1].split()
    token = fields[19]
    pidfd = os.pidfd_open(process.pid)
    processes.append((process, token, pidfd))
    ownership.append({'pid': process.pid, 'start': token, 'pidfd': pidfd, 'argv': process.args})
    owned_fds[process.pid] = pidfd
    return process

def capture_tree(pid):
    """Only descendants of an exact child we started are eligible for cleanup."""
    try:
        children = set()
        for thread in Path(f'/proc/{pid}/task').iterdir():
            children.update((thread / 'children').read_text().split())
    except FileNotFoundError:
        return
    for value in children:
        child = int(value)
        if child not in owned_fds:
            try:
                start = Path(f'/proc/{child}/stat').read_text().rsplit(')', 1)[1].split()[19]
                fd = os.pidfd_open(child)
                owned_fds[child] = fd
                ownership.append({'pid': child, 'start': start, 'pidfd': fd, 'ancestor': pid})
            except (FileNotFoundError, ProcessLookupError):
                continue
        capture_tree(child)

def kill_owned(pid):
    capture_tree(engine.pid)
    signal.pidfd_send_signal(owned_fds[pid], signal.SIGTERM)

def command(argv, env=None):
    return subprocess.check_output(argv, env=env, timeout=4).decode()

try:
    host = xapp.XAppSession('xdnd-physical-' + secrets.token_hex(6), 1900, 1000)
    sessions.append(host)
    host.start_xvfb()
    host.connect()
    own(host.server)
    desktop_env = host.environment()
    desktop_env.pop('KILIX_PRIVATE_XAPP', None)
    desktop_env['KILIX_RUN_LOG'] = str(folder / 'apprun.log')
    desktop_env['KILIX_RUN_WM'] = 'none'
    desktop_env['KILIX_NATIVE_DND'] = '1'
    desktop_env['KILIX_RUN_AUTO_FIT'] = '0'
    desktop_env['KITTY_DISABLE_WAYLAND'] = '1'
    desktop_env['NO_AT_BRIDGE'] = '1'
    desktop_env['KILIX_HOST_CLIP'] = '1'
    desktop_env['PLEB_DESKTOP_DISPLAY'] = host.display
    desktop_env['PLEB_DESKTOP_XAUTHORITY'] = host.xauthority
    desktop_env['KILIX_RUN_MAX'] = '1400x800'
    fixture = KILIX / 'tests/fixtures/xdnd_gtk.py'
    apprun = kilix_home / 'config/apprun.py'
    startup = folder / 'engine.session'
    shellrc = folder / 'bashrc'
    shellrc.write_text("PS1='XDND_PROMPT> '\n" +
        '_xdnd_line() { printf "%s" "$READLINE_LINE" > ' + shlex.quote(str(folder / 'shell-line')) + '; }\n' +
        "bind -x '\"\\C-x\\C-p\":_xdnd_line'\n" +
        "PROMPT_COMMAND=" + shlex.quote('printf "prompt\\n" >> ' + shlex.quote(str(folder / 'shell-prompts'))) + '\n')
    target_command = (f'/usr/bin/bash --noprofile --rcfile {shellrc} -i' if shell else
        f'/usr/bin/python3 {apprun} --size 360x240 --fps 20 /usr/bin/python3 {fixture} target {folder}')
    startup.write_text('new_tab XDND\nlayout horizontal\n' +
        f'launch --hold --title source /usr/bin/python3 {apprun} --size 360x240 --fps 20 /usr/bin/python3 {fixture} source {folder}\n' +
        f'launch --hold --title target {target_command}\n')
    if args.case == 'display-resize':
        startup.write_text(startup.read_text().replace('--size 360x240 ', ''))
    with tempfile.TemporaryDirectory(prefix='xdnd-owned-', dir='/tmp') as runtime:
        listen = 'unix:' + runtime + '/engine'
        log = (folder / 'engine.log').open('wb')
        engine = own(subprocess.Popen([str(engine_home / 'kitty/launcher/kitty'), *(['--debug-keyboard'] if args.case.startswith('input-') else []), '--config', 'NONE',
            '--listen-on', listen, '--session', str(startup), '--override', 'allow_remote_control=yes',
            '--override', 'enabled_layouts=horizontal,splits,tall', '--override', 'initial_window_width=1280',
            '--override', 'initial_window_height=720', '--override', 'tab_bar_style=hidden',
            '--override', 'shell_integration=enabled', '--override', 'remember_window_size=no',
            '--override', 'map ctrl+shift+u no_op',
            '--override', 'font_family=DejaVu Sans Mono'], env=desktop_env,
            stdout=log, stderr=log, cwd=folder))
        kitten = str(engine_home / 'kitty/launcher/kitten')
        def remote(*argv):
            reply = command([kitten, '@', '--to', listen, *argv], desktop_env)
            return json.loads(reply) if reply.strip() else None
        wait(lambda: Path(runtime + '/engine').exists())
        source_ready = wait(lambda: next((r for r in events('source') if r['event'] == 'ready'), None))
        target_ready = ({} if shell else wait(lambda: next((r for r in events('target') if r['event'] == 'ready'), None)))
        if shell:
            wait(lambda: (folder / 'shell-prompts').exists())
        windows = [w for osw in remote('ls') for tab in osw['tabs'] for w in tab['windows']]
        assert len(windows) == 2, windows
        source_id, target_id = [w['id'] for w in windows]
        result['private_displays'] = [source_ready['display']] + ([] if shell else [target_ready['display']])
        if not shell:
            assert source_ready['display'] != target_ready['display'] != host.display
        def position(pane, app_point):
            aw, ah = 360, 240
            if args.case == 'display-resize':
                ready = source_ready if pane == source_id else target_ready
                app_env = dict(item.split(b'=', 1) for item in Path(f"/proc/{ready['pid']}/environ").read_bytes().split(b'\0') if b'=' in item)
                with xauthority_env(app_env[b'XAUTHORITY'].decode()):
                    d = xdisplay.Display(app_env[b'DISPLAY'].decode())
                try:
                    size = d.screen().root.get_geometry()
                    aw, ah = size.width, size.height
                    result.setdefault('display_sizes', {}).setdefault(str(pane), []).append([aw, ah])
                finally:
                    d.close()
            geo = remote('get-pane-geometry', '--match', 'id:' + str(pane))
            if isinstance(geo, list):
                geo = geo[0]
            result.setdefault('geometry', {})[str(pane)] = geo
            left, top, right, bottom = geo['render_rect']
            cols, rows = geo['grid']
            cw, ch = geo['cell_size']
            pw, ph = cols * cw, (rows - 1) * ch
            scale = min(pw / aw, ph / ah)
            ic = max(1, round(aw * scale / cw))
            ir = max(1, min(rows - 1, round(ah * scale / ch)))
            bx, by = (cols - ic) // 2 * cw, (rows - 1 - ir) // 2 * ch
            ox, oy = geo['screen_origin']
            sx = geo['window_size'][0] / geo['framebuffer_size'][0]
            sy = geo['window_size'][1] / geo['framebuffer_size'][1]
            return (round(ox + (left + bx + app_point[0] * ic * cw / aw) * sx),
                    round(oy + (top + by + app_point[1] * ir * ch / ah) * sy))
        # Focus/click are delivered at the real owned engine X window.
        engine_xid = int(command(['xdotool', 'search', '--class', 'kitty'], desktop_env).split()[0])
        engine_window = host.xd.create_resource_object('window', engine_xid)
        if physical or args.case == 'cross-window':
            engine_window.configure(x=420, y=40)
            host.xd.sync(); time.sleep(.1)
        if args.case == 'cross-window':
            remote('detach-window', '--match', f'id:{target_id}', '--stay-in-tab')
            time.sleep(.2)
            xid = int(command(['xdotool', 'search', '--class', 'kitty'], desktop_env).split()[-1])
            host.xd.create_resource_object('window', xid).configure(x=1000, y=400, width=800, height=500)
            host.xd.sync(); time.sleep(.2)
        if args.case == 'resize':
            engine_window.configure(width=1050, height=580)
            host.xd.sync(); time.sleep(.5)
        start = position(source_id, (90, 100))
        dest = position(target_id, (180, 100))
        source_role = 'source'
        if physical:
            app = host.launch_app(['/usr/bin/python3', str(fixture), 'physical', str(folder)],
                clipboard=False, capture_source=False, isolate_bus=True)
            own(app)
            ready = wait(lambda: next((r for r in events('physical') if r['event'] == 'ready'), None))
            physical_ready = ready
            source_role = 'physical'
            start = (90, 100)
            host.xd.set_input_focus(host.xd.create_resource_object('window', ready['xid']), X.RevertToPointerRoot, X.CurrentTime)
        else:
            host.xd.set_input_focus(engine_window, X.RevertToPointerRoot, X.CurrentTime)
        result['gesture'] = {'start': start, 'drop': dest, 'transport': 'physical XTest'}
        capture_tree(engine.pid)
        for _, process, _, _ in host.supervisor.children:
            if process.pid not in owned_fds:
                own(process)
        def probe_input():
            entry = position(target_id, (180, 225))
            xtest.fake_input(host.xd, X.MotionNotify, x=entry[0], y=entry[1]); host.xd.sync()
            for _ in range(2):
                xtest.fake_input(host.xd, X.ButtonPress, 1)
                xtest.fake_input(host.xd, X.ButtonRelease, 1)
                host.xd.sync(); time.sleep(.15)
            app_env = dict(item.split(b'=', 1) for item in Path(f"/proc/{target_ready['pid']}/environ").read_bytes().split(b'\0') if b'=' in item)
            with xauthority_env(app_env[b'XAUTHORITY'].decode()):
                private = xdisplay.Display(app_env[b'DISPLAY'].decode())
            try:
                pointer = private.screen().root.query_pointer()
                result['input_probe'] = {'physical_focus': getattr(host.xd.get_input_focus().focus, 'id', 0),
                    'private_focus': getattr(private.get_input_focus().focus, 'id', 0),
                    'private_pointer': [pointer.root_x, pointer.root_y], 'physical_point': entry}
            finally:
                private.close()
            command(['xdotool', 'key', 'ctrl+shift+u'], desktop_env)
            command(['xdotool', 'type', '--delay', '20', '03bb'], desktop_env)
            command(['xdotool', 'key', 'Return'], desktop_env)
            command(['xdotool', 'type', '--delay', '20', 'input'], desktop_env)
            wait(lambda: any(r['event'] == 'entry-changed' and r['text'] == 'λinput' for r in events('target')), 3)
            command(['xdotool', 'key', 'ctrl+a'], desktop_env)
            wait(lambda: any(r['event'] == 'entry-selection' and r['bounds'] == [0, 6] for r in events('target')), 3)
            point = position(target_id, (180, 100))
            xtest.fake_input(host.xd, X.MotionNotify, x=point[0], y=point[1])
            xtest.fake_input(host.xd, X.ButtonPress, 4)
            xtest.fake_input(host.xd, X.ButtonRelease, 4)
            host.xd.sync()
            wait(lambda: any(r['event'] == 'ordinary-scroll' for r in events('target')), 3)

        if args.case == 'input-baseline':
            probe_input()
            result['checks']['ordinary_private_input_composition'] = True
            result['passed'] = True
            print(json.dumps(result, ensure_ascii=False), flush=True)
            sys.exit(0)
        if args.case == 'clipboard':
            def clipboard_read(role, mime, data, token):
                began = time.monotonic()
                digest = hashlib.sha256(data).hexdigest()
                for attempt in range(30):
                    count = len(events(role))
                    (folder / (role + '.command')).write_text(f'clip-read:{mime}|{token}-{attempt}')
                    received = wait(lambda: next((r for r in events(role)[count:] if r['event'] == 'clipboard-received'), None), 2)
                    if received['sha256'] == digest:
                        break
                    time.sleep(.08)
                assert received['sha256'] == digest, (role, mime, received)
                result['checks'][token] = received['time'] - began < 5
            text = 'Clipboard Καλημέρα 你好 Привет 🙂\n'.encode()
            (folder / 'clipboard-text').write_bytes(text)
            (folder / 'source.command').write_text('clip-copy:private')
            wait(lambda: any(r['event'] == 'clipboard-set' for r in events('source')))
            clipboard_read('target', 'UTF8_STRING', text, 'private_to_private_text')
            clipboard_read('physical', 'UTF8_STRING', text, 'private_to_physical_text')
            # A separate physical toolkit owner exercises the inbound hub.
            text = 'Physical clipboard héllo 世界 🐈'.encode()
            (folder / 'clipboard-text').write_bytes(text)
            (folder / 'physical.command').write_text('clip-copy:physical')
            wait(lambda: any(r['event'] == 'clipboard-set' for r in events('physical')))
            clipboard_read('target', 'UTF8_STRING', text, 'physical_to_private_text')
            def copy_with_xclip(which, mime, data):
                ready = physical_ready if which == 'physical' else source_ready if which == 'source' else target_ready
                process_env = dict(desktop_env)
                for item in Path(f"/proc/{ready['pid']}/environ").read_bytes().split(b'\0'):
                    key, _, value = item.partition(b'=')
                    if key in (b'DISPLAY', b'XAUTHORITY'):
                        process_env[key.decode()] = value.decode()
                process = own(subprocess.Popen(['xclip', '-quiet', '-selection', 'clipboard', '-target', mime],
                    env=process_env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                process.stdin.write(data); process.stdin.close()
                time.sleep(.3)
            png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII=')
            copy_with_xclip('source', 'image/png', png)
            clipboard_read('target', 'image/png', png, 'private_to_private_png')
            clipboard_read('physical', 'image/png', png, 'private_to_physical_png')
            uris = ('\r\n'.join('file://' + quote(path) for path in paths) + '\r\n').encode()
            copy_with_xclip('source', 'text/uri-list', uris)
            clipboard_read('target', 'text/uri-list', uris, 'private_to_private_files')
            clipboard_read('physical', 'text/uri-list', uris, 'private_to_physical_files')
            copy_with_xclip('physical', 'image/png', png)
            clipboard_read('target', 'image/png', png, 'physical_to_private_png')
            copy_with_xclip('physical', 'text/uri-list', uris)
            clipboard_read('target', 'text/uri-list', uris, 'physical_to_private_files')
            result['passed'] = all(result['checks'].values())
            print(json.dumps(result, ensure_ascii=False), flush=True)
            sys.exit(0)
        def move(point):
            xtest.fake_input(host.xd, X.MotionNotify, x=point[0], y=point[1]); host.xd.sync()
        move(start)
        xtest.fake_input(host.xd, X.ButtonPress, 1); host.xd.sync()
        for offset in (4, 10, 18, 26):
            move((start[0] + offset, start[1])); time.sleep(.08)
        wait(lambda: any(r['event'] == 'drag-begin' for r in events(source_role)), 3)
        time.sleep(.3)
        if args.case == 'display-resize':
            engine_window.configure(width=1400, height=850)
            host.xd.sync(); time.sleep(.7)
            dest = position(target_id, (180, 100))
        if moving or args.case in ('link', 'modifier-copy'):
            command(['xdotool', 'keydown', 'Shift_L'], desktop_env)
            if args.case == 'link':
                command(['xdotool', 'keydown', 'Control_L'], desktop_env)
        for number in range(1, 21):
            move((round(start[0] + (dest[0] - start[0]) * number / 20),
                  round(start[1] + (dest[1] - start[1]) * number / 20)))
            time.sleep(.03)
        time.sleep(.25)
        if args.case == 'cross-pane':
            move(start); time.sleep(.15); move(dest); time.sleep(.25)
        if args.case == 'target-replaced':
            (folder / 'target.command').write_text('replace-window')
            wait(lambda: any(r['event'] == 'window-replaced' for r in events('target')))
            time.sleep(.2)
        if args.case in ('target-exit', 'source-exit'):
            kill_owned(target_ready['pid'] if args.case == 'target-exit' else source_ready['pid'])
            time.sleep(.15)
        if args.case == 'lost-selection':
            (folder / 'source.command').write_text('steal-dnd')
            wait(lambda: any(r['event'] == 'selection-stolen' for r in events('source')))
            time.sleep(.2)
        if args.case in ('cancel', 'input-after-cancel'):
            command(['xdotool', 'key', 'Escape'], desktop_env)
            time.sleep(.15)
        if args.case == 'modifier-copy':
            command(['xdotool', 'keyup', 'Shift_L'], desktop_env)
            command(['xdotool', 'keydown', 'Control_L'], desktop_env)
            time.sleep(.25)  # Action changes while the physical pointer is stationary.
        prompts = (folder / 'shell-prompts').read_text() if shell else ''
        dropped_at = time.monotonic()
        xtest.fake_input(host.xd, X.ButtonRelease, 1); host.xd.sync()
        if moving or args.case in ('link', 'modifier-copy'):
            command(['xdotool', 'keyup', 'Shift_L', 'Control_L'], desktop_env)
        negative = args.case in ('refuse', 'refuse-data', 'move-refuse', 'link', 'malformed-offer', 'cancel', 'input-after-cancel', 'timeout', 'stale-finished', 'target-replaced', 'target-exit', 'source-exit', 'lost-selection')
        if args.case in ('stale-finished',):
            wait(lambda: any(r['event'] == 'drag-drop' for r in events('target')), 2)
            owner = host.xd.get_selection_owner(host.xd.intern_atom('XdndSelection'))
            owner.send_event(xevent.ClientMessage(window=owner.id, client_type=host.xd.intern_atom('XdndFinished'),
                data=(32, [0x7fffffff, 1, host.xd.intern_atom('XdndActionMove'), 0, 0])), event_mask=X.NoEventMask)
            host.xd.sync()
        if shell:
            wait(lambda: any(r['event'] == 'drag-end' for r in events(source_role)), 5)
            # A readline inspection binding records the buffer without Enter.
            remote('focus-window', '--match', f'id:{target_id}')
            command(['xdotool', 'key', 'ctrl+x', 'ctrl+p'], desktop_env)
            wait(lambda: (folder / 'shell-line').exists())
            line = (folder / 'shell-line').read_text()
            result['shell_line'] = line
            if args.case in ('shell-malformed', 'shell-encoded-lf', 'shell-encoded-cr', 'shell-encoded-tab'):
                assert line == '', repr(line)
            else:
                # Parse shell words; never execute or eval the dropped buffer.
                decode = command([str(engine_home / 'kitty/launcher/kitty'), '+launch',
                    str(fixture.with_name('shell_words.py')), line], desktop_env)
                assert json.loads(decode) == paths, repr(line)
            result['checks'].update(quoted_paths=True, no_submission=(folder / 'shell-prompts').read_text() == prompts,
                no_payload_execution=not (folder / 'SHOULD_NOT_EXIST').exists())
        elif negative:
            time.sleep(5.2 if args.case in ('timeout', 'stale-finished') else .5)
            received = [r for r in events('target') if r['event'] == 'drag-data-received']
            assert not received or all(not r['success'] for r in received), 'unexpected successful drop'
            assert not any(r['event'] == 'drag-data-delete' for r in events(source_role))
            if args.case != 'source-exit':
                wait(lambda: any(r['event'] == 'drag-end' for r in events(source_role)), 5)
            if args.case == 'stale-finished':
                ended = next(r for r in events(source_role) if r['event'] == 'drag-end')
                assert ended['time'] - dropped_at >= 4, 'wrong peer completed the drag early'
            assert not any(r['event'] == 'entry-changed' and 'Καλημέρα' in r['text'] for r in events('target'))
            if args.case in ('target-exit', 'target-replaced'):
                text = command([kitten, '@', '--to', listen, 'get-text', '--match', f'id:{target_id}'], desktop_env)
                assert 'Καλημέρα' not in text, 'closed/replaced peer became a terminal paste target'
                result['checks']['no_terminal_fallthrough'] = True
            result['checks'].update(drop_refused=True, no_move_delete=True)
        else:
            received = wait(lambda: next((r for r in events('target') if r['event'] == 'drag-data-received'), None), 5)
            result['elapsed_drop_seconds'] = received['time'] - dropped_at
            assert base64.b64decode(received['payload']) == payload, (received['size'], received['sha256'])
            assert received['success'] and received['action'] == (4 if moving else 2), 'wrong native action'
            assert not any(r['event'] == 'entry-changed' for r in events('target')), 'payload entered ordinary text input'
            result['checks']['exact_native_payload'] = True
            result['checks']['drop_within_5s'] = result['elapsed_drop_seconds'] < 5
            wait(lambda: any(r['event'] == 'drag-end' for r in events(source_role)), 5)
            assert not any(r['event'] == 'drag-failed' for r in events(source_role))
            result['checks']['native_source_completed'] = True
            if moving:
                wait(lambda: any(r['event'] == 'drag-data-delete' for r in events(source_role)))
                assert all(not (folder / name).exists() and (folder / ('copied-' + name)).exists() for name in files)
                result['checks']['accepted_native_move'] = True
            else:
                result['checks']['no_move_delete_on_copy'] = not any(r['event'] == 'drag-data-delete' for r in events(source_role))
        if args.case == 'modifier-copy':
            assert all((folder / name).exists() for name in files)
            result['checks']['stationary_modifier_renegotiated_copy'] = True
        # Pointer grab must be gone on completion, cancellation and app exit.
        probe = host.xd.screen().root.grab_pointer(False, 0, X.GrabModeAsync, X.GrabModeAsync, X.NONE, X.NONE, X.CurrentTime)
        assert probe == X.GrabSuccess, probe
        host.xd.ungrab_pointer(X.CurrentTime); host.xd.sync()
        result['checks']['no_lingering_physical_grab'] = True
        if args.case == 'input-after-cancel':
            probe_input()
            result['checks']['ordinary_click_keyboard_composition_after_cancel'] = True
            result['checks']['ordinary_text_selection_and_scrolling_after_cancel'] = True
        result['passed'] = all(result['checks'].values())
        print(json.dumps(result, ensure_ascii=False), flush=True)
except Exception as error:
    result['passed'] = False
    result['error'] = repr(error)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    try:
        (folder / 'pane-state.json').write_text(json.dumps(remote('ls'), indent=2))
        for pane in (1, 2):
            (folder / f'pane-{pane}.txt').write_text(command([kitten, '@', '--to', listen, 'get-text', '--match', f'id:{pane}'], desktop_env))
    except Exception as capture_error:
        result['capture_error'] = repr(capture_error)
    raise
finally:
    if 'engine' in globals():
        capture_tree(engine.pid)
    # Exact child identity plus pidfd signaling; never a pattern kill.
    for process, start, pidfd in reversed(processes):
        if process.poll() is None:
            signal.pidfd_send_signal(pidfd, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                process.wait(timeout=3)
    for session in reversed(sessions):
        session.close()
    for pid, fd in owned_fds.items():
        try:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
        except ProcessLookupError:
            pass
    time.sleep(.15)
    for pid, fd in owned_fds.items():
        try:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.close(fd)
    (folder / 'ownership.json').write_text(json.dumps(ownership, indent=2))
    (folder / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
