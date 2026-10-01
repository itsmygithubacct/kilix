#!/usr/bin/env python3
"""Record host X11 fallback windows for future Kilix routing fixes.

Run with the system Python (python3-xlib), once per display. Never inspect
environments or terminal contents. argv is observed process state, not a claim
to have recovered the original shell command or a D-Bus activation request.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import select
import socket
import sys


class ParentLifetime:
    """Observe one kernel process handle; PID reuse cannot prolong its lifetime."""
    def __init__(self, pid, expected_start_tick=None):
        self.fd = None
        self.dead = False
        if not pid:
            return
        try:
            self.fd = os.pidfd_open(pid)
        except ProcessLookupError:
            self.dead = True
            return
        if expected_start_tick is not None:
            try:
                fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
                if fields[19] != str(expected_start_tick):
                    self.dead = True
            except (OSError, IndexError):
                self.dead = True
            if self.dead:
                self.close()

    def active(self):
        return not self.dead and (self.fd is None or not select.select([self.fd], [], [], 0)[0])

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def process_chain(pid, proc=Path('/proc')):
    chain = []
    seen = set()
    while pid and pid not in seen and len(chain) < 8:
        seen.add(pid)
        base = proc / str(pid)
        item = {'pid': pid}
        try:
            if base.stat().st_uid != os.getuid():
                break
            status = (base / 'status').read_text()
            item['ppid'] = int(next(x.split()[1] for x in status.splitlines()
                                    if x.startswith('PPid:')))
            raw = (base / 'cmdline').read_bytes()[:65536]
            item['argv'] = [x.decode('utf-8', 'replace') for x in raw.rstrip(b'\0').split(b'\0')] if raw else []
            for key in ('exe', 'cwd'):
                try:
                    item[key] = os.readlink(base / key)
                except OSError:
                    item[key] = None
        except (OSError, ValueError, StopIteration):
            item['unavailable'] = True
            chain.append(item)
            break
        chain.append(item)
        pid = item['ppid']
    return chain


def make_logger(state):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / 'native-windows.jsonl'
    # The session state directory is private; ensure existing log permissions too.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    os.close(fd)
    logger = logging.getLogger('pleb.native-windows')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=4,
                                  encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    return logger


class Watcher:
    def __init__(self, display, logger):
        from Xlib import X
        self.d = display
        self.root = display.screen().root
        self.logger = logger
        self.atoms = {}
        self.known = {}
        self.active = 0
        self.root.change_attributes(event_mask=X.PropertyChangeMask)
        self.d.sync()

    def atom(self, name):
        if name not in self.atoms:
            self.atoms[name] = self.d.intern_atom(name)
        return self.atoms[name]

    def prop(self, window, name):
        from Xlib import X
        try:
            p = window.get_full_property(self.atom(name), X.AnyPropertyType)
            return p.value if p is not None else None
        except Exception:
            return None  # Windows can disappear between any two X requests.

    def ids(self, window, name):
        value = self.prop(window, name)
        return list(value) if value is not None and not isinstance(value, bytes) else []

    def text(self, window, name):
        value = self.prop(window, name)
        return value.decode('utf-8', 'replace').rstrip('\0') if isinstance(value, bytes) else ''

    def snapshot(self, wid):
        from Xlib.ext import res
        window = self.d.create_resource_object('window', wid)
        classes = self.text(window, 'WM_CLASS').split('\0')
        if any(c.lower() == 'kilix' for c in classes):
            return None
        types = self.ids(window, '_NET_WM_WINDOW_TYPE')
        if any(self.atom(t) in types for t in ('_NET_WM_WINDOW_TYPE_DOCK', '_NET_WM_WINDOW_TYPE_DESKTOP')):
            return None
        # Unlike the taskbar, include dialogs/splashes and SKIP_TASKBAR windows:
        # these are routing escapes too. Nested in-pane X displays are not on
        # this display's managed-client list and so never enter this watcher.
        pid = None
        source = None
        try:
            if self.d.has_extension('X-Resource'):
                reply = self.d.res_query_client_ids([{'client': wid, 'mask': res.LocalClientPIDMask}])
                for entry in reply.ids:
                    if entry.spec.mask & res.LocalClientPIDMask and len(entry.value):
                        pid = int(entry.value[0])
                        source = 'XRes'
                        break
        except Exception:
            pass
        machine = self.text(window, 'WM_CLIENT_MACHINE')
        if pid is None and machine in ('', socket.gethostname(), socket.getfqdn(), 'localhost'):
            values = self.ids(window, '_NET_WM_PID')
            if values:
                pid, source = int(values[0]), '_NET_WM_PID'
        chain = process_chain(pid) if pid else []
        return {'window': f'0x{wid:x}', 'wm_class': classes,
                'wm_command': self.text(window, 'WM_COMMAND').split('\0') if self.text(window, 'WM_COMMAND') else [],
                'client_machine': machine, 'pid_source': source,
                'processes': chain,
                'launch_context': 'observed_process_chain' if chain else 'unavailable'}

    def emit(self, event, snapshot):
        self.logger.info(json.dumps({'schema': 1, 'event': event,
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'display': self.d.get_display_name(), **snapshot}, ensure_ascii=True))

    def reconcile(self):
        from Xlib import X
        clients = set(self.ids(self.root, '_NET_CLIENT_LIST'))
        for wid in set(self.known) - clients:
            del self.known[wid]
        for wid in clients:
            if wid in self.known:
                continue
            w = self.d.create_resource_object('window', wid)
            try:
                w.change_attributes(event_mask=X.PropertyChangeMask | X.StructureNotifyMask)
                self.d.sync()
            except Exception:
                continue
            snapshot = self.snapshot(wid)
            self.known[wid] = snapshot
            if snapshot is not None:
                self.emit('opened', snapshot)
        self.focus()

    def focus(self):
        active = self.ids(self.root, '_NET_ACTIVE_WINDOW')
        wid = active[0] if active else 0
        if wid != self.active:
            self.active = wid
            snapshot = self.known.get(wid)
            if snapshot is not None:
                self.emit('focused', snapshot)

    def handle(self, event):
        from Xlib import X
        wid = getattr(getattr(event, 'window', None), 'id', 0)
        if wid == self.root.id and event.type == X.PropertyNotify:
            if event.atom == self.atom('_NET_CLIENT_LIST'):
                self.reconcile()
            elif event.atom == self.atom('_NET_ACTIVE_WINDOW'):
                self.focus()
        elif wid in self.known:
            if event.type == X.PropertyNotify and event.atom in {self.atom(n) for n in (
                    '_NET_WM_PID', 'WM_COMMAND', 'WM_CLASS', 'WM_CLIENT_MACHINE', '_NET_WM_WINDOW_TYPE')}:
                snapshot = self.snapshot(wid)
                if snapshot != self.known[wid]:
                    self.known[wid] = snapshot
                    if snapshot is not None:
                        self.emit('metadata', snapshot)
            elif event.type == X.MapNotify and self.known[wid] is not None:
                self.emit('mapped', self.known[wid])
            elif event.type == X.DestroyNotify:
                self.known.pop(wid, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path(os.environ.get(
        'PLEB_STATE_HOME', str(Path.home() / '.local/gpu_terminal/pleb/state'))))
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--parent-start-tick', type=int)
    args = parser.parse_args()
    with ParentLifetime(args.parent_pid, args.parent_start_tick) as parent:
        if not parent.active():
            return 0
        os.umask(0o077)
        from Xlib import display
        d = display.Display()
        args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = hashlib.sha256(d.get_display_name().encode()).hexdigest()[:16]
        with open(args.state_dir / f'native-windows-{key}.lock', 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            watcher = Watcher(d, make_logger(args.state_dir))
            watcher.reconcile()
            while True:
                if not parent.active():
                    return 0
                if d.pending_events():
                    watcher.handle(d.next_event())
                else:
                    endpoints = [d.fileno()] + ([] if parent.fd is None else [parent.fd])
                    select.select(endpoints, [], [], 1)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f'Native window logger stopped: {exc}', file=sys.stderr)
        sys.exit(1)
