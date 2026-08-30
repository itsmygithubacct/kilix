"""Selection-bridge tests.

The Wayland peer here is a scripted compositor rather than Weston, because a
compositor cannot run in this environment.  Two things keep that honest: the
wire framing is pinned against hand-computed bytes from the protocol
specification rather than against the encoder, and the X half runs against a
real X server so its selection semantics are the server's, not a fake's.
"""

from __future__ import annotations

import os
import queue
import resource
import selectors
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

from kilix_sdk import _wayland, clipboard  # noqa: E402
from kilix_sdk._wayland import Decoder, Encoder, WaylandConnection  # noqa: E402


SERVER_ID_BASE = 0xFF000000


def _pad(value: int) -> int:
    return (value + 3) & ~3


class ScriptedCompositor(threading.Thread):
    """A Wayland server that implements only the selection path."""

    def __init__(self, path: str) -> None:
        super().__init__(name="scripted-compositor", daemon=True)
        self.path = path
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(path)
        self._listener.listen(1)
        self._commands: queue.Queue = queue.Queue()
        self._wake_read, self._wake_write = os.pipe()
        os.set_blocking(self._wake_read, False)
        self._connection: WaylandConnection | None = None
        self._next_id = SERVER_ID_BASE
        self._registry = 0
        self._seat = 0
        self._manager = 0
        self._device = 0
        self._offer_mimes: dict[int, list[str]] = {}
        self._client_source = 0
        self.source_mimes: list[str] = []
        self.set_selection_calls: list[tuple[int, int]] = []
        self.bound: dict[str, int] = {}
        self.received: "queue.Queue[bytes]" = queue.Queue()
        self.failure: BaseException | None = None
        self._stop = threading.Event()
        self.connected = threading.Event()

    # -- test-facing API -----------------------------------------------------

    def announce(self, text: str, mimes=("text/plain;charset=utf-8",)) -> None:
        self._submit(lambda: self._announce(text, tuple(mimes)))

    def clear(self) -> None:
        self._submit(self._clear)

    def request_client_text(self, mime: str = "text/plain;charset=utf-8") -> bytes:
        answer: "queue.Queue[bytes]" = queue.Queue()
        self._submit(lambda: self._request(mime, answer))
        return answer.get(timeout=5)

    def shutdown(self) -> None:
        self._stop.set()
        os.write(self._wake_write, b"\0")
        self.join(timeout=5)

    def _submit(self, action) -> None:
        self._commands.put(action)
        os.write(self._wake_write, b"\0")

    # -- server internals ----------------------------------------------------

    def _allocate(self) -> int:
        self._next_id += 1
        return self._next_id

    def _send(self, object_id: int, opcode: int, encoder: Encoder | None = None) -> None:
        assert self._connection is not None
        self._connection.send(object_id, opcode, encoder)

    def _announce(self, text: str, mimes: tuple[str, ...]) -> None:
        offer = self._allocate()
        self._offer_mimes[offer] = list(mimes)
        self._offer_text[offer] = text
        self._send(self._device, 0, Encoder().new_id(offer))
        for mime in mimes:
            self._send(offer, 0, Encoder().string(mime))
        self._send(self._device, 5, Encoder().object(offer))

    def _clear(self) -> None:
        self._send(self._device, 5, Encoder().object(None))

    def _request(self, mime: str, answer: "queue.Queue[bytes]") -> None:
        if not self._client_source:
            answer.put(b"")
            return
        read_fd, write_fd = os.pipe()
        try:
            self._send(
                self._client_source, 1, Encoder().string(mime).fd(write_fd)
            )
        finally:
            os.close(write_fd)
        self._pending_reads.append((read_fd, bytearray(), answer))

    def run(self) -> None:
        try:
            self._offer_text = {}
            self._pending_reads = []
            client, _address = self._listener.accept()
            self._connection = WaylandConnection.from_socket(client)
            self.connected.set()
            selector = selectors.DefaultSelector()
            selector.register(self._connection.fileno(), selectors.EVENT_READ, "client")
            selector.register(self._wake_read, selectors.EVENT_READ, "wake")
            while not self._stop.is_set():
                for read_fd, buffer, answer in list(self._pending_reads):
                    try:
                        selector.register(read_fd, selectors.EVENT_READ, ("read", read_fd))
                    except KeyError:
                        pass
                # Block rather than poll: a polling peer would show up in
                # the bridge's own idle-CPU measurement.
                for key, _events in selector.select(None):
                    if key.data == "client":
                        self._dispatch()
                    elif key.data == "wake":
                        try:
                            os.read(self._wake_read, 4096)
                        except BlockingIOError:
                            pass
                        while True:
                            try:
                                self._commands.get_nowait()()
                            except queue.Empty:
                                break
                    elif isinstance(key.data, tuple):
                        self._drain(selector, key.data[1])
        except BaseException as error:  # noqa: BLE001 - reported to the test
            self.failure = error
        finally:
            if self._connection is not None:
                self._connection.close()
            self._listener.close()
            for fd in (self._wake_read, self._wake_write):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _drain(self, selector, read_fd: int) -> None:
        for index, (fd, buffer, answer) in enumerate(self._pending_reads):
            if fd != read_fd:
                continue
            chunk = os.read(fd, 65536)
            if chunk:
                buffer += chunk
                return
            selector.unregister(fd)
            os.close(fd)
            self._pending_reads.pop(index)
            answer.put(bytes(buffer))
            return

    def _dispatch(self) -> None:
        assert self._connection is not None
        for message in self._connection.read():
            decoder = self._connection.decoder(message)
            if message.object_id == 1 and message.opcode == 1:
                self._registry = decoder.new_id()
                for name, interface, version in (
                    (1, "wl_seat", 7),
                    (2, "wl_data_device_manager", 3),
                ):
                    self._send(
                        self._registry,
                        0,
                        Encoder().uint(name).string(interface).uint(version),
                    )
            elif message.object_id == 1 and message.opcode == 0:
                callback = decoder.new_id()
                self._send(callback, 0, Encoder().uint(1))
                self._send(1, 1, Encoder().uint(callback))
            elif message.object_id == self._registry and message.opcode == 0:
                decoder.uint()
                interface = decoder.string() or ""
                version = decoder.uint()
                identifier = decoder.new_id()
                self.bound[interface] = version
                if interface == "wl_seat":
                    self._seat = identifier
                    self._send(identifier, 0, Encoder().uint(3))
                    self._send(identifier, 1, Encoder().string("seat0"))
                elif interface == "wl_data_device_manager":
                    self._manager = identifier
            elif message.object_id == self._manager and message.opcode == 1:
                self._device = decoder.new_id()
                decoder.object()
            elif message.object_id == self._manager and message.opcode == 0:
                self._client_source = decoder.new_id()
                self.source_mimes = []
            elif message.object_id == self._client_source and message.opcode == 0:
                self.source_mimes.append(decoder.string() or "")
            elif message.object_id == self._device and message.opcode == 1:
                source = decoder.object()
                serial = decoder.uint()
                self.set_selection_calls.append((source, serial))
            elif message.opcode == 1 and message.object_id in self._offer_mimes:
                decoder.string()
                fd = decoder.fd()
                payload = self._offer_text.get(message.object_id, "").encode("utf-8")
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)


