"""Minimal Wayland client transport for host-owned selection bridging.

Kilix already owns and supervises the nested compositor described in
``kilix_sdk.wayland``.  Bridging its selection needs exactly one Wayland client
capability and nothing else, so this module implements only the wire framing
that path uses rather than adding a client library to every provider's build:
length-prefixed messages, client-allocated object identifiers, and SCM_RIGHTS
descriptor passing.

The transport is deliberately free of any timer or retry loop.  A caller polls
:meth:`WaylandConnection.fileno` through its own event loop and reads only when
the compositor has written, so an idle bridge costs no wakeups of its own.
"""

from __future__ import annotations

import array
import os
import select
import socket
import struct
from collections import deque


_WORD = struct.Struct("=I")
_SIGNED_WORD = struct.Struct("=i")
_HEADER_BYTES = 8
_MAX_MESSAGE_BYTES = 4096
_MAX_FDS_PER_MESSAGE = 4
_FIRST_CLIENT_ID = 2
_LAST_CLIENT_ID = 0xFEFFFFFF
_SEND_TIMEOUT = 5.0


class WaylandProtocolError(RuntimeError):
    """The compositor connection violated the framing this client accepts."""


def _padded(length: int) -> int:
    return (length + 3) & ~3


class Encoder:
    """Accumulate one request body and the descriptors it carries."""

    def __init__(self) -> None:
        self.body = bytearray()
        self.fds: list[int] = []

    def uint(self, value: int) -> "Encoder":
        self.body += _WORD.pack(int(value) & 0xFFFFFFFF)
        return self

    def integer(self, value: int) -> "Encoder":
        self.body += _SIGNED_WORD.pack(int(value))
        return self

    def object(self, value: int | None) -> "Encoder":
        return self.uint(0 if value is None else value)

    def new_id(self, value: int) -> "Encoder":
        return self.uint(value)

    def string(self, value: str | None) -> "Encoder":
        if value is None:
            return self.uint(0)
        data = value.encode("utf-8") + b"\0"
        self.uint(len(data))
        self.body += data
        self.body += b"\0" * (_padded(len(data)) - len(data))
        return self

    def fd(self, value: int) -> "Encoder":
        if len(self.fds) >= _MAX_FDS_PER_MESSAGE:
            raise WaylandProtocolError("too many descriptors for one request")
        self.fds.append(int(value))
        return self


class Decoder:
    """Read one event body in declaration order."""

    def __init__(self, body: bytes, fds: deque[int]) -> None:
        self._body = body
        self._offset = 0
        self._fds = fds

    def _take(self, count: int) -> bytes:
        end = self._offset + count
        if end > len(self._body):
            raise WaylandProtocolError("truncated Wayland event body")
        chunk = self._body[self._offset:end]
        self._offset = end
        return chunk

    def uint(self) -> int:
        return _WORD.unpack(self._take(4))[0]

    def integer(self) -> int:
        return _SIGNED_WORD.unpack(self._take(4))[0]

    def object(self) -> int:
        return self.uint()

    def new_id(self) -> int:
        return self.uint()

    def fixed(self) -> float:
        return _SIGNED_WORD.unpack(self._take(4))[0] / 256.0

    def string(self) -> str | None:
        length = self.uint()
        if length == 0:
            return None
        data = self._take(_padded(length))[:length]
        if not data.endswith(b"\0"):
            raise WaylandProtocolError("Wayland string is not terminated")
        return data[:-1].decode("utf-8", "replace")

    def array(self) -> bytes:
        length = self.uint()
        return self._take(_padded(length))[:length]

    def fd(self) -> int:
        if not self._fds:
            raise WaylandProtocolError("Wayland event is missing its descriptor")
        return self._fds.popleft()


class Message:
    """One decoded event: its target object, opcode and undecoded body."""

    __slots__ = ("object_id", "opcode", "body")

    def __init__(self, object_id: int, opcode: int, body: bytes) -> None:
        self.object_id = int(object_id)
        self.opcode = int(opcode)
        self.body = body


def display_socket(display: str | None = None, runtime_dir: str | None = None) -> str:
    """Return the socket path for a Wayland display name.

    ``runtime_dir`` of ``None`` reads the environment; an explicit empty string
    means the caller asserts there is no runtime directory.
    """
    name = display or os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
    if os.path.isabs(name):
        return name
    if runtime_dir is None:
        root = os.environ.get("XDG_RUNTIME_DIR") or ""
    else:
        root = runtime_dir
    if not root:
        raise WaylandProtocolError(
            "XDG_RUNTIME_DIR is required to locate a relative Wayland display"
        )
    return os.path.join(root, name)


