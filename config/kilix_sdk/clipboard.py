"""Bounded, asynchronous X11 CLIPBOARD exchange between private displays.

Only ordinary data formats are read. Selection targets with side effects and
PRIMARY are untouched. INCR transfers retain their original bytes even when a
new copy replaces the clipboard, and incomplete reads never replace the hub.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import time
import threading
from typing import Mapping
from urllib.parse import unquote_to_bytes, urlsplit

from Xlib import X, Xatom, display as xdisplay
from Xlib.ext import xfixes
from Xlib.protocol import event as xevent
from Xlib.protocol import request as xrequest

_XAUTHORITY_LOCK = threading.RLock()

MAX_BYTES = 64 * 1024 * 1024
MAX_TARGETS = 256
MAX_TRANSFERS = 16
TRANSFER_TIMEOUT = 10.0
MAX_TRANSFER_TIME = 60.0
FORMATS = (
    'UTF8_STRING', 'text/plain;charset=utf-8', 'text/plain', 'STRING',
    'image/png', 'image/jpeg', 'text/uri-list',
    'x-special/gnome-copied-files', 'application/x-kde-cutselection',
    # KeePassXC and KDE mark secrets with this. Mirroring it lets a pane's own
    # clipboard manager keep the value out of its history.
    'x-kde-passwordManagerHint',
)


@dataclass(frozen=True, init=False)
class Content:
    """Immutable, byte-preserving representations of one clipboard value."""
    formats: tuple[tuple[str, bytes], ...]

    def __init__(self, formats: Mapping[str, bytes]):
        values = []
        size = 0
        for name in FORMATS:
            if name in formats:
                if not isinstance(formats[name], (bytes, bytearray, memoryview)):
                    raise TypeError('clipboard values must be byte strings')
                value = bytes(formats[name])
                size += len(value)
                if size > MAX_BYTES:
                    raise ValueError('clipboard exceeds the 64 MiB transfer limit')
                values.append((name, value))
        if set(formats) - set(FORMATS):
            raise ValueError('unsupported clipboard format')
        object.__setattr__(self, 'formats', tuple(values))

    def get(self, name: str) -> bytes | None:
        return dict(self.formats).get(name)

    @classmethod
    def from_text(cls, text: str) -> Content:
        value = text.encode('utf-8')
        return cls({'UTF8_STRING': value,
                    'text/plain;charset=utf-8': value,
                    'STRING': text.encode('latin-1', 'replace')})

    @property
    def text(self) -> str:
        for name in FORMATS[:4]:
            value = self.get(name)
            if value is not None:
                return value.decode('latin-1' if name == 'STRING' else 'utf-8',
                                    'replace')
        return ''

    @classmethod
    def from_files(cls, paths, *, cut: bool = False) -> Content:
        uris = [Path(path).absolute().as_uri() for path in paths]
        return cls({'text/uri-list': ('\r\n'.join(uris) + '\r\n').encode(),
                    'x-special/gnome-copied-files':
                        (('cut' if cut else 'copy') + '\n' + '\n'.join(uris)).encode(),
                    'application/x-kde-cutselection': b'1' if cut else b'0'})

    @property
    def files(self) -> tuple[str, tuple[str, ...]]:
        try:
            return self._files()
        except (UnicodeError, ValueError):
            return 'copy', ()

    def _files(self) -> tuple[str, tuple[str, ...]]:
        value = self.get('x-special/gnome-copied-files')
        operation = 'copy'
        if value is not None:
            lines = value.decode('utf-8', 'strict').splitlines()
            if not lines or lines[0] not in ('cut', 'copy'):
                return operation, ()
            operation, lines = lines[0], lines[1:]
        else:
            value = self.get('text/uri-list')
            if value is None:
                return operation, ()
            lines = value.decode('utf-8', 'strict').splitlines()
            if self.get('application/x-kde-cutselection') == b'1':
                operation = 'cut'
        paths = []
        for line in lines:
            if not line or line.startswith('#'):
                continue
            uri = urlsplit(line)
            if (uri.scheme != 'file' or uri.netloc not in ('', 'localhost')
                    or uri.query or uri.fragment):
                continue
            path = os.fsdecode(unquote_to_bytes(uri.path))
            if path.startswith('/') and '\0' not in path:
                paths.append(path)
        return operation, tuple(dict.fromkeys(paths))


# Published when the copy the hub holds is cleared at its source. It equals an
# empty Content, so everything else treats it as empty, but endpoints receive
# it by identity and give up their mirrored selection instead of claiming an
# empty one (an empty claim is how a new pane waits for an in-progress copy).
CLEARED = Content({})


class Hub:
    def __init__(self):
        self.content = Content({})
        self._sinks = []
        self._pending_sinks = []
        self.fd_hooks = {}
        self.tick_hooks = []
        self.clipboard_revision = 0
        self.clipboard_read_revision = None
        self.clipboard_read_source = None

    def add_fd(self, fd, callback):
        self.fd_hooks[fd] = callback

    def remove_fd(self, fd):
        self.fd_hooks.pop(fd, None)

    def add_content_sink(self, callback):
        self._sinks.append(callback)

    def remove_content_sink(self, callback):
        if callback in self._sinks:
            self._sinks.remove(callback)

    def add_pending_sink(self, callback):
        self._pending_sinks.append(callback)

    def remove_pending_sink(self, callback):
        if callback in self._pending_sinks:
            self._pending_sinks.remove(callback)

    def begin_clipboard_read(self, source=None):
        self.clipboard_revision += 1
        self.clipboard_read_revision = self.clipboard_revision
        self.clipboard_read_source = source
        for callback in tuple(self._pending_sinks):
            callback()
        return self.clipboard_revision

    def end_clipboard_read(self, revision):
        if self.clipboard_read_revision == revision:
            self.clipboard_read_revision = None
            self.clipboard_read_source = None

    def set_clipboard_content(self, content, source=None):
        self.clipboard_revision += 1
        self.clipboard_read_revision = None
        self.clipboard_read_source = None
        self.content = content
        for callback in tuple(self._sinks):
            if callback != source:
                callback(content)


@contextmanager
def xauthority_env(path):
    with _XAUTHORITY_LOCK:
        previous = os.environ.get('XAUTHORITY')
        if path is not None:
            os.environ['XAUTHORITY'] = path
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop('XAUTHORITY', None)
            else:
                os.environ['XAUTHORITY'] = previous


class SelectionBridge:
    """One event-loop endpoint; the hub must expose fd and tick hooks."""
    def __init__(self, hub, display_name, xauthority=None, *, read_existing=False):
        self.hub = hub
        self.d = self.win = self._fd = None
        self._ok = False
        self.error = None
        self._sink = self._receive
        self._pending_sink = self._announce_pending
        self._content = None
        self._claim = False
        self._timestamp = 0
        self._published_revision = None
        self._selection_owner = 0
        self._incoming = None
        self._queue = []
        self._read_formats = {}
        self._read_known_targets = False
        self._read_targets = ()
        self._read_owner = 0
        self._outgoing = {}
        self._pending_requests = []
        self._generation = 0
        try:
            with xauthority_env(xauthority):
                self.d = xdisplay.Display(display_name)
            self.d.xfixes_query_version()
            self._selection_event = self.d.query_extension("XFIXES").first_event + xfixes.XFixesSelectionNotify
            self.win = self.d.screen().root.create_window(
                -10, -10, 1, 1, 0, X.CopyFromParent,
                window_class=X.InputOnly, visual=X.CopyFromParent,
                event_mask=X.PropertyChangeMask)
            self.atoms = {name: self.d.intern_atom(name) for name in
                          (*FORMATS, 'CLIPBOARD', 'TARGETS', 'TIMESTAMP',
                           'MULTIPLE', 'ATOM_PAIR', 'INCR', 'TEXT',
                           '_KILIX_CLIP_TIME')}
            self.names = {atom: name for name, atom in self.atoms.items()}
            self.d.xfixes_select_selection_input(
                self.win, self.atoms['CLIPBOARD'],
                xfixes.XFixesSetSelectionOwnerNotifyMask |
                xfixes.XFixesSelectionWindowDestroyNotifyMask |
                xfixes.XFixesSelectionClientCloseNotifyMask)
            maximum = int(self.d.display.info.max_request_length) * 4
            self.chunk_size = max(1024, min(65536, maximum - 256))
            self._fd = self.d.fileno()
            hub.add_fd(self._fd, self._on_readable)
            hub.add_content_sink(self._sink)
            add_pending = getattr(hub, 'add_pending_sink', None)
            if add_pending is not None:
                add_pending(self._pending_sink)
            hub.tick_hooks.append(self.tick)
            self._ok = True
            self.d.flush()
            owner = self.d.get_selection_owner(self.atoms['CLIPBOARD'])
            self._selection_owner = owner.id if owner else 0
            if owner and read_existing:
                self._start_read(owner.id, X.CurrentTime)
            elif not owner and not read_existing:
                # A new pane needs an owner even before its first copy. With
                # no owner, X11 refuses paste without consulting this relay.
                content = getattr(hub, 'clipboard_content', None)
                if content is None:
                    content = getattr(hub, 'content', None)
                if isinstance(content, Content):
                    self.push(content)
        except Exception:
            self.close()
            raise

    def _receive(self, value):
        if value is CLEARED:
            # The hub published a clear: the copy this display mirrored was
            # cleared at its source (a password manager's auto-clear, or the
            # owner exiting). Stop serving it rather than keep it pasteable.
            if self._ok:
                self._release()
            return
        self.push(value)

    def push(self, value):
        if isinstance(value, str):
            value = Content.from_text(value)
        if not self._ok or value is None:
            return
        if value == self._content and self._owns_selection():
            return
        self._abort_read()
        self._content = value
        self._claim = True
        # Obtain an actual server timestamp without blocking the event loop.
        self.win.change_property(self.atoms['_KILIX_CLIP_TIME'], Xatom.INTEGER,
                                 8, b'')
        self.d.flush()

    def _release(self):
        self._abort_read()
        self._claim = False
        self._content = None
        if self._timestamp:
            # Release at this endpoint's own claim time, never CurrentTime: X
            # ignores an older SetSelectionOwner, so a copy another client
            # made since (even one this endpoint has not yet heard about) is
            # never taken away.
            xrequest.SetSelectionOwner(display=self.d.display, window=X.NONE,
                                       selection=self.atoms['CLIPBOARD'],
                                       time=self._timestamp)
        if self._selection_owner == self.win.id:
            self._selection_owner = 0
        self.d.flush()

    def _source_released(self):
        # Clear the hub only if what it holds is still the value read from
        # this display; a newer copy elsewhere must survive an old owner here.
        revision = self._published_revision
        self._published_revision = None
        if revision is not None and revision == getattr(self.hub, 'clipboard_revision', None):
            self.hub.set_clipboard_content(CLEARED, source=self._sink)

    def _announce_pending(self):
        if self._ok and self._selection_owner == self.win.id:
            # GTK/browser clients cache available formats. A fresh ownership
            # timestamp invalidates that cache before the new data arrives;
            # requests for the refreshed formats wait for acquisition.
            try:
                self._claim = True
                self.win.change_property(self.atoms['_KILIX_CLIP_TIME'], Xatom.INTEGER, 8, b'')
                self.d.flush()
            except Exception as error:
                self.error = error
                self.close()

    def _owns_selection(self):
        owner = self.d.get_selection_owner(self.atoms['CLIPBOARD'])
        return bool(owner and owner.id == self.win.id)

    def _on_readable(self):
        if not self._ok:
            return
        try:
            for _ in range(256):
                # Reading a property reply can buffer the next transfer
                # event in python-xlib. Drain it before select() waits on a
                # socket that no longer contains those bytes.
                if not self.d.pending_events():
                    break
                self._handle(self.d.next_event())
        except Exception as error:
            self.error = error
            self.close()

    def tick(self, _now=None):
        if not self._ok:
            return
        self._on_readable()  # python-xlib can buffer events after a reply.
        now = time.monotonic()
        if self._incoming and (now > self._incoming['deadline'] or
                               now > self._read_deadline or
                               self._read_revision != self.hub.clipboard_revision):
            self._abort_read()
        for key, transfer in tuple(self._outgoing.items()):
            if now > transfer['deadline'] or now > transfer['hard_deadline']:
                self._outgoing.pop(key, None)
        pending = getattr(self.hub, 'clipboard_read_revision', None)
        for request in tuple(self._pending_requests):
            if now > request['deadline']:
                self._pending_requests.remove(request)
                self._notify(request['event'], 0)
            elif pending is not None and self._serve_pending(request['event']):
                self._pending_requests.remove(request)
            elif pending is None:
                self._pending_requests.remove(request)
                if self.hub.clipboard_revision == request['revision']:
                    # Acquisition failed: the last clipboard is retained in
                    # the hub, but must not masquerade as the attempted copy.
                    self._notify(request['event'], 0)
                else:
                    self._serve(request['event'], deferred=True)

    def _handle(self, ev):
        # python-xlib constructs display-specific event classes, so matching
        # against the extension module's class fails on real X servers.
        if ev.type == self._selection_event:
            if ev.selection == self.atoms['CLIPBOARD']:
                age = (self._timestamp - ev.selection_timestamp) & 0xffffffff
                if 0 < age < 0x80000000:
                    # A synchronous reply can leave an earlier ownership
                    # notification buffered. It must not undo a newer claim
                    # or start reading a clipboard this endpoint now owns.
                    return
                owner = getattr(ev.owner, 'id', ev.owner)
                self._selection_owner = owner
                if owner == self.win.id:
                    self._timestamp = ev.selection_timestamp
                elif owner:
                    self._claim = False
                    self._content = None
                    self._start_read(owner, ev.timestamp)
                else:
                    self._abort_read()
                    self._source_released()
            return
        if ev.type == X.SelectionNotify:
            self._read_reply(ev)
        elif ev.type == X.SelectionRequest:
            self._serve(ev)
        elif ev.type == X.PropertyNotify:
            if ev.window.id == self.win.id:
                if (ev.atom == self.atoms['_KILIX_CLIP_TIME'] and
                        ev.state == X.PropertyNewValue and self._claim):
                    self._claim = False
                    self._timestamp = ev.time
                    self.win.set_selection_owner(self.atoms['CLIPBOARD'], ev.time)
                    self._selection_owner = self.win.id
                    self.d.flush()
            elif (self._incoming and self._incoming['incremental'] and
                  ev.window.id == self._incoming['window'].id and
                  ev.atom == self._incoming['property'] and
                  ev.state == X.PropertyNewValue):
                self._read_chunk()
            elif ev.state == X.PropertyDelete:
                self._send_chunk((ev.window.id, ev.atom))
        elif ev.type == X.DestroyNotify:
            for key in tuple(self._outgoing):
                if key[0] == ev.window.id:
                    self._outgoing.pop(key, None)
            self._pending_requests[:] = [request for request in self._pending_requests
                if request['event'].requestor.id != ev.window.id]
        elif ev.type == X.SelectionClear:
            # Existing outgoing transfers retain their immutable payload.
            if not self._claim and ((ev.time - self._timestamp) & 0xffffffff) < 0x80000000:
                self._content = None

    def _abort_read(self):
        revision = getattr(self, '_read_revision', None)
        if revision is not None:
            end = getattr(self.hub, 'end_clipboard_read', None)
            if end is not None:
                end(revision)
        if self._incoming:
            try:
                self._incoming['window'].destroy()
                self.d.flush()
            except Exception:
                pass
        self._incoming = None
        self._queue = []
        self._read_formats = {}
        self._read_known_targets = False
        self._read_targets = ()
        self._read_owner = 0

    def _start_read(self, owner, timestamp):
        self._abort_read()
        self._read_owner = owner
        self._read_time = timestamp
        self._read_revision = self.hub.begin_clipboard_read(self)
        self._read_deadline = time.monotonic() + MAX_TRANSFER_TIME
        self._queue = ['TARGETS']
        self._next_read()

    def _next_read(self):
        if (self._read_revision != self.hub.clipboard_revision or
                time.monotonic() > self._read_deadline):
            self._abort_read()
            return
        if not self._queue:
            owner = self.d.get_selection_owner(self.atoms['CLIPBOARD'])
            if ((self._read_formats or self._read_known_targets) and owner
                    and owner.id == self._read_owner):
                content = Content(self._read_formats)
                self._content = content
                self.hub.set_clipboard_content(content, source=self._sink)
                self._published_revision = getattr(self.hub, 'clipboard_revision', None)
            end = getattr(self.hub, 'end_clipboard_read', None)
            if end is not None:
                end(self._read_revision)
            self._read_formats = {}
            self._read_owner = 0
            return
        name = self._queue.pop(0)
        # A distinct requestor window prevents delayed replies/chunks from an
        # old owner from being mistaken for a newer clipboard acquisition.
        window = self.d.screen().root.create_window(
            -10, -10, 1, 1, 0, X.CopyFromParent,
            window_class=X.InputOnly, visual=X.CopyFromParent,
            event_mask=X.PropertyChangeMask)
        prop = self.atoms['CLIPBOARD']
        self._incoming = {'name': name, 'window': window, 'property': prop,
                          'incremental': False, 'data': bytearray(),
                          'type': None, 'deadline': time.monotonic() + TRANSFER_TIMEOUT}
        window.convert_selection(self.atoms['CLIPBOARD'], self.atoms[name],
                                 prop, self._read_time)
        self.d.flush()

    def _property(self, window, prop, limit):
        reply = window.get_property(prop, X.AnyPropertyType, 0,
                                    (limit + 3) // 4)
        if reply is None or reply.bytes_after:
            raise ValueError('missing or oversized clipboard property')
        return reply

    def _read_reply(self, ev):
        transfer = self._incoming
        if (not transfer or ev.requestor.id != transfer['window'].id or
                ev.selection != self.atoms['CLIPBOARD'] or
                ev.target != self.atoms[transfer['name']]):
            return
        if not ev.property:
            self._finish_read(None)
            return
        if ev.property != transfer['property']:
            self._abort_read()
            return
        limit = MAX_TARGETS * 4 if transfer['name'] == 'TARGETS' else MAX_BYTES
        try:
            reply = self._property(transfer['window'], ev.property, limit)
            if reply.property_type == self.atoms['INCR']:
                if (reply.format != 32 or len(reply.value) != 1 or
                        int(reply.value[0]) > limit):
                    raise ValueError('invalid INCR size')
                transfer['incremental'] = True
                transfer['window'].delete_property(ev.property)
                self.d.flush()
                return
            self._consume_reply(reply)
        except (ValueError, TypeError):
            self._abort_read()

    def _read_chunk(self):
        # Chunk events belong to the temporary requestor, not our owner window.
        transfer = self._incoming
        if not transfer:
            return
        limit = MAX_TARGETS * 4 if transfer['name'] == 'TARGETS' else MAX_BYTES
        try:
            reply = self._property(transfer['window'], transfer['property'], limit)
            if transfer['name'] == 'TARGETS':
                if reply.format != 32 or reply.property_type != Xatom.ATOM:
                    raise ValueError('invalid target chunk')
                value = bytes(reply.value.tobytes())
            else:
                if reply.format != 8 or reply.property_type != self.atoms[transfer['name']]:
                    raise ValueError('invalid clipboard chunk type')
                value = bytes(reply.value)
            if len(transfer['data']) + len(value) > limit:
                raise ValueError('clipboard transfer exceeds limit')
            transfer['data'].extend(value)
            transfer['deadline'] = time.monotonic() + TRANSFER_TIMEOUT
            transfer['window'].delete_property(transfer['property'])
            self.d.flush()
            if not value:
                if transfer['name'] == 'TARGETS':
                    import array
                    atoms = array.array('I')
                    atoms.frombytes(transfer['data'])
                    self._finish_read(atoms)
                else:
                    self._finish_read(bytes(transfer['data']))
        except (ValueError, TypeError):
            self._abort_read()

    def _consume_reply(self, reply):
        name = self._incoming['name']
        if name == 'TARGETS':
            if reply.format != 32 or reply.property_type != Xatom.ATOM:
                raise ValueError('invalid clipboard targets')
            self._finish_read(reply.value)
        else:
            if reply.format != 8 or reply.property_type != self.atoms[name]:
                raise ValueError('invalid clipboard property type')
            self._finish_read(bytes(reply.value))

    def _finish_read(self, value):
        transfer = self._incoming
        transfer['window'].delete_property(transfer['property'])
        transfer['window'].destroy()
        self._incoming = None
        if transfer['name'] == 'TARGETS':
            if value is None:
                self._queue = ['UTF8_STRING', 'STRING']
            else:
                self._read_known_targets = True
                available = set(value)
                self._queue = [name for name in FORMATS if self.atoms[name] in available]
                self._read_targets = tuple(self._queue)
        elif value is not None:
            total = sum(len(data) for data in self._read_formats.values()) + len(value)
            if total > MAX_BYTES:
                self._abort_read()
                return
            self._read_formats[transfer['name']] = value
        self.d.flush()
        self._next_read()

    def _write_target(self, requestor, target, prop, *, content=None):
        content = self._content if content is None else content
        if target == self.atoms['TARGETS']:
            if content is None:
                return False
            requestor.change_property(prop, Xatom.ATOM, 32,
                [self.atoms[name] for name, _ in content.formats] +
                [self.atoms[name] for name in ('TARGETS', 'TIMESTAMP', 'MULTIPLE')] +
                ([self.atoms['TEXT']] if content.get('UTF8_STRING') is not None else []))
            return True
        if target == self.atoms['TIMESTAMP']:
            requestor.change_property(prop, Xatom.INTEGER, 32, [self._timestamp])
            return content is not None
        name = self.names.get(target)
        if name == 'TEXT':
            name, target = 'UTF8_STRING', self.atoms['UTF8_STRING']
        value = content.get(name) if content and name in FORMATS else None
        if value is None:
            return False
        if len(value) <= self.chunk_size:
            requestor.change_property(prop, target, 8, value)
        else:
            key = (requestor.id, prop)
            retained = {id(item['data']): item['data'] for item in self._outgoing.values()}
            retained[id(value)] = value
            if (key in self._outgoing or len(self._outgoing) >= MAX_TRANSFERS or
                    sum(map(len, retained.values())) > MAX_BYTES):
                return False
            requestor.change_attributes(event_mask=X.PropertyChangeMask | X.StructureNotifyMask)
            self._outgoing[key] = {'window': requestor, 'type': target,
                'data': value, 'offset': 0,
                'deadline': time.monotonic() + TRANSFER_TIMEOUT,
                'hard_deadline': time.monotonic() + MAX_TRANSFER_TIME}
            requestor.change_property(prop, self.atoms['INCR'], 32, [len(value)])
        return True

    def _send_chunk(self, key):
        transfer = self._outgoing.get(key)
        if not transfer:
            return
        value = transfer['data'][transfer['offset']:transfer['offset'] + self.chunk_size]
        try:
            transfer['window'].change_property(key[1], transfer['type'], 8, value)
            self.d.flush()
        except Exception:
            self._outgoing.pop(key, None)
            return
        transfer['offset'] += len(value)
        transfer['deadline'] = time.monotonic() + TRANSFER_TIMEOUT
        if not value:
            self._outgoing.pop(key, None)

    def _notify(self, ev, prop):
        try:
            ev.requestor.send_event(xevent.SelectionNotify(
                time=ev.time, requestor=ev.requestor.id, selection=ev.selection,
                target=ev.target, property=prop), onerror=lambda *_args: None)
            self.d.flush()
        except Exception:
            # A paste client can close while waiting for the copied formats.
            pass

    def _serve_pending(self, ev):
        """Serve a fully acquired new target without waiting for other targets."""
        source = getattr(self.hub, 'clipboard_read_source', None)
        if (source is None or not source._ok or
                source._read_revision != self.hub.clipboard_read_revision or
                time.monotonic() > source._read_deadline):
            return False
        name = self.names.get(ev.target)
        if name == 'TARGETS' and source._read_known_targets:
            # Only this metadata request may use the empty placeholders.
            content = Content({target: b'' for target in source._read_targets})
        elif name in (*FORMATS, 'TEXT'):
            target = 'UTF8_STRING' if name == 'TEXT' else name
            if target not in source._read_formats:
                return False
            content = Content(source._read_formats)
        else:
            # MULTIPLE retains its complete-bundle behavior.
            return False
        try:
            owner = source.d.get_selection_owner(source.atoms['CLIPBOARD'])
            if not owner or owner.id != source._read_owner:
                return False
            prop = ev.property or ev.target
            if not self._write_target(ev.requestor, ev.target, prop, content=content):
                return False
            self._notify(ev, prop)
            return True
        except Exception:
            return False

    def _serve(self, ev, *, deferred=False):
        prop = ev.property or ev.target
        served = False
        try:
            valid_time = deferred or ev.time == X.CurrentTime or ((ev.time - self._timestamp) & 0xffffffff) < 0x80000000
            pending = getattr(self.hub, 'clipboard_read_revision', None)
            if (not deferred and pending is not None and valid_time and
                    ev.selection == self.atoms['CLIPBOARD'] and
                    ev.target in (self.atoms[name] for name in
                                  (*FORMATS, 'TARGETS', 'TEXT', 'MULTIPLE'))):
                if self._serve_pending(ev):
                    return
                if len(self._pending_requests) < MAX_TRANSFERS:
                    ev.requestor.change_attributes(event_mask=X.PropertyChangeMask | X.StructureNotifyMask)
                    self._pending_requests.append({'event': ev, 'revision': pending,
                        'deadline': time.monotonic() + TRANSFER_TIMEOUT})
                    self.d.flush()
                    return
                self._notify(ev, 0)
                return
            if (ev.selection == self.atoms['CLIPBOARD'] and self._content is not None
                    and valid_time):
                if ev.target == self.atoms['MULTIPLE'] and ev.property:
                    reply = self._property(ev.requestor, prop, MAX_TARGETS * 8)
                    if reply.format == 32 and reply.property_type == self.atoms['ATOM_PAIR'] and len(reply.value) % 2 == 0:
                        pairs = list(reply.value)
                        used = {prop}
                        for i in range(0, len(pairs), 2):
                            target, output = pairs[i:i + 2]
                            if (not output or output in used or target == self.atoms['MULTIPLE'] or
                                    not self._write_target(ev.requestor, target, output)):
                                pairs[i] = 0
                            used.add(output)
                        ev.requestor.change_property(prop, self.atoms['ATOM_PAIR'], 32, pairs)
                        served = True
                else:
                    served = self._write_target(ev.requestor, ev.target, prop)
        except Exception:
            served = False
        self._notify(ev, prop if served else 0)

    def close(self):
        self._ok = False
        self._abort_read()
        for request in self._pending_requests:
            self._notify(request['event'], 0)
        self._pending_requests.clear()
        self._outgoing.clear()
        if self._fd is not None:
            self.hub.remove_fd(self._fd)
        self.hub.remove_content_sink(self._sink)
        remove_pending = getattr(self.hub, 'remove_pending_sink', None)
        if remove_pending is not None:
            remove_pending(self._pending_sink)
        if self.tick in self.hub.tick_hooks:
            self.hub.tick_hooks.remove(self.tick)
        if self.d is not None:
            self.d.close()
            self.d = None


def relay_main():
    import argparse
    import ctypes
    import select
    import signal
    parser = argparse.ArgumentParser()
    parser.add_argument('--host-display', required=True)
    parser.add_argument('--host-authority')
    parser.add_argument('--private-display', required=True)
    parser.add_argument('--private-authority', required=True)
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--ready-fd', type=int, required=True)
    args = parser.parse_args()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != args.parent_pid:
        raise SystemExit('clipboard relay parent is no longer present')
    hub = Hub()
    bridges = []
    running = True
    def stop(_signum, _frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        bridges.append(SelectionBridge(hub, args.host_display,
                                      args.host_authority, read_existing=True))
        bridges.append(SelectionBridge(hub, args.private_display,
                                      args.private_authority))
        os.write(args.ready_fd, b'1')
        os.close(args.ready_fd)
        while running and all(bridge._ok for bridge in bridges):
            ready, _, _ = select.select(tuple(hub.fd_hooks), (), (), .1)
            for fd in ready:
                callback = hub.fd_hooks.get(fd)
                if callback:
                    callback()
            for callback in tuple(hub.tick_hooks):
                callback()
    finally:
        for bridge in reversed(bridges):
            bridge.close()


if __name__ == '__main__':
    relay_main()
