"""Native XDND endpoint for a managed private application display.

OSC 72 is the engine's pane-scoped drag protocol, not a clipboard transport.
An application remains the native source; a destination sees a normal XDND
offer, selection conversion and Finished exchange. Run this endpoint in the
same event loop as the pane's injector and terminal parser.
"""
from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass, field
import re
import time
from typing import Callable

from Xlib import X, Xatom, display as xdisplay, error as xerror
from Xlib.ext import xfixes, xtest
from Xlib.protocol import event as xevent
from Xlib.protocol import request as xrequest

from .clipboard import xauthority_env

DND_CODE = 72
MAX_TYPES = 64
MAX_TYPE_BYTES = 4096
MAX_BYTES = 16 * 1024 * 1024
MAX_REQUESTS = 8
IDLE_TIMEOUT = 2.0
TRANSFER_TIMEOUT = 4.5
GESTURE_TIMEOUT = 120.0
_TYPE = re.compile(r'[A-Za-z0-9!#$&^_.+\-]+/[A-Za-z0-9!#$&^_.+;=\-]+\Z')
_TEXT_TYPES = frozenset(('UTF8_STRING', 'STRING', 'TEXT', 'COMPOUND_TEXT'))
_ATOMS = ('XdndSelection', 'XdndAware', 'XdndProxy', 'XdndTypeList',
          'XdndActionList', 'XdndActionCopy', 'XdndActionMove', 'XdndActionLink',
          'XdndEnter', 'XdndPosition', 'XdndStatus', 'XdndDrop', 'XdndLeave',
          'XdndFinished', 'TARGETS', 'TIMESTAMP', 'INCR', '_KILIX_DND_TIME',
          '_KILIX_DND_DATA', 'DELETE', 'NULL')


def _time_after(stamp: int, start: int) -> bool:
    # X timestamps are 32-bit serial numbers; zero is CurrentTime, not an epoch.
    return bool(stamp and start and ((stamp - start) & 0xffffffff) < 0x80000000)


def _time_in_period(stamp: int, start: int, end: int) -> bool:
    return (_time_after(end, start) and _time_after(stamp, start) and
            ((stamp - start) & 0xffffffff) <= ((end - start) & 0xffffffff))


def parse_message(value: bytes) -> dict | None:
    """Parse one OSC body. Non-protocol strings never become input text."""
    if not value.startswith(b'72;') or len(value) > 4608:
        return None
    meta, _, payload = value[3:].partition(b';')
    values = {'kind': 'dnd', 't': 'a', 'payload': payload}
    try:
        for item in meta.split(b':'):
            key, sep, raw = item.partition(b'=')
            if not sep or len(key) != 1 or key.decode('ascii') in values:
                # t has a default, but may be specified once.
                if key != b't' or 'explicit_t' in values:
                    return None
            name = key.decode('ascii')
            if name == 't':
                if len(raw) != 1 or raw not in b'aAmMrRopPqeEk':
                    return None
                values['t'] = raw.decode('ascii')
                values['explicit_t'] = True
            else:
                if not re.fullmatch(rb'-?[0-9]{1,10}', raw):
                    return None
                number = int(raw)
                if not -(2 ** 31) <= number <= 2 ** 32 - 1:
                    return None
                values[name] = number
    except (UnicodeError, ValueError):
        return None
    values.pop('explicit_t', None)
    return values


def _types(raw: bytes) -> tuple[str, ...]:
    if len(raw) > MAX_TYPE_BYTES:
        raise ValueError('XDND offer too large')
    result = tuple(dict.fromkeys(raw.decode('ascii', 'strict').split()))
    if not result or len(result) > MAX_TYPES or any(
            not (_TYPE.fullmatch(s) or s in _TEXT_TYPES) for s in result):
        raise ValueError('invalid XDND types')
    return result


@dataclass
class _Source:
    owner: int
    timestamp: int
    version: int = 5
    types: tuple[str, ...] = ()
    operations: int = 0
    operations_known: bool = False
    started: bool = False
    released: bool = False
    native_drop: bool = False
    drop_action: int = 0
    drop_time: int = 0
    failed: bool = False
    cancel_sent: bool = False
    canceling: bool = False
    finish: int | None = None
    action: int = 1
    delete_window: object = None
    delete_complete: bool = False
    delete_supported: bool | None = None
    delete_probe: object = None
    deadline: float = field(default_factory=lambda: time.monotonic() + GESTURE_TIMEOUT)
    cache: dict[str, bytes] = field(default_factory=dict)
    requests: deque[int] = field(default_factory=deque)


@dataclass
class _Target:
    types: tuple[str, ...]
    operations: int
    suggested: int
    owner: object = None
    receiver: int = 0
    recipient: int = 0
    version: int = 5
    action: int = 0
    dropped: bool = False
    stamp: int = 0
    acquired: int = 0
    point: tuple[int, int] = (0, 0)
    deadline: float = field(default_factory=lambda: time.monotonic() + GESTURE_TIMEOUT)
    cache: dict[str, bytes] = field(default_factory=dict)
    requests: deque = field(default_factory=deque)
    reading: int = -1
    data: bytearray = field(default_factory=bytearray)
    idle: float = 0
    hard: float = 0
    awaiting: bool = False
    sent_position: tuple | None = None
    release_pending: bool = False
    time_requests: deque = field(default_factory=deque)
    deliveries: list = field(default_factory=list)
    failed: bool = False
    delete_receipt: tuple | None = None
    epoch_ready: bool = False
    canceling: bool = False


@dataclass
class _Delivery:
    generation: _Target
    event: object
    time: int
    delivered: bool = False
    failed: bool = False


@dataclass(eq=False)
class _Output:
    source: _Source
    generation: int
    data: bytes
    meta: dict
    offset: int = 0


