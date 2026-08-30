"""Event-driven text selection bridge for nested Wayland sessions.

A Kilix provider owns the X display; :class:`kilix_sdk.wayland.NestedWaylandSession`
owns a private Weston inside it.  A Wayland client of that nested compositor and
an X client of the provider display therefore hold two unrelated selections, and
nothing in the stack carried text between them.  This module is that carrier.

Both halves are interrupt-driven and neither one has a timer:

* the X half arms XFIXES ``SetSelectionOwnerNotify`` and then only ever reacts to
  X events already delivered on the display descriptor;
* the Wayland half blocks on its compositor socket and reacts to
  ``wl_data_device.selection``.

An idle bridge therefore performs no wakeups of its own, which is the property
the 0.2.1 feasibility gate names.  Transfers are text only, are bounded by
:data:`MAX_SELECTION_BYTES`, and are refused rather than truncated when a peer
offers more than that.
"""

from __future__ import annotations

import os
import selectors
import threading
from dataclasses import dataclass

from . import _wayland
from ._wayland import Encoder, WaylandConnection, WaylandProtocolError


MAX_SELECTION_BYTES = 128 * 1024

#: Offered to Wayland peers, most specific first.
WAYLAND_TEXT_MIME_TYPES = (
    "text/plain;charset=utf-8",
    "text/plain",
    "UTF8_STRING",
    "STRING",
)

#: Accepted from Wayland peers, in preference order.
_WAYLAND_ACCEPTED = (
    "text/plain;charset=utf-8",
    "text/plain;charset=UTF-8",
    "UTF8_STRING",
    "text/plain",
    "STRING",
    "TEXT",
)

_WL_DISPLAY = 1
_DISPLAY_SYNC = 0
_DISPLAY_GET_REGISTRY = 1
_DISPLAY_ERROR = 0
_DISPLAY_DELETE_ID = 1

_REGISTRY_BIND = 0
_REGISTRY_GLOBAL = 0
_REGISTRY_GLOBAL_REMOVE = 1

_CALLBACK_DONE = 0

_SEAT_CAPABILITIES = 0
_SEAT_NAME = 1

_MANAGER_CREATE_DATA_SOURCE = 0
_MANAGER_GET_DATA_DEVICE = 1

_DEVICE_SET_SELECTION = 1
_DEVICE_DATA_OFFER = 0
_DEVICE_ENTER = 1
_DEVICE_LEAVE = 2
_DEVICE_MOTION = 3
_DEVICE_DROP = 4
_DEVICE_SELECTION = 5

_OFFER_RECEIVE = 1
_OFFER_DESTROY = 2
_OFFER_OFFER = 0
_OFFER_SOURCE_ACTIONS = 1
_OFFER_ACTION = 2

_SOURCE_OFFER = 0
_SOURCE_DESTROY = 1
_SOURCE_TARGET = 0
_SOURCE_SEND = 1
_SOURCE_CANCELLED = 2

_SEAT_VERSION = 2
_MANAGER_VERSION = 3


class SelectionBridgeError(RuntimeError):
    """The selection bridge could not be established or lost a required peer."""


@dataclass(frozen=True)
class BridgeCounters:
    """What the bridge actually carried, for evidence rather than decoration."""

    wayland_to_x: int = 0
    x_to_wayland: int = 0
    refused_oversize: int = 0

    def record(self) -> dict[str, int]:
        return {
            "wayland_to_x": self.wayland_to_x,
            "x_to_wayland": self.x_to_wayland,
            "refused_oversize": self.refused_oversize,
        }


class Reactor:
    """One selector shared by both halves of a bridge."""

    def __init__(self) -> None:
        self._selector = selectors.DefaultSelector()
        self._callbacks: dict[int, object] = {}

    def register(self, fd: int, events: int, callback) -> None:
        fd = int(fd)
        if fd in self._callbacks:
            self._selector.modify(fd, events, callback)
        else:
            self._selector.register(fd, events, callback)
        self._callbacks[fd] = callback

    def unregister(self, fd: int) -> None:
        fd = int(fd)
        if fd not in self._callbacks:
            return
        del self._callbacks[fd]
        try:
            self._selector.unregister(fd)
        except (KeyError, ValueError, OSError):
            pass

    def wait(self, timeout: float | None = None) -> int:
        fired = 0
        for key, events in self._selector.select(timeout):
            key.data(int(key.fd), events)
            fired += 1
        return fired

    def close(self) -> None:
        self._callbacks.clear()
        self._selector.close()