class WireFormatTests(unittest.TestCase):
    """Pin the framing against the specification, not against the encoder."""

    def test_request_bytes_match_a_hand_encoded_message(self):
        encoder = Encoder().new_id(2)
        connection_pair = socket.socketpair()
        try:
            client = WaylandConnection.from_socket(connection_pair[0])
            client.send(1, 1, encoder)
            data = connection_pair[1].recv(64)
        finally:
            connection_pair[0].close()
            connection_pair[1].close()
        # wl_display@1.get_registry(new_id 2): object 1, opcode 1, size 12.
        expected = struct.pack("=I", 1) + struct.pack("=I", (12 << 16) | 1)
        expected += struct.pack("=I", 2)
        self.assertEqual(data, expected)

    def test_string_arguments_are_nul_terminated_and_padded(self):
        encoder = Encoder().string("wl_seat")
        # "wl_seat" is 7 bytes plus NUL: length 8, already 4-byte aligned.
        self.assertEqual(
            bytes(encoder.body), struct.pack("=I", 8) + b"wl_seat\x00"
        )
        encoder = Encoder().string("ab")
        self.assertEqual(len(encoder.body), 4 + _pad(3))
        self.assertEqual(bytes(encoder.body), struct.pack("=I", 3) + b"ab\x00\x00")

    def test_null_string_and_object_encode_as_zero(self):
        self.assertEqual(bytes(Encoder().string(None).body), struct.pack("=I", 0))
        self.assertEqual(bytes(Encoder().object(None).body), struct.pack("=I", 0))

    def test_decoder_reads_in_declaration_order(self):
        from collections import deque

        body = (
            struct.pack("=I", 42)
            + struct.pack("=I", 8)
            + b"wl_seat\x00"
            + struct.pack("=I", 7)
        )
        decoder = Decoder(body, deque())
        self.assertEqual(decoder.uint(), 42)
        self.assertEqual(decoder.string(), "wl_seat")
        self.assertEqual(decoder.uint(), 7)

    def test_truncated_body_is_rejected(self):
        from collections import deque

        decoder = Decoder(struct.pack("=H", 1), deque())
        with self.assertRaises(_wayland.WaylandProtocolError):
            decoder.uint()

    def test_display_socket_requires_a_runtime_directory(self):
        self.assertEqual(
            _wayland.display_socket("wayland-0", "/run/user/9"),
            "/run/user/9/wayland-0",
        )
        self.assertEqual(_wayland.display_socket("/tmp/abs.sock", None), "/tmp/abs.sock")
        with self.assertRaises(_wayland.WaylandProtocolError):
            _wayland.display_socket("wayland-0", "")


class WaylandEndpointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="kilix-selection-")
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.socket_path = os.path.join(self.directory, "wayland-0")
        self.compositor = ScriptedCompositor(self.socket_path)
        self.compositor.start()
        self.addCleanup(self.compositor.shutdown)
        self.reactor = clipboard.Reactor()
        self.addCleanup(self.reactor.close)
        self.texts: list[str] = []
        self.refusals: list[tuple[str, int]] = []
        self.endpoint = clipboard.WaylandSelectionEndpoint(
            self.socket_path,
            self.reactor,
            on_text=self.texts.append,
            on_refused=lambda origin, size: self.refusals.append((origin, size)),
        )
        self.addCleanup(self.endpoint.close)
        self.endpoint.connect()

    def _settle(self, predicate, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.reactor.wait(0.05)
        return predicate()

    def test_connect_binds_the_seat_and_data_device(self):
        self.assertTrue(self.endpoint.ready)
        self.assertEqual(self.compositor.bound.get("wl_seat"), 2)
        self.assertEqual(self.compositor.bound.get("wl_data_device_manager"), 3)

    def test_selection_offer_is_read_as_text(self):
        self.compositor.announce("hello from android")
        self.assertTrue(self._settle(lambda: self.texts))
        self.assertEqual(self.texts, ["hello from android"])

    def test_utf8_payloads_survive_the_transfer(self):
        self.compositor.announce("naïve — ünïcøde ✓")
        self.assertTrue(self._settle(lambda: self.texts))
        self.assertEqual(self.texts, ["naïve — ünïcøde ✓"])

    def test_offer_without_a_text_mime_is_ignored(self):
        self.compositor.announce("ignored", mimes=("image/png",))
        self.reactor.wait(0.3)
        self.assertEqual(self.texts, [])

    def test_oversize_offer_is_refused_rather_than_truncated(self):
        self.compositor.announce("x" * (clipboard.MAX_SELECTION_BYTES + 1))
        self.assertTrue(self._settle(lambda: self.refusals))
        self.assertEqual(self.texts, [])
        self.assertEqual(self.refusals[0][0], "wayland")

    def test_publish_owns_the_selection_and_serves_the_text(self):
        self.endpoint.publish("from the host")
        self._settle(lambda: self.compositor.set_selection_calls)
        self.assertEqual(len(self.compositor.set_selection_calls), 1)
        source, serial = self.compositor.set_selection_calls[0]
        self.assertNotEqual(source, 0)
        self.assertGreater(serial, 0)
        self.assertEqual(
            self.compositor.source_mimes, list(clipboard.WAYLAND_TEXT_MIME_TYPES)
        )
        answer: "queue.Queue[bytes]" = queue.Queue()
        thread = threading.Thread(
            target=lambda: answer.put(self.compositor.request_client_text()),
            daemon=True,
        )
        thread.start()
        self._settle(lambda: not answer.empty(), timeout=5)
        thread.join(timeout=5)
        self.assertEqual(answer.get_nowait(), b"from the host")

    def test_publish_uses_a_strictly_increasing_serial(self):
        self.endpoint.publish("one")
        self._settle(lambda: len(self.compositor.set_selection_calls) == 1)
        self.endpoint.publish("two")
        self._settle(lambda: len(self.compositor.set_selection_calls) == 2)
        serials = [serial for _source, serial in self.compositor.set_selection_calls]
        self.assertEqual(serials, sorted(set(serials)))

    def test_our_own_published_text_is_not_read_back_into_a_loop(self):
        self.endpoint.publish("echo me")
        self._settle(lambda: self.compositor.set_selection_calls)
        self.compositor.announce("echo me")
        self.reactor.wait(0.3)
        self.reactor.wait(0.3)
        self.assertEqual(self.texts, [])

    def test_oversize_publish_is_refused(self):
        self.endpoint.publish("y" * (clipboard.MAX_SELECTION_BYTES + 1))
        self.assertEqual(self.compositor.set_selection_calls, [])
        self.assertEqual(self.refusals[0][0], "x")


def _x_server():
    """Start a private X server on a display it chooses, or return None.

    ``-displayfd`` makes the server pick a free display and report it, which
    avoids both racing another suite for a fixed number and inheriting a stale
    socket left by a server that was killed rather than asked to stop.
    """
    if not shutil.which("Xvfb"):
        return None
    read_fd, write_fd = os.pipe()
    try:
        process = subprocess.Popen(
            ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "320x240x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(write_fd,),
        )
    except OSError:
        os.close(read_fd)
        os.close(write_fd)
        return None
    os.close(write_fd)
    reported = b""
    try:
        deadline = time.monotonic() + 15
        os.set_blocking(read_fd, False)
        while time.monotonic() < deadline and b"\n" not in reported:
            if process.poll() is not None:
                break
            try:
                chunk = os.read(read_fd, 32)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            if not chunk:
                break
            reported += chunk
    finally:
        os.close(read_fd)
    number = reported.decode("ascii", "ignore").strip()
    if not number.isdigit():
        _stop_x_server(process)
        return None
    display = f":{number}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        from Xlib import display as xdisplay

        probe = None
        try:
            probe = xdisplay.Display(display)
        except Exception:
            time.sleep(0.1)
            continue
        finally:
            if probe is not None:
                probe.close()
        return process, display
    _stop_x_server(process)
    return None


def _stop_x_server(process) -> None:
    """Ask the server to exit so it removes its own socket."""
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class XEndpointTests(unittest.TestCase):
    server = None
    display = None

    @classmethod
    def setUpClass(cls):
        started = _x_server()
        if started is None:
            raise unittest.SkipTest("no X server is available for selection tests")
        cls.server, cls.display = started

    @classmethod
    def tearDownClass(cls):
        _stop_x_server(cls.server)

    def setUp(self):
        self.reactor = clipboard.Reactor()
        self.addCleanup(self.reactor.close)
        self.texts: list[str] = []
        self.endpoint = clipboard.XSelectionEndpoint(
            self.reactor, display=self.display, xauthority="", on_text=self.texts.append
        )
        self.addCleanup(self.endpoint.close)

    def _settle(self, predicate, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.reactor.wait(0.05)
        return predicate()

    def test_published_text_is_readable_by_an_independent_x_client(self):
        self.endpoint.publish("host clipboard text")
        served = _read_clipboard_with_a_separate_client(self.display, self.reactor)
        self.assertEqual(served, "host clipboard text")

    def test_a_foreign_owner_is_noticed_without_polling(self):
        owner = _own_clipboard_in_a_subprocess(self.display, "android clipboard text")
        self.addCleanup(_reap, owner)
        self.assertTrue(self._settle(lambda: self.texts))
        self.assertEqual(self.texts[-1], "android clipboard text")

    def test_our_own_ownership_change_is_not_read_back(self):
        self.endpoint.publish("no loop please")
        self.reactor.wait(0.3)
        self.reactor.wait(0.3)
        self.assertEqual(self.texts, [])


def _read_clipboard_with_a_separate_client(display: str, reactor) -> str:
    """Read CLIPBOARD from a second process while the endpoint keeps serving."""
    script = (
        "import sys\n"
        "from Xlib import X, display as d\n"
        "conn = d.Display(sys.argv[1])\n"
        "root = conn.screen().root\n"
        "win = root.create_window(0,0,1,1,0, X.CopyFromParent, X.InputOutput,"
        " X.CopyFromParent, event_mask=X.PropertyChangeMask)\n"
        "sel = conn.get_atom('CLIPBOARD')\n"
        "target = conn.get_atom('UTF8_STRING')\n"
        "prop = conn.get_atom('READER')\n"
        "win.convert_selection(sel, target, prop, X.CurrentTime)\n"
        "conn.flush()\n"
        "import time\n"
        "deadline = time.time() + 10\n"
        "while time.time() < deadline:\n"
        "    e = conn.next_event()\n"
        "    if type(e).__name__ == 'SelectionNotify':\n"
        "        if not e.property:\n"
        "            print('')\n"
        "            break\n"
        "        value = win.get_full_property(prop, X.AnyPropertyType).value\n"
        "        if isinstance(value, str):\n"
        "            value = value.encode()\n"
        "        sys.stdout.write(bytes(value).decode('utf-8'))\n"
        "        break\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, display],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while process.poll() is None and time.monotonic() < deadline:
            reactor.wait(0.05)
        stdout, _stderr = process.communicate(timeout=5)
    finally:
        _reap(process)
    return stdout


def _own_clipboard_in_a_subprocess(display: str, text: str):
    script = (
        "import sys, time\n"
        "from Xlib import X, Xatom, display as d\n"
        "from Xlib.protocol import event as ev\n"
        "conn = d.Display(sys.argv[1])\n"
        "text = sys.argv[2]\n"
        "root = conn.screen().root\n"
        "win = root.create_window(0,0,1,1,0, X.CopyFromParent, X.InputOutput,"
        " X.CopyFromParent, event_mask=X.PropertyChangeMask)\n"
        "sel = conn.get_atom('CLIPBOARD')\n"
        "utf8 = conn.get_atom('UTF8_STRING')\n"
        "targets = conn.get_atom('TARGETS')\n"
        "win.set_selection_owner(sel, X.CurrentTime)\n"
        "conn.flush()\n"
        "sys.stderr.write('ready\\n'); sys.stderr.flush()\n"
        "while True:\n"
        "    e = conn.next_event()\n"
        "    if type(e).__name__ != 'SelectionRequest':\n"
        "        continue\n"
        "    prop = e.property or e.target\n"
        "    ok = True\n"
        "    if e.target == targets:\n"
        "        e.requestor.change_property(prop, Xatom.ATOM, 32, [targets, utf8])\n"
        "    elif e.target == utf8:\n"
        "        e.requestor.change_property(prop, utf8, 8, text.encode())\n"
        "    else:\n"
        "        ok = False\n"
        "    n = ev.SelectionNotify(time=e.time, requestor=e.requestor,"
        " selection=e.selection, target=e.target, property=prop if ok else 0)\n"
        "    e.requestor.send_event(n, event_mask=X.NoEventMask)\n"
        "    conn.flush()\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, display, text],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    process.stderr.readline()
    return process


def _reap(process) -> None:
    """Stop a helper client and close its pipes so the suite leaks nothing."""
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    for stream in (process.stdout, process.stderr, process.stdin):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


class BridgeIntegrationTests(unittest.TestCase):
    server = None
    display = None

    @classmethod
    def setUpClass(cls):
        started = _x_server()
        if started is None:
            raise unittest.SkipTest("no X server is available for selection tests")
        cls.server, cls.display = started

    @classmethod
    def tearDownClass(cls):
        _stop_x_server(cls.server)

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="kilix-bridge-")
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.socket_path = os.path.join(self.directory, "wayland-0")
        self.compositor = ScriptedCompositor(self.socket_path)
        self.compositor.start()
        self.addCleanup(self.compositor.shutdown)
        self.bridge = clipboard.SelectionBridge(
            self.socket_path, display=self.display, xauthority=""
        )
        self.bridge.open()
        self.addCleanup(self.bridge.close)

    def _pump(self, predicate, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.bridge.reactor.wait(0.05)
        return predicate()

    def test_wayland_selection_reaches_the_x_clipboard(self):
        self.compositor.announce("android says hello")
        self.assertTrue(self._pump(lambda: self.bridge.counters.wayland_to_x == 1))
        served = _read_clipboard_with_a_separate_client(
            self.display, self.bridge.reactor
        )
        self.assertEqual(served, "android says hello")

    def test_x_clipboard_reaches_the_wayland_selection(self):
        owner = _own_clipboard_in_a_subprocess(self.display, "host says hello")
        self.addCleanup(_reap, owner)
        self.assertTrue(self._pump(lambda: self.bridge.counters.x_to_wayland == 1))
        answer: "queue.Queue[bytes]" = queue.Queue()
        thread = threading.Thread(
            target=lambda: answer.put(self.compositor.request_client_text()),
            daemon=True,
        )
        thread.start()
        self._pump(lambda: not answer.empty())
        thread.join(timeout=5)
        self.assertEqual(answer.get_nowait(), b"host says hello")

    def test_a_carried_selection_does_not_bounce_back(self):
        self.compositor.announce("one way only")
        self.assertTrue(self._pump(lambda: self.bridge.counters.wayland_to_x == 1))
        for _ in range(20):
            self.bridge.reactor.wait(0.05)
        self.assertEqual(self.bridge.counters.x_to_wayland, 0)
        self.assertEqual(self.bridge.counters.wayland_to_x, 1)

    def test_an_idle_bridge_consumes_no_measurable_cpu(self):
        window = float(os.environ.get("KILIX_BRIDGE_IDLE_SECONDS", "3"))
        before = resource.getrusage(resource.RUSAGE_SELF)
        thread = self.bridge.start()
        time.sleep(window)
        self.bridge.stop()
        thread.join(timeout=5)
        after = resource.getrusage(resource.RUSAGE_SELF)
        spent = (after.ru_utime - before.ru_utime) + (
            after.ru_stime - before.ru_stime
        )
        self.assertIsNone(self.bridge.error)
        # A poll loop at even 10 Hz would spend far more than this.
        self.assertLess(spent, window * 0.01, f"idle CPU {spent:.4f}s over {window:g}s")


if __name__ == "__main__":
    unittest.main()