class NativeDndBridge:
    """One display, one pane and no process-global drag registry or X grab.

    ``write`` writes to this pane's terminal. ``image_box`` returns the live
    (x,y,width,height) image placement in pane pixels. ``injector`` is the
    session's existing private injector. Call handle() for parsed OSC 72
    responses and tick() at least every 10 ms while busy. close() unregisters
    before the private server is stopped.
    """
    def __init__(self, display: str, authority: str, write: Callable,
                 image_box: Callable, injector, *, diagnostic: Callable | None = None):
        self.write, self.image_box, self.injector = write, image_box, injector
        self.diagnostic = diagnostic or (lambda *_: None)
        self.closed = False
        self._closing = False
        self._ticking = False
        self.enabled = False
        self.source: _Source | None = None
        self.target: _Target | None = None
        self.reading = None
        self.outgoing = {}
        self.output = deque()
        self.output_bytes = 0
        self._output_generation = 0
        self.chunk = 32768
        self.stamp = 0
        self.position_pending = False
        self._selection_stamp = 0
        self._deferred_events = deque()
        self._refreshing_epoch = False
        with xauthority_env(authority):
            self.d = xdisplay.Display(display)
        try:
            self.d.set_error_handler(self._x_error)
            self.atoms = {name: self.d.intern_atom(name) for name in _ATOMS}
            self.names = {v: k for k, v in self.atoms.items()}
            self.root = self.d.screen().root
            self.win = self.root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
                X.InputOnly, X.CopyFromParent, event_mask=X.PropertyChangeMask)
            self.catcher = self.root.create_window(0, 0, 1, 1, 0, X.CopyFromParent,
                X.InputOnly, X.CopyFromParent, override_redirect=True)
            self.catcher.change_property(self.atoms['XdndAware'], Xatom.ATOM, 32, [5])
            self.d.xfixes_query_version()
            self.selection_event = self.d.query_extension('XFIXES').first_event + xfixes.XFixesSelectionNotify
            self.d.xfixes_select_selection_input(self.win, self.atoms['XdndSelection'],
                xfixes.XFixesSetSelectionOwnerNotifyMask |
                xfixes.XFixesSelectionWindowDestroyNotifyMask |
                xfixes.XFixesSelectionClientCloseNotifyMask)
            self.chunk = max(1024, min(self.chunk, int(self.d.display.info.max_request_length) * 4 - 256))
            self.mark_time()
            self.send(t='q')
            self.d.flush()
        except Exception:
            self.d.close()
            raise

    def _x_error(self, error, request):
        # Peer windows can disappear between a query and an asynchronous send.
        # A failed write never converts a refusal into completion.
        source, target = self.source, self.target
        # Unhandled errors have request=None in python-xlib. Their origin is
        # unknown, so the current gesture cannot establish successful delivery.
        # Payload writes use request-specific handlers below, bound to a target
        # object, so a late error from an old gesture cannot poison its successor.
        if target:
            target.failed = True
        if source:
            source.failed = True
        self._callback(self.diagnostic, 'xdnd X error', type(error).__name__)

    def _delivery_error(self, delivery):
        def failed(error, request):
            delivery.failed = True
            delivery.delivered = False
            if self.target is delivery.generation:
                delivery.generation.failed = True
            self._callback(self.diagnostic, 'xdnd delivery X error', type(error).__name__)
            return 1  # handled; do not invoke the unscoped default handler
        return failed

    def _refresh_epoch(self):
        if self._refreshing_epoch:
            return False  # an in-progress drain is never positive authority
        self._refreshing_epoch = True
        try:
            self.d.sync()
            self._epoch_owner = self._selection_owner()
            # The final owner query is inside this drain: its reply can queue
            # a same-XID reacquisition. No round trip follows the drained result.
            # Synchronous replies also queue XFixes events. Consume ownership
            # changes before granting authority, retaining other events in order.
            for _ in range(256):
                if not self.d.pending_events():
                    return True
                event = self.d.next_event()
                if event.type == self.selection_event and event.selection == self.atoms['XdndSelection']:
                    self._native_owner_changed(event)
                elif event.type == X.DestroyNotify and self._bound_peer(event.window.id):
                    # Close/reuse of a bound peer also revokes completion. Keep
                    # any queries made by cancellation inside this same drain.
                    self._event(event)
                else:
                    self._deferred_events.append(event)
                    if len(self._deferred_events) >= 256:
                        return False
            return False  # bounded pass; an unexamined epoch never grants access
        finally:
            self._refreshing_epoch = False

    def _bound_peer(self, xid):
        source, target = self.source, self.target
        return (bool(source and xid == source.owner) or bool(target and
                (xid in (target.owner.id, target.receiver, target.recipient) or
                 any(xid == r.requestor.id for r in target.requests) or
                 any(xid == d.event.requestor.id for d in target.deliveries))))

    def _source_current(self, source):
        return (not self.closed and not self._closing and self._refresh_epoch() and
                self.source is source and not source.failed and
                time.monotonic() < source.deadline and
                source.timestamp != X.CurrentTime and self._selection_stamp == source.timestamp and
                self._epoch_owner == source.owner)

    def _target_current(self, target):
        return (not self.closed and not self._closing and self._refresh_epoch() and
                self.target is target and not target.failed and
                time.monotonic() < target.deadline and
                target.acquired != X.CurrentTime and self._selection_stamp == target.acquired and
                self._epoch_owner == target.owner.id)

    def _delivery_complete(self, target):
        self.d.sync()  # establish the result of every queued publication request
        return (self._target_current(target) and bool(target.deliveries) and
                all(d.delivered and not d.failed for d in target.deliveries) and
                not target.time_requests)

    def fileno(self):
        return self.d.fileno()

    @property
    def busy(self):
        return bool(self.source or self.target or self.output or self.reading)

    def _callback(self, callback, *args, **kwargs):
        source, target = self.source, self.target
        try:
            return callback(*args, **kwargs)
        except Exception as error:
            # Preserve the original exception class and the lifetime that
            # invoked it, even if the callback installed a successor first.
            error._xdnd_callback_lifetime = (source, target)
            raise

    def send(self, payload: bytes = b'', **meta):
        if self.closed:
            return False
        source, target = self.source, self.target
        generation, closing = self._output_generation, self._closing
        header = ':'.join(f'{key}={value}' for key, value in meta.items()).encode('ascii')
        self._callback(self.write, b'\x1b]72;' + header + b';' + payload + b'\x1b\\')
        current = (not self.closed and self._closing == closing and self.source is source and
                   self.target is target and self._output_generation == generation)
        if not current:
            # A partially written callback sequence cannot be retried as a
            # fresh success. Revoke only its still-attached captured lifetime.
            if source is not None and self.source is source:
                source.failed = True
                self._clear_output()
            if target is not None and self.target is target:
                target.failed = True
        return current

    def _send_data(self, data: bytes, *, source, **meta):
        # Work per event-loop pass is bounded; the terminal remains responsive.
        if self.closed or self._closing or self.source is not source or source.failed:
            return
        encoded = base64.b64encode(data)
        if self.output_bytes + len(encoded) > MAX_BYTES * 2:
            raise ValueError('XDND terminal queue full')
        self.output.append(_Output(source, self._output_generation, encoded, meta))
        self.output_bytes += len(encoded)

    def _clear_output(self):
        self._output_generation += 1
        self.output.clear()
        self.output_bytes = 0

    def _output_current(self, entry):
        return (not self.closed and not self._closing and self.source is entry.source and
                not entry.source.failed and entry.generation == self._output_generation and
                bool(self.output) and self.output[0] is entry)

    def mark_time(self):
        window = self.target.owner if self.target and not self.target.acquired else self.win
        window.change_property(self.atoms['_KILIX_DND_TIME'], Xatom.INTEGER, 8, b'1')
        self.d.flush()

    def resize(self):
        """Keep the native source catch surface on the resized owned display."""
        if self.source and not self.closed:
            self.catcher.configure(width=self.injector.app_w, height=self.injector.app_h,
                stack_mode=X.Above)
            self.d.flush()

    def _window(self, xid):
        return self.d.create_resource_object('window', xid)

    def _property(self, window, name, expected, limit):
        value = window.get_property(self.atoms[name], expected, 0, (limit + 3) // 4)
        if value is not None and (value.property_type != expected or value.format not in (8, 16, 32) or
                value.bytes_after or len(value.value) * (value.format // 8) > limit):
            raise ValueError('invalid or oversized XDND property')
        return value

    def _atoms_property(self, window, name):
        prop = self._property(window, name, Xatom.ATOM, MAX_TYPES * 4)
        if prop is None:
            return ()
        if prop.format != 32 or prop.property_type != Xatom.ATOM:
            raise ValueError('invalid XDND atom list')
        return tuple(int(v) for v in prop.value)

    def _message(self, recipient, name, values, *, receiver=None):
        values = [int(n) & 0xffffffff for n in values]
        values += [0] * (5 - len(values))
        self._window(recipient).send_event(xevent.ClientMessage(
            window=receiver or recipient, client_type=self.atoms[name], data=(32, values)),
            event_mask=X.NoEventMask)
        self.d.flush()

    def _selection_owner(self):
        owner = self.d.get_selection_owner(self.atoms['XdndSelection'])
        self._epoch_owner = owner.id if owner else 0
        return self._epoch_owner

    def _native_owner_changed(self, event):
        owner = getattr(event.owner, 'id', event.owner)
        stamp = event.selection_timestamp & 0xffffffff
        if self._selection_owner() != owner:
            return  # queued notification from an earlier selection generation
        if self._selection_stamp and stamp and 0 < ((self._selection_stamp - stamp) & 0xffffffff) < 0x80000000:
            return
        self._selection_stamp = stamp
        source, target = self.source, self.target
        # The source was created by its real acquisition notification. Any
        # further callback is a replacement, even with equal XID/time words.
        if source:
            self.cancel_source('selection epoch changed', source=source)
        if target and self.target is target:
            if (not target.epoch_ready and owner == target.owner.id and
                    stamp == target.acquired):
                target.epoch_ready = True  # our one real acquisition
            else:
                self.cancel_target('selection replaced', target=target)
        if self.closed or self._closing or not self.enabled or not owner or not stamp or self.source or self.target:
            return
        # A selection without the actual forwarded press is never authority
        # to start a physical drag or to export its payload.
        if 1 not in self.injector._btns_down or self._selection_owner() != owner:
            return
        self.source = _Source(owner, stamp)
        self.catcher.destroy()
        self.catcher = self.root.create_window(0, 0, 1, 1, 0, X.CopyFromParent,
            X.InputOnly, X.CopyFromParent, override_redirect=True)
        self.catcher.change_property(self.atoms['XdndAware'], Xatom.ATOM, 32, [5])
        self._window(owner).change_attributes(event_mask=X.StructureNotifyMask)
        g = self.root.get_geometry()
        self.catcher.configure(x=0, y=0, width=g.width, height=g.height, stack_mode=X.Above)
        self.catcher.map()
        self.d.flush()
        pointer = self.root.query_pointer()
        # GTK re-evaluates the target on motion, not when another window maps.
        xtest.fake_input(self.d, X.MotionNotify, x=min(g.width - 1, pointer.root_x + 1), y=pointer.root_y)
        xtest.fake_input(self.d, X.MotionNotify, x=pointer.root_x, y=pointer.root_y)
        self.d.flush()
        self._callback(self.diagnostic, 'xdnd native source', owner)

    def _source_enter(self, values):
        source = self.source
        if not source or values[0] != source.owner or source.types:
            return
        if not self._source_current(source):
            self.cancel_source('stale offer', source=source)
            return
        source.version = values[1] >> 24
        if not 3 <= source.version <= 5:
            self.cancel_source('unsupported XDND version', source=source)
            return
        window = self._window(source.owner)
        atoms = self._atoms_property(window, 'XdndTypeList') if values[1] & 1 else tuple(a for a in values[2:5] if a)
        if not atoms or len(atoms) > MAX_TYPES:
            raise ValueError('invalid XDND source offer')
        names = [self.d.get_atom_name(a) for a in atoms]
        if any(not (_TYPE.fullmatch(s) or s in _TEXT_TYPES) for s in names):
            raise ValueError('invalid XDND atom name')
        source.types = _types(' '.join(names).encode('ascii'))
        actions = self._atoms_property(window, 'XdndActionList')
        source.operations_known = bool(actions)
        source.operations = ((1 if self.atoms['XdndActionCopy'] in actions else 0) |
                             (2 if self.atoms['XdndActionMove'] in actions else 0))
        # If ActionList is absent the proposed action in Position is authority.
        if not actions:
            source.operations = 0

    def _source_position(self, values):
        source = self.source
        if not source or values[0] != source.owner or not source.types or source.released or source.native_drop:
            return
        if not self._source_current(source):
            self.cancel_source('stale position', source=source)
            return
        action = 1 if values[4] == self.atoms['XdndActionCopy'] else 2 if values[4] == self.atoms['XdndActionMove'] else 0
        if source.started:
            action = source.action
        if not source.operations and not source.operations_known and action:
            source.operations = action
        accepted = bool(action and action & source.operations)
        self._message(source.owner, 'XdndStatus', [self.catcher.id, 3 if accepted else 2, 0, 0,
            self.atoms['XdndActionCopy' if action == 1 else 'XdndActionMove'] if accepted else 0])
        if not accepted:
            self.cancel_source('unsupported action', source=source)
        elif not source.started:
            source.action = action
            source.started = True
            if not self.send(t='o', x=1) or not self._source_current(source):
                return
            if not self.send(' '.join(source.types).encode('ascii'), t='o', o=source.operations) or not self._source_current(source):
                return
            self.send(t='P', x=-1)

    def _release_native_source(self):
        source = self.source
        if not source or source.released or source.failed or self.closed or self._closing:
            return
        source.released = True
        source.deadline = time.monotonic() + TRANSFER_TIMEOUT
        # Keep the catcher mapped until the real toolkit sends XdndDrop.
        self.injector.move_click(0, 0, button=1, press=False)
        if self.closed or self.source is not source:
            return
        self.injector.release_all()

    def _source_finished(self):
        source = self.source
        if not source or source.finish is None or not source.native_drop:
            return
        action = source.finish
        if (action != source.drop_action or not action & source.operations or
                not self._source_current(source) or self.reading or
                source.requests or self.output or not source.cache):
            source.finish = action = 0
        if self.source is not source:
            return
        if not action:
            self._cancel_native(source)
            if self.closed or self.source is not source:
                return
        if action == 2 and source.delete_supported is None:
            if source.delete_probe is None:
                window = self.root.create_window(-10, -10, 1, 1, 0,
                    X.CopyFromParent, X.InputOnly, X.CopyFromParent)
                if not self._source_current(source):
                    window.destroy()
                    return
                source.delete_probe = window
                window.convert_selection(self.atoms['XdndSelection'], self.atoms['TARGETS'],
                    self.atoms['_KILIX_DND_DATA'], source.timestamp)
                self.d.flush()
            return
        if action == 2 and source.delete_supported and not source.delete_complete:
            if not source.cache:
                source.finish = action = 0
            elif source.delete_window is None:
                # GTK/ICCCM moves request DELETE after the receiving toolkit
                # has confirmed success. Only the original selection owner
                # can emit its native data-delete callback; we never delete a
                # path or relay an unconfirmed destination DELETE request.
                window = self.root.create_window(-10, -10, 1, 1, 0,
                    X.CopyFromParent, X.InputOnly, X.CopyFromParent)
                if not self._source_current(source):
                    window.destroy()
                    return
                source.delete_window = window
                source.delete_window.convert_selection(self.atoms['XdndSelection'],
                    self.atoms['DELETE'], self.atoms['_KILIX_DND_DATA'], source.drop_time)
                self.d.flush()
                return
            else:
                return
        if action == 2 and source.version < 5 and source.delete_supported is False:
            # A legacy MOVE has no result words. The paired consuming client
            # must implement DELETE; lack of that contract never earns MOVE.
            source.finish = action = 0
        values = [self.catcher.id]
        if source.version == 5:
            values += [int(bool(action)),
                self.atoms['XdndActionCopy' if action == 1 else 'XdndActionMove'] if action else 0]
        else:
            self._callback(self.diagnostic, 'xdnd legacy source result not expressible', action)
        # The legacy wire format treats *any* Finished as success. On failure
        # cancel natively and withhold Finished; toolkit timeout is preferable
        # to authorizing a MOVE result. Supported legacy COPY may finish only
        # after an explicitly confirmed non-destructive downstream result.
        if action and not self._source_current(source):
            return
        if self.source is not source:
            return
        if source.version == 5 or action == 1 or action == 2 and source.delete_complete:
            self._message(source.owner, 'XdndFinished', values)
        self._callback(self.diagnostic, 'xdnd source completed', action)
        if self.source is source:
            self._clear_source()

    def _clear_source(self):
        source, read, catcher, target = self.source, self.reading, self.catcher, self.target
        self.source = None
        self.reading = None
        self._clear_output()
        generation = self._output_generation
        if source:
            source.cache.clear()
            source.requests.clear()
            source.drop_action = 0
            if source.delete_probe is not None:
                source.delete_probe.destroy()
            if source.delete_window is not None:
                source.delete_window.destroy()
        if read:
            read['window'].destroy()
        catcher.unmap()
        if (self.closed or self.source is not None or self.target is not target or
                self._output_generation != generation):
            return
        if not self.send(t='o', x=2):
            return
        self.d.flush()

    def cancel_source(self, reason, *, source=None):
        source = self.source if source is None else source
        if not source or self.source is not source or source.canceling:
            return
        source.canceling = True
        try:
            # Revoke export before invoking diagnostics or a reentrant writer.
            source.failed = True
            self._clear_output()
            self._callback(self.diagnostic, 'xdnd source canceled', reason)
            if self.source is not source:
                return
            if not self.send(t='E', y=-1) or self.closed or self.source is not source:
                return
            self._cancel_native(source)
            if self.source is not source:
                return
            if source.native_drop:
                source.finish = 0
                self._source_finished()
            else:
                self._clear_source()
        finally:
            source.canceling = False

    def _cancel_native(self, source):
        if source.cancel_sent:
            return
        source.cancel_sent = True
        # Escape precedes release; a failure never emits legacy success.
        self.injector.chord('Escape', 0, 1)
        if self.closed or self.source is not source:
            return
        self.injector.chord('Escape', 0, 3)
        if self.closed or self.source is not source:
            return
        self.injector.release_all()

    def _read_source(self):
        source = self.source
        # Release and Drop are distinct native events. An engine request can
        # arrive between them; retain its bounded index, not offered bytes.
        if not source or not source.native_drop or self.reading or not source.requests:
            return
        idx = source.requests.popleft()
        if not 0 <= idx < len(source.types) or not self._source_current(source) or not source.drop_time:
            self.cancel_source('invalid source request', source=source)
            return
        name = source.types[idx]
        if name in source.cache:
            self._send_data(source.cache[name], source=source, t='e', y=idx)
            return
        window = self.root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
            X.InputOnly, X.CopyFromParent, event_mask=X.PropertyChangeMask)
        atom = self.d.intern_atom(name)
        if not self._source_current(source):
            window.destroy()
            return
        now = time.monotonic()
        self.reading = {'window': window, 'index': idx, 'type': atom,
                        'source': source,
                        'time': source.drop_time,
                        'data': bytearray(), 'incremental': False, 'announced': 0, 'payload_type': 0,
                        'idle': now + IDLE_TIMEOUT, 'hard': now + TRANSFER_TIMEOUT}
        window.convert_selection(self.atoms['XdndSelection'], atom,
                                 self.atoms['_KILIX_DND_DATA'], source.drop_time)
        self.d.flush()

    def _source_data(self, event, *, incremental=False):
        read, source = self.reading, self.source
        if not source or not source.native_drop or not self._source_current(source):
            return
        if not incremental and source.delete_probe is not None and event.requestor.id == source.delete_probe.id:
            if (event.selection != self.atoms['XdndSelection'] or event.target != self.atoms['TARGETS'] or
                    event.time != source.timestamp):
                return
            value = source.delete_probe.get_property(self.atoms['_KILIX_DND_DATA'],
                X.AnyPropertyType, 0, MAX_TYPES + 4) if event.property == self.atoms['_KILIX_DND_DATA'] else None
            if not self._source_current(source):
                return
            valid = (value is not None and value.property_type == Xatom.ATOM and value.format == 32 and
                     not value.bytes_after and len(value.value) <= MAX_TYPES + 4)
            source.delete_probe.destroy()
            source.delete_probe = None
            if not valid:
                source.finish = 0
            else:
                source.delete_supported = self.atoms['DELETE'] in value.value
            self._source_finished()
            return
        if (source and not incremental and source.delete_window is not None and
                event.requestor.id == source.delete_window.id):
            value = source.delete_window.get_property(self.atoms['_KILIX_DND_DATA'],
                X.AnyPropertyType, 0, 1) if event.property == self.atoms['_KILIX_DND_DATA'] else None
            if not self._source_current(source):
                return
            source.delete_complete = bool(event.selection == self.atoms['XdndSelection'] and
                event.time == source.drop_time and
                _time_after(source.drop_time, source.timestamp) and
                event.target == self.atoms['DELETE'] and value is not None and
                value.property_type == self.atoms['NULL'] and value.format == 8 and
                not value.value and not value.bytes_after)
            if not source.delete_complete:
                source.finish = 0
            self._source_finished()
            return
        if (not read or read['source'] is not source or
                getattr(event.window if incremental else event.requestor, 'id', 0) != read['window'].id):
            return
        prop = self.atoms['_KILIX_DND_DATA']
        if not incremental and (event.selection != self.atoms['XdndSelection'] or event.target != read['type'] or event.time != read['time']):
            return
        if incremental and not _time_after(event.time, read['time']):
            return
        if not incremental and event.property != prop:
            self.cancel_source('source conversion refused', source=source)
            return
        value = read['window'].get_property(prop, X.AnyPropertyType, 0, MAX_BYTES // 4 + 1)
        if not self._source_current(source) or self.reading is not read:
            return
        if value is None:
            return
        if value.bytes_after:
            raise ValueError('source data exceeds limit')
        if not incremental and value.property_type == self.atoms['INCR']:
            if value.format != 32 or len(value.value) != 1 or value.value[0] > MAX_BYTES:
                raise ValueError('invalid source INCR')
            read['announced'] = int(value.value[0])
            read['incremental'] = True
            self._callback(self.diagnostic, 'xdnd source INCR', int(value.value[0]))
            if not self._source_current(source) or self.reading is not read:
                return
            read['window'].delete_property(prop)
            self.d.flush()
            return
        # TEXT can convert to STRING or UTF8_STRING under ICCCM.
        converted = (self.d.get_atom_name(value.property_type) if value.property_type else '')
        if value.format != 8 or (value.property_type != read['type'] and
                not (source.types[read['index']] == 'TEXT' and converted in _TEXT_TYPES)):
            raise ValueError('invalid source data type')
        if not self._source_current(source) or self.reading is not read:
            return
        if read['payload_type'] and read['payload_type'] != value.property_type:
            raise ValueError('source INCR changed payload type')
        read['payload_type'] = value.property_type
        data = bytes(value.value)
        if len(read['data']) + len(data) + sum(map(len, source.cache.values())) > MAX_BYTES:
            raise ValueError('source data exceeds limit')
        read['data'].extend(data)
        read['idle'] = time.monotonic() + IDLE_TIMEOUT
        read['window'].delete_property(prop)
        self.d.flush()
        if not read['incremental'] or not data:
            if read['incremental'] and len(read['data']) < read['announced']:
                raise ValueError('source INCR ended below lower bound')
            if not self._source_current(source) or self.reading is not read:
                return
            data = bytes(read['data'])
            source.cache[source.types[read['index']]] = data
            self._send_data(data, source=source, t='e', y=read['index'])
            read['window'].destroy()
            self.reading = None

    def _point(self, event):
        bx, by, bw, bh = self._callback(self.image_box)
        x, y = event.get('X', -1), event.get('Y', -1)
        if bw <= 0 or bh <= 0 or not bx <= x < bx + bw or not by <= y < by + bh:
            return None
        return (min(self.injector.app_w - 1, max(0, round((x - bx) * self.injector.app_w / bw))),
                min(self.injector.app_h - 1, max(0, round((y - by) * self.injector.app_h / bh))))

    def _find_target(self, point):
        # Query actual stacking/geometry; never reuse a window from a different
        # pane. The InputOnly source catcher is excluded for same-display drops.
        x, y = point
        window = self.root
        path = []
        visits = 0
        for _depth in range(32):
            child = None
            for candidate in reversed(window.query_tree().children):
                visits += 1
                if visits > 512:
                    raise ValueError('XDND window tree exceeds limit')
                if candidate.id in (self.win.id, self.catcher.id):
                    continue
                attrs = candidate.get_attributes()
                if attrs.map_state != X.IsViewable or attrs.win_class == X.InputOnly:
                    continue
                g = candidate.get_geometry()
                origin = self.root.translate_coords(candidate, 0, 0)
                # python-xlib translate_coords is destination.translate_coords(source).
                if origin.x <= x < origin.x + g.width and origin.y <= y < origin.y + g.height:
                    child = candidate
                    break
            if child is None:
                break
            path.append(child)
            window = child
        for candidate in reversed(path):
            prop = self._property(candidate, 'XdndAware', Xatom.ATOM, 4)
            if prop is None:
                continue
            if prop.format != 32 or len(prop.value) != 1 or not 3 <= prop.value[0] <= 5:
                return (0, 0, 5)
            receiver, recipient, version = candidate.id, candidate.id, int(prop.value[0])
            proxy = self._property(candidate, 'XdndProxy', Xatom.WINDOW, 4)
            if proxy is not None:
                if proxy.format != 32 or len(proxy.value) != 1:
                    return (0, 0, 5)
                if not 0 < int(proxy.value[0]) <= 0xffffffff:
                    return (0, 0, 5)
                proxy_window = self._window(int(proxy.value[0]))
                check = self._property(proxy_window, 'XdndProxy', Xatom.WINDOW, 4)
                aware = self._property(proxy_window, 'XdndAware', Xatom.ATOM, 4)
                if (check is None or check.format != 32 or list(check.value) != [proxy_window.id]
                        or aware is None or aware.format != 32 or len(aware.value) != 1
                        or not 3 <= aware.value[0] <= 5):
                    return (0, 0, 5)
                recipient, version = proxy_window.id, int(aware.value[0])
            return (receiver, recipient, version)
        return (0, 0, 5)

    def _leave_target(self):
        target = self.target
        if target and target.receiver and not target.dropped:
            self._message(target.recipient, 'XdndLeave', [target.owner.id], receiver=target.receiver)

    def _position(self):
        target = self.target
        if not target or not target.acquired or target.dropped or not self.position_pending:
            return
        receiver, recipient, version = self._find_target(target.point)
        if not self._target_current(target):
            return
        if (receiver, recipient) != (target.receiver, target.recipient):
            self._leave_target()
            target.receiver, target.recipient, target.version = receiver, recipient, version
            target.action = 0
            target.awaiting = False
            target.sent_position = None
            if not self.send(t='m', o=0) or not self._target_current(target):
                return
            if receiver:
                self._window(receiver).change_attributes(event_mask=X.StructureNotifyMask)
                atoms = [self.d.intern_atom(s) for s in target.types]
                target.owner.change_property(self.atoms['XdndTypeList'], Xatom.ATOM, 32, atoms)
                self._message(recipient, 'XdndEnter', [target.owner.id, version << 24 | (len(atoms) > 3),
                    *atoms[:3]], receiver=receiver)
        if target.awaiting:
            return
        self.position_pending = False
        position = (receiver, recipient, target.point, target.suggested)
        if receiver and position != target.sent_position:
            action = self.atoms['XdndActionCopy' if target.suggested == 1 else 'XdndActionMove'] if target.suggested in (1, 2) else self.atoms['XdndActionLink']
            self._message(recipient, 'XdndPosition', [target.owner.id, 0,
                (target.point[0] & 0xffff) << 16 | (target.point[1] & 0xffff), self.stamp, action],
                receiver=receiver)
            target.awaiting = True
            target.sent_position = position
        elif target.release_pending:
            self._drop_target()

    def _drop_target(self):
        target = self.target
        if not target or target.dropped or target.awaiting or self.position_pending:
            return
        if (not target.receiver or not target.action or not target.action & target.operations
                or not self._target_current(target)
                or not _time_after(self.stamp, target.acquired)):
            self.cancel_target('drop refused', target=target)
            return
        target.dropped = True
        target.stamp = self.stamp
        target.deadline = time.monotonic() + TRANSFER_TIMEOUT
        self._message(target.recipient, 'XdndDrop', [target.owner.id, 0, self.stamp], receiver=target.receiver)
        self._callback(self.diagnostic, 'xdnd target dropped', target.receiver)

    def _target_motion(self, event):
        if event.get('x') == -1 and event.get('y') == -1:
            if self.target and not self.target.dropped:
                self.cancel_target('pointer left')
            return
        source_before, target_before = self.source, self.target
        point = self._point(event)
        if self.closed or self._closing or self.source is not source_before or self.target is not target_before:
            return
        if point is None:
            self.cancel_target('outside image')
            if self.closed or self._closing or self.target is not None:
                return
            self.send(t='m', o=0)
            return
        types = _types(event['payload']) if event['payload'] else self.target.types if self.target else ()
        if not types:
            raise ValueError('empty XDND terminal offer')
        ops = event.get('o', 0) & 3
        suggested = event.get('s', 1 if ops & 1 else 2 if ops & 2 else 0)
        target = self.target
        if self.source:
            self.cancel_target('intra-display bridge drop unsupported')
            if self.closed or self._closing or self.target is not None or self.source is not source_before:
                return
            self.send(t='m', o=0)
            return
        if target and target.dropped:
            return
        if target and target.types != types:
            self.cancel_target('offer replaced', target=target)
            if self.closed or self._closing or self.target is not None:
                return
            target = None
        if target is None:
            target = self.target = _Target(types, ops, suggested)
            target.owner = self.root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
                X.InputOnly, X.CopyFromParent, event_mask=X.PropertyChangeMask)
            # Acquire on the next property timestamp, with a known ownership
            # period. CurrentTime would erase that period (ICCCM 2.1/2.2).
            target.owner.change_property(self.atoms['XdndActionList'], Xatom.ATOM, 32,
                [self.atoms[name] for bit, name in ((1, 'XdndActionCopy'), (2, 'XdndActionMove')) if ops & bit])
        target.operations, target.suggested, target.point = ops, suggested, point
        if event['t'] == 'M':
            # Bind release to the latest native receiver and negotiated action.
            target.release_pending = True
            self.position_pending = True
            self.mark_time()
        else:
            self.position_pending = True
            self.mark_time()

    def _target_status(self, values):
        target = self.target
        if not target or target.dropped or not target.awaiting or values[0] != target.receiver:
            return
        target.awaiting = False
        action = 1 if values[4] == self.atoms['XdndActionCopy'] else 2 if values[4] == self.atoms['XdndActionMove'] else 0
        # Preserve the explicit action. Legacy MOVE completion will additionally
        # require the successful receiver-bound DELETE exchange.
        target.action = action if values[1] & 1 and action & target.operations else 0
        if self.position_pending:
            self._position()
            if target.awaiting or self.target is not target:
                return
        if not self.send(' '.join(target.types).encode('ascii'), t='m', o=target.action) or not self._target_current(target):
            return
        if target.release_pending:
            self._drop_target()

    def _notify(self, event, prop, *, onerror=None):
        if onerror is None:
            target = self.target
            def refused(error, request):
                # A late failed refusal is not a new gesture's delivery error.
                if prop and self.target is target and target is not None:
                    target.failed = True
                return 1
            onerror = refused
        event.requestor.send_event(xevent.SelectionNotify(time=event.time,
            requestor=event.requestor.id, selection=event.selection,
            target=event.target, property=prop), event_mask=X.NoEventMask, onerror=onerror)
        self.d.flush()

    def _receiver_client(self, target, requestor):
        try:
            if not self.d.has_extension('X-Resource'):
                return 0
            version = self.d.res_query_version()
            if (version.server_major, version.server_minor) < (1, 2):
                return 0
            named = tuple(dict.fromkeys((target.receiver, requestor)))
            if any(type(xid) is not int or not 0 < xid <= 0xffffffff for xid in named):
                return 0
            clients = {}
            for xid in named:
                # ClientXid lives in spec.client and has no value payload.
                # Query each named window separately: the server normalizes
                # resource IDs and deduplicates same-client specifications.
                ids = self.d.res_query_client_ids([{'client': xid, 'mask': 1}]).ids
                if len(ids) != 1:
                    return 0
                value = ids[0]
                client = value.spec.client
                if (type(value.spec.mask) is not int or value.spec.mask != 1 or
                        len(value.value) != 0 or type(client) is not int or
                        not 0 < client <= 0xffffffff):
                    return 0
                clients[xid] = client
            a, b = clients.get(target.receiver, 0), clients.get(requestor, 0)
            return a if a and a == b else 0
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, xerror.XError):
            return 0

    def _delete_identity(self, target, client):
        return (target.owner.id, target.acquired, target.receiver, target.recipient,
                target.stamp, target.action, client)

    def _publish_selection(self, event, target, prop, kind, fmt, data, *, onerror=None):
        failed = [False]
        def refused(error, request):
            failed[0] = True
            if onerror is not None:
                onerror(error, request)
            elif self.target is target:
                target.failed = True
        if not self._target_current(target):
            self._notify(event, 0)
            return False
        event.requestor.change_property(prop, kind, fmt, data, onerror=refused)
        self.d.sync()
        if failed[0] or not self._target_current(target):
            self._notify(event, 0)
            return False
        self._notify(event, prop, onerror=refused)
        self.d.sync()
        return not failed[0] and self._target_current(target)

    def _serve_target(self, event, *, now=None):
        target = self.target
        if (not target or
                event.selection != self.atoms['XdndSelection'] or
                not self._target_current(target) or event.owner.id != target.owner.id):
            self._notify(event, 0)
            return
        if now is None:
            # A fresh property timestamp bounds the current ownership period.
            # CurrentTime carries no epoch and is refused, as ICCCM requestors
            # must use the triggering event's timestamp. Never substitute zero.
            if event.time == X.CurrentTime or len(target.time_requests) >= MAX_REQUESTS:
                self._notify(event, 0)
                return
            clock = self.root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
                X.InputOnly, X.CopyFromParent, event_mask=X.PropertyChangeMask)
            target.time_requests.append((event, clock))
            clock.change_property(self.atoms['_KILIX_DND_TIME'], Xatom.INTEGER, 8, b'1')
            self.d.flush()
            return
        if not _time_in_period(event.time, target.acquired, now):
            self._notify(event, 0)
            return
        prop = event.property or event.target
        if event.target == self.atoms['TARGETS']:
            names = [self.d.intern_atom(s) for s in target.types]
            names += [self.atoms['TARGETS'], self.atoms['TIMESTAMP']]
            if target.operations & 2:
                names.append(self.atoms['DELETE'])
            self._publish_selection(event, target, prop, Xatom.ATOM, 32, names)
            return
        if event.target == self.atoms['TIMESTAMP']:
            self._publish_selection(event, target, prop, Xatom.INTEGER, 32, [target.acquired])
            return
        if not target.dropped or not target.action or not _time_after(event.time, target.stamp):
            self._notify(event, 0)
            return
        if event.target == self.atoms['DELETE']:
            # GTK requests DELETE only after a successful MOVE callback. Its
            # IPC requestor can differ from the Xdnd target: resolve their
            # server client identity, then refresh the epoch after that query.
            client = self._receiver_client(target, event.requestor.id)
            if (target.action != 2 or not client or not self._delivery_complete(target) or
                    target.requests or self.outgoing or target.reading >= 0):
                self._notify(event, 0)
            elif not any(d.delivered and not d.failed and
                         self._receiver_client(target, d.event.requestor.id) == client
                         for d in target.deliveries):
                self._notify(event, 0)
            elif self._publish_selection(event, target, prop, self.atoms['NULL'], 8, b''):
                target.delete_receipt = self._delete_identity(target, client)
            return
        name = self.d.get_atom_name(event.target)
        if not self._target_current(target):
            self._notify(event, 0)
            return
        if (name not in target.types or len(target.deliveries) >= MAX_REQUESTS
                or any(r.requestor.id == event.requestor.id and (r.property or r.target) == prop for r in target.requests)
                or (event.requestor.id, prop) in self.outgoing):
            self._notify(event, 0)
            return
        if any(d.event.requestor.id == event.requestor.id and
               (d.event.property or d.event.target) == prop and
               (not _time_after(event.time, d.time) or event.time == d.time)
               for d in target.deliveries):
            self._notify(event, 0)
            return
        delivery = _Delivery(target, event, event.time)
        target.deliveries.append(delivery)
        if name in target.cache:
            self._serve_data(event, target.cache[name], delivery=delivery)
            return
        target.requests.append(event)
        self._request_target_data()

    def _request_target_data(self):
        target = self.target
        if (not target or not target.dropped or not target.action or target.reading >= 0
                or not target.requests or not self._target_current(target)):
            return
        event = target.requests[0]
        idx = target.types.index(self.d.get_atom_name(event.target))
        if not self._target_current(target):
            return
        target.reading = idx
        target.data.clear()
        now = time.monotonic()
        target.idle, target.hard = now + IDLE_TIMEOUT, now + TRANSFER_TIMEOUT
        self.send(t='r', x=idx + 1)

    def _serve_data(self, event, data, *, delivery=None):
        target = self.target
        if (not target or not target.dropped or not target.action or
                not self._target_current(target) or
                event.owner.id != target.owner.id or
                event.selection != self.atoms['XdndSelection'] or
                self.d.get_atom_name(event.target) not in target.types or
                not _time_in_period(event.time, target.acquired, self.stamp) or
                not _time_after(event.time, target.stamp)):
            self._notify(event, 0)
            return
        if not self._target_current(target):
            self._notify(event, 0)
            return
        prop = event.property or event.target
        if delivery is None:
            delivery = next((d for d in target.deliveries if d.event is event and d.time == event.time
                             and not d.delivered and not d.failed), None)
        if delivery is None:
            if len(target.deliveries) >= MAX_REQUESTS:
                self._notify(event, 0)
                return
            delivery = _Delivery(target, event, event.time)
            target.deliveries.append(delivery)
        failed = self._delivery_error(delivery)
        if len(data) <= self.chunk:
            delivery.delivered = self._publish_selection(event, target, prop, event.target, 8, data, onerror=failed)
        else:
            self._callback(self.diagnostic, 'xdnd target INCR', len(data))
            if not self._target_current(target):
                return
            event.requestor.change_attributes(event_mask=X.PropertyChangeMask | X.StructureNotifyMask, onerror=failed)
            if not self._target_current(target):
                self._notify(event, 0)
                return
            self.outgoing[event.requestor.id, prop] = {'window': event.requestor,
                'generation': target,
                'delivery': delivery, 'time': delivery.time,
                'type': event.target, 'data': data, 'offset': 0,
                'idle': time.monotonic() + IDLE_TIMEOUT, 'hard': time.monotonic() + TRANSFER_TIMEOUT}
            self._publish_selection(event, target, prop, self.atoms['INCR'], 32, [len(data)], onerror=failed)

    def _target_data(self, event):
        target = self.target
        if (not target or not target.dropped or not target.action or
                not self._target_current(target) or target.reading < 0
                or event.get('x') != target.reading + 1):
            return
        if event['t'] == 'R':
            self.cancel_target('terminal conversion failed', target=target)
            return
        encoded = event['payload']
        # OSC 72 permits omitted RFC 4648 padding; each engine chunk is a
        # complete encoding, including the shorter final chunk.
        data = base64.b64decode(encoded + b'=' * (-len(encoded) % 4), validate=True)
        if len(data) + len(target.data) + sum(map(len, target.cache.values())) > MAX_BYTES:
            raise ValueError('target payload exceeds limit')
        target.data.extend(data)
        target.idle = time.monotonic() + IDLE_TIMEOUT
        if not event['payload'] and not event.get('m', 0):
            data = bytes(target.data)
            name = target.types[target.reading]
            target.cache[name] = data
            remaining = deque()
            for request in target.requests:
                if self.d.get_atom_name(request.target) == name:
                    self._serve_data(request, data)
                    if self.target is not target:
                        return
                else:
                    remaining.append(request)
            target.requests = remaining
            target.reading = -1
            target.data.clear()
            self._request_target_data()

    def _target_finished(self, values):
        target = self.target
        if not target or not target.dropped or values[0] != target.receiver:
            return
        action = 0
        if target.version == 5:
            if values[1] & 1:
                action = 1 if values[2] == self.atoms['XdndActionCopy'] else 2 if values[2] == self.atoms['XdndActionMove'] else 0
        elif target.action == 1:
            # Legacy Finished has reserved-zero fields. For non-destructive
            # COPY, receipt from the selected receiver after every requested
            # payload completed is the ordinary toolkit completion signal.
            # Keep the selected action; never infer MOVE from these words.
            action = 1
        elif target.action == 2 and target.delete_receipt:
            # Legacy reserved words carry no result. Revalidate the named
            # receiver's client and the exact prior affirmative DELETE receipt.
            client = self._receiver_client(target, target.receiver)
            if target.delete_receipt == self._delete_identity(target, client):
                action = 2
        if (action != target.action or not self._delivery_complete(target) or self.outgoing or target.requests
                or target.reading >= 0):
            action = 0
        if self.target is not target:
            return
        if target.version < 5 and not action:
            self._callback(self.diagnostic, 'xdnd legacy completion unconfirmed', target.version)
        if action and not self._target_current(target):
            return
        if self.target is not target:
            return
        self.send(t='r', o=action)
        if self.target is not target:
            return
        self._callback(self.diagnostic, 'xdnd target completed', action)
        if self.target is target:
            self._clear_target()

    def _clear_target(self):
        target = self.target
        self.target = None
        self.position_pending = False
        self.outgoing.clear()
        if target:
            for event, clock in target.time_requests:
                self._notify(event, 0)
                clock.destroy()
            for event in target.requests:
                self._notify(event, 0)
            target.time_requests.clear()
            target.requests.clear()
        # Endpoint destruction lets the server relinquish only a selection
        # still owned by this window. No query-to-clear race can erase a new
        # lifetime (including equal-word ownership) during cleanup.
        if target:
            target.owner.destroy()
        if not self.closed:
            self.d.flush()

    def cancel_target(self, reason, *, target=None):
        target = self.target if target is None else target
        if not target or self.target is not target or target.canceling:
            return
        target.canceling = target.failed = True
        try:
            self._callback(self.diagnostic, 'xdnd target canceled', reason)
            if self.target is not target:
                return
            if target.dropped:
                self.send(t='r', o=0)
            else:
                self._leave_target()
                if self.target is not target:
                    return
                self.send(t='m', o=0)
            if self.target is target:
                self._clear_target()
        finally:
            target.canceling = False

    def handle(self, event):
        if self.closed or self._closing:
            return
        source_before, target_before = self.source, self.target
        try:
            kind = event['t']
            if kind == 'q':
                # New engine advertises action-aware, bounded native transport.
                if b'native=1' not in event['payload'].split(b':'):
                    self._callback(self.diagnostic, 'xdnd disabled', 'engine lacks native bridge capability')
                    return
                self.enabled = True
                self.send(t='a')
            elif self.enabled and kind in ('m', 'M'):
                self._target_motion(event)
            elif self.enabled and kind in ('r', 'R'):
                self._target_data(event)
            elif kind == 'E' and event['payload'] != b'OK':
                self.cancel_source('engine drag error')
            elif kind == 'e' and self.source:
                source = self.source
                code = event.get('x', 0)
                if code == 2:
                    action = event.get('o', 0)
                    if self.source.released or self.source.native_drop:
                        return
                    if action in (1, 2) and action & self.source.operations:
                        self.source.action = action
                        pointer = self.root.query_pointer()
                        if not self._source_current(source):
                            return
                        xtest.fake_input(self.d, X.MotionNotify, x=pointer.root_x, y=pointer.root_y)
                        self._message(source.owner, 'XdndStatus', [self.catcher.id, 3, 0, 0,
                            self.atoms['XdndActionCopy' if action == 1 else 'XdndActionMove']])
                elif code == 3:
                    self._release_native_source()
                elif code == 4:
                    action = event.get('o', 0)
                    if event.get('y', 1) or action not in (1, 2) or not action & self.source.operations:
                        self.cancel_source('physical drag refused or canceled')
                    else:
                        source = self.source
                        source.finish = action
                        self._release_native_source()
                        if not self.closed and self.source is source:
                            self._source_finished()
                elif code == 5:
                    if not self.source.released:
                        self.cancel_source('pre-Drop payload request')
                        return
                    if len(self.source.requests) >= MAX_REQUESTS:
                        raise ValueError('too many source requests')
                    self.source.requests.append(event.get('y', -1))
        except (ValueError, UnicodeError, xerror.XError) as error:
            source_error, target_error = getattr(error, '_xdnd_callback_lifetime', (self.source, self.target))
            self._callback(self.diagnostic, 'xdnd protocol error', str(error))
            if source_error is not None:
                self.cancel_source('protocol error', source=source_error)
            if target_error is not None:
                self.cancel_target('protocol error', target=target_error)

    def _event(self, event):
        if event.type == self.selection_event and event.selection == self.atoms['XdndSelection']:
            self._native_owner_changed(event)
        elif event.type == X.ClientMessage:
            if event.data[0] != 32:
                return
            name = self.names.get(event.client_type)
            values = list(event.data[1])
            if event.window.id == self.catcher.id:
                if name == 'XdndEnter':
                    self._source_enter(values)
                elif name == 'XdndPosition':
                    self._source_position(values)
                elif (name == 'XdndDrop' and self.source and self.source.started and
                      self.source.released and not self.source.native_drop and values[0] == self.source.owner and
                      self._source_current(self.source)):
                    if not _time_after(values[2], self.source.timestamp):
                        self.cancel_source('invalid native Drop epoch/action')
                        return
                    self.source.drop_time = values[2] & 0xffffffff
                    self.source.drop_action = self.source.action
                    self.source.native_drop = True
                    self._source_finished()
                elif name == 'XdndLeave' and self.source and not self.source.released:
                    if not self.source.started:
                        self.cancel_source('native drag left')
                    else:
                        # A RandR/toolkit target-cache change can emit Leave
                        # while the physical drag still owns the selection.
                        # Re-enter the catch surface; selection loss remains
                        # the native cancellation authority.
                        self.resize()
                        xtest.fake_input(self.d, X.MotionNotify, x=1, y=1)
                        self.d.flush()
            elif self.target and event.window.id == self.target.owner.id:
                if name == 'XdndStatus':
                    self._target_status(values)
                elif name == 'XdndFinished':
                    self._target_finished(values)
        elif event.type == X.SelectionRequest:
            self._serve_target(event)
        elif event.type == X.SelectionNotify:
            self._source_data(event)
        elif event.type == X.PropertyNotify:
            target = self.target
            timed = next(((request, clock) for request, clock in target.time_requests
                          if clock.id == event.window.id), None) if target else None
            if timed and event.atom == self.atoms['_KILIX_DND_TIME'] and event.state == X.PropertyNewValue:
                request, clock = timed
                target.time_requests.remove(timed)
                clock.destroy()
                self.stamp = event.time & 0xffffffff
                self._serve_target(request, now=self.stamp)
            elif (event.atom == self.atoms['_KILIX_DND_TIME'] and event.state == X.PropertyNewValue and
                    (event.window.id == self.win.id or target and not target.acquired and event.window.id == target.owner.id)):
                self.stamp = event.time
                if target and not target.acquired and event.window.id == target.owner.id:
                    if not event.time:
                        self.cancel_target('unknown acquisition timestamp', target=target)
                        return
                    target.acquired = event.time & 0xffffffff
                    target.owner.set_selection_owner(self.atoms['XdndSelection'], target.acquired)
                    self.d.sync()
                    if self._selection_owner() != target.owner.id:
                        self.cancel_target('selection acquisition failed', target=target)
                        return
                    self._selection_stamp = target.acquired
                self._position()
            elif (self.reading and self.reading['incremental'] and event.state == X.PropertyNewValue
                    and event.atom == self.atoms['_KILIX_DND_DATA']):
                self._source_data(event, incremental=True)
            elif event.state == X.PropertyDelete:
                key = (event.window.id, event.atom)
                transfer = self.outgoing.get(key)
                if transfer:
                    target = self.target
                    if (not target or transfer['generation'] is not target or not target.dropped
                            or not target.action or not self._target_current(target)):
                        if self.outgoing.get(key) is transfer:
                            self.outgoing.pop(key, None)
                        return
                    # Ignore deletes queued before this Drop, including a
                    # previous gesture using the same toolkit property.
                    if not _time_after(event.time, transfer['time']):
                        return
                    data = transfer['data'][transfer['offset']:transfer['offset'] + self.chunk]
                    delivery = transfer['delivery']
                    transfer['window'].change_property(event.atom, transfer['type'], 8, data,
                        onerror=self._delivery_error(delivery))
                    self.d.sync()
                    if not self._target_current(target) or self.outgoing.get(key) is not transfer:
                        return
                    transfer['offset'] += len(data)
                    transfer['idle'] = time.monotonic() + IDLE_TIMEOUT
                    if not data:
                        delivery.delivered = not delivery.failed and self._target_current(target)
                        if self.outgoing.get(key) is transfer:
                            self.outgoing.pop(key, None)
                    self.d.flush()
        elif event.type == X.DestroyNotify:
            if self.source and event.window.id == self.source.owner:
                self.cancel_source('source closed')
            if self.target and self._bound_peer(event.window.id):
                self.cancel_target('target closed')
        elif event.type == X.SelectionClear and self.target and event.window.id == self.target.owner.id:
            self.cancel_target('selection lost')

    def tick(self):
        if self.closed or self._closing or self._ticking:
            return
        self._ticking = True
        try:
            # Replies can queue extension events even when select() says quiet.
            for _ in range(256):
                if self._deferred_events:
                    self._event(self._deferred_events.popleft())
                    if self.closed or self._closing:
                        return
                    continue
                if not self.d.pending_events():
                    break
                self._event(self.d.next_event())
                if self.closed or self._closing:
                    return
            now = time.monotonic()
            source = self.source
            if source and (now > source.deadline or
                    not self._source_current(source) or self.reading and
                    (now > self.reading['idle'] or now > self.reading['hard'])):
                self.cancel_source('source timeout or owner lost', source=source)
            if self.closed or self._closing:
                return
            target = self.target
            if target and (now > target.deadline or target.reading >= 0 and
                    (now > target.idle or now > target.hard) or
                    any(now > r['idle'] or now > r['hard'] for r in self.outgoing.values())):
                self.cancel_target('target timeout', target=target)
            if self.closed or self._closing:
                return
            self._read_source()
            budget = 65536
            while self.output and budget > 0:
                entry = self.output[0]
                if (not self._output_current(entry) or not entry.source.native_drop or
                        not self._source_current(entry.source) or not self._output_current(entry)):
                    break
                part = entry.data[entry.offset:entry.offset + 4096]
                # Leave the exact entry queued across write. Nested dispatch
                # may revoke it; no popped tuple is ever restored afterwards.
                if not self.send(part, **entry.meta, m=int(bool(part))):
                    return
                if not self._output_current(entry):
                    return
                if part:
                    entry.offset += len(part)
                    budget -= len(part)
                    self.output_bytes -= len(part)
                else:
                    self.output.popleft()
            if self.closed or self._closing:
                return
            self.d.flush()
        except (xerror.ConnectionClosedError, OSError) as error:
            raise EOFError('private XDND server disconnected') from error
        except (ValueError, UnicodeError, xerror.XError) as error:
            source, target = getattr(error, '_xdnd_callback_lifetime', (self.source, self.target))
            self._callback(self.diagnostic, 'xdnd event error', str(error))
            if source is not None:
                self.cancel_source('X protocol failure', source=source)
            if target is not None:
                self.cancel_target('X protocol failure', target=target)
        finally:
            self._ticking = False

    def close(self):
        if self.closed or self._closing:
            return
        self._closing = True
        self.enabled = False
        self._clear_output()
        try:
            self.cancel_source('session closed')
            if self.closed:
                return
            self.cancel_target('session closed')
            self.send(t='A')
            self.send(t='o', x=2)
            self.d.flush()
        finally:
            self.closed = True
            self.source = self.target = self.reading = None
            self.output.clear()
            self.output_bytes = 0
            self.outgoing.clear()
            self.d.close()


__all__ = ['NativeDndBridge', 'parse_message']