class WaylandSelectionEndpoint:
    """Read and publish the text selection of one nested compositor."""

    def __init__(
        self,
        socket_path: str,
        reactor: Reactor,
        *,
        on_text=None,
        on_refused=None,
        connection: WaylandConnection | None = None,
    ) -> None:
        self.socket_path = str(socket_path)
        self._reactor = reactor
        self._on_text = on_text
        self._on_refused = on_refused
        self._connection = connection or WaylandConnection(self.socket_path)
        self._handlers: dict[int, object] = {}
        self._globals: dict[str, tuple[int, int]] = {}
        self._registry = 0
        self._seat = 0
        self._device = 0
        self._manager = 0
        self._offers: dict[int, set[str]] = {}
        self._selection_offer = 0
        self._source = 0
        self._published: str | None = None
        self._serial = 0
        self._reads: dict[int, dict] = {}
        self._writes: dict[int, dict] = {}
        self.ready = False
        self._closed = False
        self._reactor.register(
            self._connection.fileno(), selectors.EVENT_READ, self._readable
        )

    # -- connection plumbing -------------------------------------------------

    def fileno(self) -> int:
        return self._connection.fileno()

    def _send(self, object_id: int, opcode: int, encoder: Encoder | None = None) -> None:
        self._connection.send(object_id, opcode, encoder)

    def _readable(self, _fd: int, _events: int) -> None:
        self.pump()

    def pump(self) -> None:
        """Frame and dispatch everything the compositor has written."""
        for message in self._connection.read():
            handler = self._handlers.get(message.object_id)
            decoder = self._connection.decoder(message)
            if message.object_id == _WL_DISPLAY:
                self._display_event(message.opcode, decoder)
            elif handler is not None:
                handler(message.opcode, decoder)
        if self._connection.closed:
            raise SelectionBridgeError("nested compositor closed the connection")

    def _display_event(self, opcode: int, decoder) -> None:
        if opcode == _DISPLAY_ERROR:
            object_id = decoder.object()
            code = decoder.uint()
            message = decoder.string() or ""
            raise SelectionBridgeError(
                f"nested compositor rejected object {object_id} "
                f"(code {code}): {message}"
            )
        if opcode == _DISPLAY_DELETE_ID:
            self._handlers.pop(decoder.uint(), None)

    # -- handshake -----------------------------------------------------------

    def connect(self) -> None:
        """Bind the seat and data device, blocking only on compositor replies."""
        if self.ready:
            return
        self._registry = self._connection.allocate()
        self._handlers[self._registry] = self._registry_event
        self._send(_WL_DISPLAY, _DISPLAY_GET_REGISTRY, Encoder().new_id(self._registry))
        self._roundtrip()
        for interface in ("wl_seat", "wl_data_device_manager"):
            if interface not in self._globals:
                raise SelectionBridgeError(
                    f"nested compositor does not advertise {interface}"
                )
        self._seat = self._bind("wl_seat", _SEAT_VERSION, self._seat_event)
        self._manager = self._bind("wl_data_device_manager", _MANAGER_VERSION, None)
        self._device = self._connection.allocate()
        self._handlers[self._device] = self._device_event
        self._send(
            self._manager,
            _MANAGER_GET_DATA_DEVICE,
            Encoder().new_id(self._device).object(self._seat),
        )
        self._roundtrip()
        self.ready = True

    def _bind(self, interface: str, version: int, handler) -> int:
        name, advertised = self._globals[interface]
        selected = min(int(advertised), int(version))
        identifier = self._connection.allocate()
        if handler is not None:
            self._handlers[identifier] = handler
        self._send(
            self._registry,
            _REGISTRY_BIND,
            Encoder().uint(name).string(interface).uint(selected).new_id(identifier),
        )
        return identifier

    def _roundtrip(self, timeout: float = 5.0) -> None:
        done = []
        callback = self._connection.allocate()

        def finish(opcode, decoder):
            if opcode == _CALLBACK_DONE:
                decoder.uint()
                done.append(True)

        self._handlers[callback] = finish
        self._send(_WL_DISPLAY, _DISPLAY_SYNC, Encoder().new_id(callback))
        selector = selectors.DefaultSelector()
        selector.register(self._connection.fileno(), selectors.EVENT_READ)
        try:
            while not done:
                if not selector.select(timeout):
                    raise SelectionBridgeError(
                        "nested compositor did not answer within "
                        f"{timeout:g} seconds"
                    )
                self.pump()
        finally:
            selector.close()
            self._handlers.pop(callback, None)

    def _registry_event(self, opcode: int, decoder) -> None:
        if opcode == _REGISTRY_GLOBAL:
            name = decoder.uint()
            interface = decoder.string() or ""
            version = decoder.uint()
            self._globals[interface] = (name, version)
        elif opcode == _REGISTRY_GLOBAL_REMOVE:
            decoder.uint()

    def _seat_event(self, opcode: int, decoder) -> None:
        if opcode == _SEAT_CAPABILITIES:
            decoder.uint()
        elif opcode == _SEAT_NAME:
            decoder.string()

    # -- selection in --------------------------------------------------------

    def _device_event(self, opcode: int, decoder) -> None:
        if opcode == _DEVICE_DATA_OFFER:
            offer = decoder.new_id()
            self._offers[offer] = set()
            self._handlers[offer] = self._make_offer_handler(offer)
        elif opcode == _DEVICE_ENTER:
            self._note_serial(decoder.uint())
        elif opcode == _DEVICE_SELECTION:
            self._selection(decoder.object())
        elif opcode == _DEVICE_MOTION:
            decoder.uint()
        # leave and drop carry no arguments this bridge reads.

    def _make_offer_handler(self, offer: int):
        def handle(opcode: int, decoder) -> None:
            if opcode == _OFFER_OFFER:
                mime = decoder.string()
                if mime:
                    self._offers.setdefault(offer, set()).add(mime)
            elif opcode in (_OFFER_SOURCE_ACTIONS, _OFFER_ACTION):
                decoder.uint()

        return handle

    def _note_serial(self, serial: int) -> None:
        if serial > self._serial:
            self._serial = int(serial)

    def _selection(self, offer: int) -> None:
        previous = self._selection_offer
        self._selection_offer = offer
        if previous and previous != offer:
            self._destroy_offer(previous)
        if not offer:
            return
        mimes = self._offers.get(offer, set())
        chosen = next((mime for mime in _WAYLAND_ACCEPTED if mime in mimes), None)
        if chosen is None:
            return
        read_fd, write_fd = os.pipe()
        try:
            os.set_blocking(read_fd, False)
            # The compositor writes into its own duplicate of write_fd; keeping
            # ours open would hold the pipe open past the peer's EOF.
            self._send(offer, _OFFER_RECEIVE, Encoder().string(chosen).fd(write_fd))
        except Exception:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        self._reads[read_fd] = {"chunks": [], "size": 0, "offer": offer}
        self._reactor.register(read_fd, selectors.EVENT_READ, self._transfer_readable)

    def _transfer_readable(self, fd: int, _events: int) -> None:
        state = self._reads.get(fd)
        if state is None:
            return
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if chunk:
            state["size"] += len(chunk)
            if state["size"] > MAX_SELECTION_BYTES:
                self._finish_read(fd, refused=True)
                return
            state["chunks"].append(chunk)
            return
        self._finish_read(fd, refused=False)

    def _finish_read(self, fd: int, *, refused: bool) -> None:
        state = self._reads.pop(fd, None)
        self._reactor.unregister(fd)
        try:
            os.close(fd)
        except OSError:
            pass
        if state is None:
            return
        if refused:
            if self._on_refused:
                self._on_refused("wayland", state["size"])
            return
        text = b"".join(state["chunks"]).decode("utf-8", "replace")
        if text == self._published:
            # Our own published selection came back as an offer; ignore it
            # rather than round-tripping it into a loop.
            return
        if self._on_text:
            self._on_text(text)

    def _destroy_offer(self, offer: int) -> None:
        self._offers.pop(offer, None)
        if self._handlers.pop(offer, None) is not None:
            try:
                self._send(offer, _OFFER_DESTROY)
            except WaylandProtocolError:
                pass

    # -- selection out -------------------------------------------------------

    def publish(self, text: str) -> None:
        """Own the compositor selection and serve ``text`` to its clients."""
        if not self.ready:
            raise SelectionBridgeError("Wayland endpoint is not connected")
        payload = text.encode("utf-8")
        if len(payload) > MAX_SELECTION_BYTES:
            if self._on_refused:
                self._on_refused("x", len(payload))
            return
        self._published = text
        previous = self._source
        self._source = self._connection.allocate()
        self._handlers[self._source] = self._make_source_handler(self._source, payload)
        self._send(
            self._manager, _MANAGER_CREATE_DATA_SOURCE, Encoder().new_id(self._source)
        )
        for mime in WAYLAND_TEXT_MIME_TYPES:
            self._send(self._source, _SOURCE_OFFER, Encoder().string(mime))
        self._serial += 1
        self._send(
            self._device,
            _DEVICE_SET_SELECTION,
            Encoder().object(self._source).uint(self._serial),
        )
        if previous:
            self._handlers.pop(previous, None)
            try:
                self._send(previous, _SOURCE_DESTROY)
            except WaylandProtocolError:
                pass

    def _make_source_handler(self, source: int, payload: bytes):
        def handle(opcode: int, decoder) -> None:
            if opcode == _SOURCE_SEND:
                decoder.string()
                fd = decoder.fd()
                self._start_write(fd, payload)
            elif opcode == _SOURCE_TARGET:
                decoder.string()
            elif opcode == _SOURCE_CANCELLED:
                if self._source == source:
                    self._source = 0
                    self._published = None
                self._handlers.pop(source, None)

        return handle

    def _start_write(self, fd: int, payload: bytes) -> None:
        try:
            os.set_blocking(fd, False)
        except OSError:
            try:
                os.close(fd)
            except OSError:
                pass
            return
        self._writes[fd] = {"payload": payload, "offset": 0}
        self._reactor.register(fd, selectors.EVENT_WRITE, self._transfer_writable)

    def _transfer_writable(self, fd: int, _events: int) -> None:
        state = self._writes.get(fd)
        if state is None:
            return
        payload = state["payload"]
        try:
            written = os.write(fd, payload[state["offset"]:])
        except BlockingIOError:
            return
        except OSError:
            written = -1
        if written >= 0:
            state["offset"] += written
            if state["offset"] < len(payload):
                return
        self._writes.pop(fd, None)
        self._reactor.unregister(fd)
        try:
            os.close(fd)
        except OSError:
            pass

    # -- teardown ------------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in list(self._reads):
            self._reactor.unregister(fd)
            self._reads.pop(fd, None)
            try:
                os.close(fd)
            except OSError:
                pass
        for fd in list(self._writes):
            self._reactor.unregister(fd)
            self._writes.pop(fd, None)
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            self._reactor.unregister(self._connection.fileno())
        except (OSError, ValueError):
            pass
        self._connection.close()

    def __enter__(self) -> "WaylandSelectionEndpoint":
        return self

    def __exit__(self, _kind, _value, _traceback) -> None:
        self.close()