class WaylandConnection:
    """One client connection with client-side object allocation."""

    def __init__(self, path: str, *, connector=socket.socket) -> None:
        self.path = str(path)
        self._socket = connector(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._socket.connect(self.path)
        except OSError:
            self._socket.close()
            raise
        self._socket.setblocking(False)
        self._buffer = bytearray()
        self._fds: deque[int] = deque()
        self._next_id = _FIRST_CLIENT_ID
        self.closed = False
        self._released = False

    @classmethod
    def from_socket(cls, sock) -> "WaylandConnection":
        """Wrap an already-connected socket, for tests and injected transports."""
        connection = cls.__new__(cls)
        connection.path = ""
        connection._socket = sock
        connection._socket.setblocking(False)
        connection._buffer = bytearray()
        connection._fds = deque()
        connection._next_id = _FIRST_CLIENT_ID
        connection.closed = False
        connection._released = False
        return connection

    def fileno(self) -> int:
        return self._socket.fileno()

    def allocate(self) -> int:
        identifier = self._next_id
        if identifier > _LAST_CLIENT_ID:
            raise WaylandProtocolError("Wayland object identifiers are exhausted")
        self._next_id = identifier + 1
        return identifier

    def send(self, object_id: int, opcode: int, encoder: Encoder | None = None) -> None:
        """Write one request, transferring any descriptors it carries."""
        if self.closed:
            raise WaylandProtocolError("Wayland connection is closed")
        encoder = encoder or Encoder()
        size = _HEADER_BYTES + len(encoder.body)
        if size > _MAX_MESSAGE_BYTES:
            raise WaylandProtocolError("Wayland request is too large")
        header = _WORD.pack(int(object_id)) + _WORD.pack((size << 16) | int(opcode))
        payload = memoryview(bytes(header) + bytes(encoder.body))
        ancillary = []
        if encoder.fds:
            ancillary = [(
                socket.SOL_SOCKET,
                socket.SCM_RIGHTS,
                array.array("i", encoder.fds),
            )]
        sent = 0
        while sent < len(payload):
            try:
                # Descriptors ride with the first byte of their own message, so
                # they are attached only to this initial sendmsg.
                sent += self._socket.sendmsg(
                    [payload[sent:]], ancillary if sent == 0 else []
                )
            except BlockingIOError:
                if not select.select([], [self._socket], [], _SEND_TIMEOUT)[1]:
                    raise WaylandProtocolError(
                        "Wayland compositor stopped accepting requests"
                    )
        return None

    def read(self) -> list[Message]:
        """Drain everything the compositor has written and frame it."""
        while True:
            try:
                data, ancillary, _flags, _address = self._socket.recvmsg(
                    _MAX_MESSAGE_BYTES,
                    socket.CMSG_SPACE(_MAX_FDS_PER_MESSAGE * 4),
                )
            except BlockingIOError:
                break
            except (ConnectionResetError, OSError) as error:
                self.closed = True
                raise WaylandProtocolError(
                    f"Wayland connection failed: {error}"
                ) from error
            for level, kind, payload in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    received = array.array("i")
                    received.frombytes(
                        payload[: len(payload) - (len(payload) % received.itemsize)]
                    )
                    self._fds.extend(int(value) for value in received)
            if not data:
                self.closed = True
                break
            self._buffer += data
        messages: list[Message] = []
        while len(self._buffer) >= _HEADER_BYTES:
            object_id = _WORD.unpack_from(self._buffer, 0)[0]
            word = _WORD.unpack_from(self._buffer, 4)[0]
            size = word >> 16
            opcode = word & 0xFFFF
            if size < _HEADER_BYTES or size > _MAX_MESSAGE_BYTES:
                self.closed = True
                raise WaylandProtocolError("Wayland event declares an invalid size")
            if len(self._buffer) < size:
                break
            body = bytes(self._buffer[_HEADER_BYTES:size])
            del self._buffer[:size]
            messages.append(Message(object_id, opcode, body))
        return messages

    def decoder(self, message: Message) -> Decoder:
        return Decoder(message.body, self._fds)

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        self.closed = True
        while self._fds:
            try:
                os.close(self._fds.popleft())
            except OSError:
                pass
        try:
            self._socket.close()
        except OSError:
            pass

    def __enter__(self) -> "WaylandConnection":
        return self

    def __exit__(self, _kind, _value, _traceback) -> None:
        self.close()


__all__ = [
    "Decoder",
    "Encoder",
    "Message",
    "WaylandConnection",
    "WaylandProtocolError",
    "display_socket",
]
