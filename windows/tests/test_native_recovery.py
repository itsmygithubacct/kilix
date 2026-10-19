"""Portable native-broker logic checks; actual ConPTY tests live in the engine."""
import importlib
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

ENGINE = Path(__file__).resolve().parents[2] / 'src'
if not (ENGINE / 'kitty/windows_pty_broker.py').exists():
    raise unittest.SkipTest('Initialize the recovery engine for portable native-broker checks')
sys.path.insert(0, str(ENGINE))
broker = importlib.import_module('kitty.windows_pty_broker')
wire = broker.wire


def eventually(function, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if answer := function():
            return answer
        time.sleep(.01)
    raise AssertionError('Timed out waiting for broker state')


class ProtocolTests(unittest.TestCase):
    def test_frames_reject_lengths_versions_and_truncation(self):
        packet = wire.encode(wire.INPUT, b'\x00\xff\x1b_Gabc\x1b\\')
        self.assertEqual(packet[:12], struct.pack('!IHHI', 0x4b504231, 1, 2, 10))
        self.assertEqual(wire.decode(packet), (wire.INPUT, packet[12:]))
        for data in (b'', packet[:-1], packet + b'x', b'xxxx' + packet[4:],
                     packet[:4] + b'\x00\x02' + packet[6:]):
            with self.assertRaises(ValueError):
                wire.decode(data)
        with self.assertRaises(ValueError):
            wire.encode(wire.INPUT, b'x' * (wire.MAX_PAYLOAD + 1))

    def test_status_matches_original_c_layout(self):
        self.assertEqual(wire.STATUS_DATA.size, 4744)
        record = dict(id='session', broker_pid=123, child_pid=456, cwd='C:\\work', command='cmd.exe',
                      started_millis=789, journal_bytes=5000, journal_epoch=2, attached=True,
                      replay_complete=False, rows=37, columns=111)
        raw = wire.status_bytes(record)
        self.assertEqual(raw[8:16], struct.pack('!Q', 123))
        self.assertEqual(raw[68:76], b'session\0')
        self.assertEqual(raw[133:141], b'C:\\work\0')
        decoded = wire.status_dict(raw)
        self.assertTrue(all(decoded[key] == value for key, value in record.items()))

    def test_bounded_journal_discards_epoch_and_reports_incomplete(self):
        journal = wire.Journal(32768)
        journal.append(b'a' * 20000)
        snapshot = tuple(journal.chunks)
        journal.append(b'b' * 20000)
        self.assertEqual(journal.size, 20000)
        self.assertEqual(journal.epoch, 1)
        self.assertFalse(journal.complete)
        self.assertEqual(b''.join(snapshot), b'a' * 20000)
        self.assertEqual(b''.join(journal.chunks), b'b' * 20000)

    def test_resize_fragmentation_and_ordinary_escape_input(self):
        packet = b'abc\x1b[8;37;111tdef\x1b[A'
        for boundary in range(len(packet) + 1):
            decoder = wire.ResizeDecoder()
            result = decoder.feed(packet[:boundary]) + decoder.feed(packet[boundary:])
            self.assertEqual([payload for kind, payload in result if kind == wire.RESIZE],
                             [wire.SIZE.pack(37, 111, 0, 0)])
            self.assertEqual(b''.join(payload for kind, payload in result if kind == wire.INPUT) + decoder.flush(),
                             b'abcdef\x1b[A')
        decoder = wire.ResizeDecoder()
        self.assertEqual(decoder.feed(b'\x1b'), [])
        self.assertEqual(decoder.flush(), b'\x1b')

    def test_cursor_reports_and_keys_are_not_split_into_escape_key_writes(self):
        for packet in (b'\x1b[1;1R', b'\x1b[?1;2c', b'\x1b[A', b'\x1b[1;5D', b'\x1bOA'):
            for boundary in range(len(packet) + 1):
                with self.subTest(packet=packet, boundary=boundary):
                    decoder = wire.ResizeDecoder()
                    result = decoder.feed(packet[:boundary]) + decoder.feed(packet[boundary:])
                    self.assertEqual(result, [(wire.INPUT, packet)])
                    self.assertEqual(decoder.flush(), b'')
            decoder = wire.ResizeDecoder()
            result = [frame for value in packet for frame in decoder.feed(bytes([value]))]
            self.assertEqual(result, [(wire.INPUT, packet)])
        decoder = wire.ResizeDecoder()
        ordinary = b'abc\x1b[1;1R\x1b[A\x03'
        self.assertEqual(decoder.feed(ordinary), [(wire.INPUT, ordinary)])
        self.assertEqual(decoder.feed(b'\x1b[' + b'1;' * 200), [(wire.INPUT, b'\x1b[' + b'1;' * 200)])

    def test_replay_skips_answered_queries_and_keeps_pending_requests_and_graphics(self):
        decoration = b'\x1b[32mrender\x1b[0m\x1b[?25h'
        # CSI-looking bytes inside a control string are opaque, not requests.
        graphics = b'\x1b_Ga=T;opaque\x1b[6n\x1b\\'
        title = b'\x1b]0;title\x1b[6n\x07'
        raw = b'\x1b[c' + decoration + graphics + title + b'\x1b[6n'
        for boundary in range(len(raw) + 1):
            chunks = (raw[:boundary], raw[boundary:])
            queries = wire.TerminalQueries()
            for chunk in chunks:
                queries.output.feed(chunk)
            queries.input.feed(b'\x1b[?62;')
            queries.input.feed(b'4c')
            unanswered = frozenset(queries.pending.values())
            self.assertEqual(b''.join(queries.replay(chunks, 0, unanswered)),
                             decoration + graphics + title + b'\x1b[6n')
            queries.input.feed(b'\x1b[1;1R')
            self.assertEqual(b''.join(queries.replay(chunks, 0, frozenset(queries.pending.values()))),
                             decoration + graphics + title)
        # Preserve only the newest pending query, even after journal eviction.
        raw = b'\x1b[6nold\x1b[6nnew'
        queries = wire.TerminalQueries()
        queries.output.feed(raw)
        self.assertEqual(b''.join(queries.replay([raw], 0, frozenset(queries.pending.values()))),
                         b'old\x1b[6nnew')
        self.assertEqual(b''.join(queries.replay([raw[7:]], 7, frozenset(queries.pending.values()))),
                         b'\x1b[6nnew')
        # A partial sequence at the snapshot boundary still joins live bytes.
        self.assertEqual(b''.join(queries.replay([b'abc\x1b['], 0, frozenset())), b'abc\x1b[')


@unittest.skipIf(os.name == 'nt', 'Portable server exercise uses a real POSIX PTY; see engine Windows tests')
class ServerProcessTests(unittest.TestCase):
    def test_shared_server_logic_reconnects_real_process_and_returns_exit_status(self):
        import fcntl
        import pty
        import termios
        import tty
        from multiprocessing.connection import Connection

        class Pty:
            def __init__(self):
                self.master, slave = pty.openpty()
                tty.setraw(slave)
                os.set_blocking(self.master, False)
                code = ("import os,sys; n=0; print('READY',flush=True)\n"
                        "for line in sys.stdin:\n"
                        " if line.strip()=='inc': n+=1; print('INCREMENTED',flush=True)\n"
                        " elif line.strip()=='query': print('\\x1b[6nQUERY_END',flush=True)\n"
                        " elif line.strip()=='state': print('STATE',os.getpid(),n,flush=True)\n"
                        " elif line.strip()=='exit': sys.exit(17)\n")
                self.child = subprocess.Popen([sys.executable, '-u', '-c', code],
                                              stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
                self.pid = self.child.pid
                os.close(slave)
            def read(self):
                try:
                    return os.read(self.master, 32768)
                except BlockingIOError:
                    return None
                except OSError:
                    return b''
            def input(self, kind, payload):
                if kind == wire.RESIZE:
                    rows, cols, x, y = wire.SIZE.unpack(payload)
                    fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, x, y))
                else:
                    os.write(self.master, payload)
            def poll(self):
                return self.child.poll()
            def close(self):
                if self.child.poll() is None:
                    self.child.kill()
                self.child.wait(timeout=5)
                os.close(self.master)

        native = types.ModuleType('kitty.windows_pty_ipc')
        native.check_peer = lambda connection, server: None
        native.close_connection = lambda connection: connection.close()
        process = Pty()
        session = broker.Session(dict(id='test', rows=24, columns=80, cwd='/tmp', argv=['fixture']), process)
        clients = []
        runner = threading.Thread(target=session.run, daemon=True)
        with patch.dict(sys.modules, {'kitty.windows_pty_ipc': native}):
            def client():
                left, right = socket.socketpair()
                a, b = Connection(left.detach()), Connection(right.detach())
                clients.append(a)
                threading.Thread(target=session.serve_client, args=(b,), daemon=True).start()
                wire.send(a, wire.ATTACH, wire.SIZE.pack(24, 80, 0, 0))
                return a
            def output_until(connection, marker):
                output = bytearray()
                def receive():
                    if connection.poll(.05):
                        kind, payload = wire.receive(connection)
                        if kind == wire.OUTPUT:
                            output.extend(payload)
                    return marker in output
                eventually(receive)
                return output
            runner.start()
            try:
                first = client()
                output_until(first, b'READY')
                wire.send(first, wire.INPUT, b'inc\n')
                output_until(first, b'INCREMENTED')
                wire.send(first, wire.INPUT, b'query\n')
                self.assertIn(b'\x1b[6n', output_until(first, b'QUERY_END'))
                wire.send(first, wire.INPUT, b'\x1b[1;1R\n')
                eventually(lambda: not session.queries.pending)
                first.close()
                eventually(lambda: session.attached is None)
                self.assertIsNone(process.poll())
                second = client()
                self.assertNotIn(b'\x1b[6n', output_until(second, b'QUERY_END'))
                wire.send(second, wire.INPUT, b'state\n')
                output_until(second, f'STATE {process.pid} 1'.encode())
                # A malformed client must not kill the shared input worker.
                wire.send(second, wire.RESIZE, wire.SIZE.pack(0, 80, 0, 0))
                eventually(lambda: session.attached is None)
                self.assertIsNone(process.poll())
                third = client()
                output_until(third, b'INCREMENTED')
                wire.send(third, wire.INPUT, b'inc\nstate\n')
                output_until(third, f'STATE {process.pid} 2'.encode())
                wire.send(third, wire.RESIZE, wire.SIZE.pack(37, 111, 0, 0))
                eventually(lambda: session.columns == 111)
                wire.send(third, wire.INPUT, b'exit\n')
                status = []
                def exited():
                    if third.poll(.05):
                        kind, payload = wire.receive(third)
                        if kind == wire.EXIT:
                            status.append(wire.EXIT_STATUS.unpack(payload)[0] >> 8)
                    return bool(status)
                eventually(exited)
                self.assertEqual(status, [17])
            finally:
                session.stopping.set()
                for connection in clients:
                    connection.close()
                runner.join(timeout=5)
                self.assertFalse(runner.is_alive())


if __name__ == '__main__':
    unittest.main()