__all__ = [
    "BridgeCounters",
    "MAX_SELECTION_BYTES",
    "Reactor",
    "SelectionBridgeError",
    "WAYLAND_TEXT_MIME_TYPES",
    "WaylandSelectionEndpoint",
]


try:  # pragma: no cover - import guard, exercised by the absence test
    from Xlib import X, Xatom
    from Xlib import display as _xdisplay
    from Xlib.ext import xfixes as _xfixes
    from Xlib.protocol import event as _xevent
except ImportError:  # pragma: no cover
    X = Xatom = _xdisplay = _xfixes = _xevent = None


_INCR_CHUNK_HINT = 4096


class XSelectionEndpoint:
    """Read and publish one X selection on the provider-owned display."""

    def __init__(
        self,
        reactor: Reactor,
        *,
        display: str | None = None,
        xauthority: str | None = None,
        selection: str = "CLIPBOARD",
        on_text=None,
        on_refused=None,
        connection=None,
    ) -> None:
        if _xdisplay is None and connection is None:
            raise SelectionBridgeError(
                "python-xlib is required for the X half of the selection bridge"
            )
        self._reactor = reactor
        self._on_text = on_text
        self._on_refused = on_refused
        self._closed = False
        self._owned: str | None = None
        self._pending = False
        self._restart = False
        self._incr: list[bytes] | None = None
        self._incr_size = 0
        if connection is not None:
            self._display = connection
        else:
            from .xapp import _temporary_xauthority

            authority = xauthority if xauthority is not None else os.environ.get(
                "XAUTHORITY"
            )
            selected = display or os.environ.get("DISPLAY")
            if not selected:
                raise SelectionBridgeError("an X DISPLAY is required")
            if authority:
                with _temporary_xauthority(authority):
                    self._display = _xdisplay.Display(selected)
            else:
                self._display = _xdisplay.Display(selected)
        if not self._display.has_extension("XFIXES"):
            raise SelectionBridgeError(
                "the X server has no XFIXES extension, so selection changes "
                "could only be discovered by polling"
            )
        self._display.xfixes_query_version()
        screen = self._display.screen()
        self._window = screen.root.create_window(
            0, 0, 1, 1, 0,
            X.CopyFromParent,
            X.InputOutput,
            X.CopyFromParent,
            event_mask=X.PropertyChangeMask,
        )
        self._selection = self._display.get_atom(selection)
        self._target = self._display.get_atom("UTF8_STRING")
        self._targets = self._display.get_atom("TARGETS")
        self._incr_atom = self._display.get_atom("INCR")
        self._text = self._display.get_atom("TEXT")
        self._plain_utf8 = self._display.get_atom("text/plain;charset=utf-8")
        self._plain = self._display.get_atom("text/plain")
        self._property = self._display.get_atom("KILIX_SELECTION")
        self._max_property = min(
            MAX_SELECTION_BYTES,
            max(4096, self._display.display.info.max_request_length * 4 - 256),
        )
        self._display.xfixes_select_selection_input(
            self._window,
            self._selection,
            _xfixes.XFixesSetSelectionOwnerNotifyMask,
        )
        self._display.flush()
        self._reactor.register(
            self._display.fileno(), selectors.EVENT_READ, self._readable
        )

    def fileno(self) -> int:
        return self._display.fileno()

    @property
    def window_id(self) -> int:
        return int(self._window.id)

    def _readable(self, _fd: int, _events: int) -> None:
        self.pump()

    def pump(self) -> None:
        """Dispatch every X event already delivered on the display socket."""
        while True:
            count = self._display.pending_events()
            if not count:
                return
            for _ in range(count):
                self._event(self._display.next_event())

    def _event(self, event) -> None:
        name = type(event).__name__
        if name == "SetSelectionOwnerNotify":
            if getattr(event, "selection", 0) != self._selection:
                return
            owner = getattr(event, "owner", None)
            if owner is not None and int(getattr(owner, "id", owner)) == self._window.id:
                return
            self._convert()
        elif name == "SelectionNotify":
            self._selection_notify(event)
        elif name == "SelectionRequest":
            self._serve(event)
        elif name == "SelectionClear":
            if event.atom == self._selection:
                self._owned = None
        elif name == "PropertyNotify":
            self._property_notify(event)

    # -- reading -------------------------------------------------------------

    def _convert(self) -> None:
        if self._pending:
            # A conversion is already in flight; remember that the selection
            # moved again so exactly one more conversion follows it.
            self._restart = True
            return
        self._pending = True
        self._window.convert_selection(
            self._selection, self._target, self._property, X.CurrentTime
        )
        self._display.flush()

    def _selection_notify(self, event) -> None:
        self._pending = False
        if not getattr(event, "property", 0):
            self._after_read()
            return
        prop = self._window.get_full_property(
            self._property, X.AnyPropertyType, sizehint=_INCR_CHUNK_HINT
        )
        if prop is None:
            self._after_read()
            return
        if prop.property_type == self._incr_atom:
            self._incr = []
            self._incr_size = 0
            self._window.delete_property(self._property)
            self._display.flush()
            return
        self._window.delete_property(self._property)
        self._display.flush()
        self._deliver(self._property_bytes(prop))

    def _property_notify(self, event) -> None:
        if self._incr is None or event.atom != self._property:
            return
        if event.state != X.PropertyNewValue:
            return
        prop = self._window.get_full_property(
            self._property, X.AnyPropertyType, sizehint=_INCR_CHUNK_HINT
        )
        self._window.delete_property(self._property)
        self._display.flush()
        if prop is None:
            self._incr = None
            self._after_read()
            return
        chunk = self._property_bytes(prop)
        if not chunk:
            data = b"".join(self._incr)
            self._incr = None
            self._deliver(data)
            return
        self._incr_size += len(chunk)
        if self._incr_size > MAX_SELECTION_BYTES:
            self._incr = None
            if self._on_refused:
                self._on_refused("x", self._incr_size)
            self._after_read()
            return
        self._incr.append(chunk)

    @staticmethod
    def _property_bytes(prop) -> bytes:
        value = prop.value
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8", "replace")
        try:
            return bytes(value)
        except (TypeError, ValueError):
            return b""

    def _deliver(self, data: bytes) -> None:
        if len(data) > MAX_SELECTION_BYTES:
            if self._on_refused:
                self._on_refused("x", len(data))
            self._after_read()
            return
        text = data.decode("utf-8", "replace")
        if text != self._owned and self._on_text:
            self._on_text(text)
        self._after_read()

    def _after_read(self) -> None:
        if self._restart:
            self._restart = False
            self._convert()

    # -- publishing ----------------------------------------------------------

    def publish(self, text: str) -> None:
        """Take ownership of the selection and serve ``text`` to requestors."""
        payload = text.encode("utf-8")
        if len(payload) > self._max_property:
            if self._on_refused:
                self._on_refused("wayland", len(payload))
            return
        self._owned = text
        self._window.set_selection_owner(self._selection, X.CurrentTime)
        self._display.flush()

    def _serve(self, event) -> None:
        prop = getattr(event, "property", 0) or event.target
        text = self._owned
        refused = text is None
        if not refused:
            payload = text.encode("utf-8")
            if event.target == self._targets:
                event.requestor.change_property(
                    prop,
                    Xatom.ATOM,
                    32,
                    [
                        self._targets,
                        self._target,
                        Xatom.STRING,
                        self._text,
                        self._plain_utf8,
                        self._plain,
                    ],
                )
            elif event.target in (
                self._target, self._plain_utf8, self._plain, self._text
            ):
                event.requestor.change_property(prop, event.target, 8, payload)
            elif event.target == Xatom.STRING:
                event.requestor.change_property(
                    prop, Xatom.STRING, 8, text.encode("latin-1", "replace")
                )
            else:
                refused = True
        notify = _xevent.SelectionNotify(
            time=event.time,
            requestor=event.requestor,
            selection=event.selection,
            target=event.target,
            property=0 if refused else prop,
        )
        event.requestor.send_event(notify, event_mask=X.NoEventMask)
        self._display.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._reactor.unregister(self._display.fileno())
        except (OSError, ValueError):
            pass
        try:
            self._window.destroy()
            self._display.flush()
        except Exception:
            pass
        try:
            self._display.close()
        except Exception:
            pass

    def __enter__(self) -> "XSelectionEndpoint":
        return self

    def __exit__(self, _kind, _value, _traceback) -> None:
        self.close()


class SelectionBridge:
    """Carry text both ways between a nested Wayland session and the host X display."""

    def __init__(
        self,
        wayland_socket: str,
        *,
        display: str | None = None,
        xauthority: str | None = None,
        selection: str = "CLIPBOARD",
    ) -> None:
        self.wayland_socket = str(wayland_socket)
        self._display_name = display
        self._xauthority = xauthority
        self._selection = selection
        self.reactor = Reactor()
        self.wayland: WaylandSelectionEndpoint | None = None
        self.x: XSelectionEndpoint | None = None
        self._wake_read = -1
        self._wake_write = -1
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._counters = BridgeCounters()
        self._closed = False
        self.error: BaseException | None = None

    @property
    def counters(self) -> BridgeCounters:
        return self._counters

    def open(self) -> "SelectionBridge":
        """Connect both halves. Raises rather than degrading silently."""
        self._wake_read, self._wake_write = os.pipe()
        os.set_blocking(self._wake_read, False)
        self.reactor.register(self._wake_read, selectors.EVENT_READ, self._drain_wake)
        self.wayland = WaylandSelectionEndpoint(
            self.wayland_socket,
            self.reactor,
            on_text=self._from_wayland,
            on_refused=self._refused,
        )
        self.wayland.connect()
        self.x = XSelectionEndpoint(
            self.reactor,
            display=self._display_name,
            xauthority=self._xauthority,
            selection=self._selection,
            on_text=self._from_x,
            on_refused=self._refused,
        )
        return self

    def _drain_wake(self, fd: int, _events: int) -> None:
        try:
            os.read(fd, 4096)
        except OSError:
            pass

    def _from_wayland(self, text: str) -> None:
        if self.x is None:
            return
        self.x.publish(text)
        self._counters = BridgeCounters(
            self._counters.wayland_to_x + 1,
            self._counters.x_to_wayland,
            self._counters.refused_oversize,
        )

    def _from_x(self, text: str) -> None:
        if self.wayland is None:
            return
        self.wayland.publish(text)
        self._counters = BridgeCounters(
            self._counters.wayland_to_x,
            self._counters.x_to_wayland + 1,
            self._counters.refused_oversize,
        )

    def _refused(self, _origin: str, _size: int) -> None:
        self._counters = BridgeCounters(
            self._counters.wayland_to_x,
            self._counters.x_to_wayland,
            self._counters.refused_oversize + 1,
        )

    def serve(self, timeout: float | None = None) -> None:
        """Block on both descriptors until :meth:`stop` or a peer disappears."""
        while not self._stop.is_set():
            self.reactor.wait(timeout)

    def start(self) -> threading.Thread:
        """Serve on a daemon thread so a launcher can own the foreground."""
        if self._thread is not None:
            return self._thread

        def run() -> None:
            try:
                self.serve()
            except BaseException as error:  # noqa: BLE001 - recorded, not swallowed
                self.error = error

        self._thread = threading.Thread(
            target=run, name="kilix-selection-bridge", daemon=True
        )
        self._thread.start()
        return self._thread

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._wake_write >= 0:
            try:
                os.write(self._wake_write, b"\0")
            except OSError:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop()
        if self.x is not None:
            self.x.close()
        if self.wayland is not None:
            self.wayland.close()
        for fd in (self._wake_read, self._wake_write):
            if fd >= 0:
                try:
                    self.reactor.unregister(fd)
                except (OSError, ValueError):
                    pass
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._wake_read = self._wake_write = -1
        self.reactor.close()

    def __enter__(self) -> "SelectionBridge":
        return self.open()

    def __exit__(self, _kind, _value, _traceback) -> None:
        self.close()


__all__ = [
    "BridgeCounters",
    "MAX_SELECTION_BYTES",
    "Reactor",
    "SelectionBridge",
    "SelectionBridgeError",
    "WAYLAND_TEXT_MIME_TYPES",
    "WaylandSelectionEndpoint",
    "XSelectionEndpoint",
]
