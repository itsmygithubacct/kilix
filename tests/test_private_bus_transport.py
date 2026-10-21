"""Final cleanup receipts survive an ordinary close arriving during retirement."""
import json
from pathlib import Path
import socket
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_sdk.bus_supervisor import NativeAncestor, OwnedSocket


class ReceiptTransport(unittest.TestCase):
    def test_retirement_preserves_receipt_with_a_queued_close(self):
        for queued_first in (False, True):
            with self.subTest(queued_first=queued_first):
                server, client = socket.socketpair(type=socket.SOCK_SEQPACKET)
                native = object.__new__(NativeAncestor)
                native.control = OwnedSocket(server).capture()
                native.listener = None
                self.addCleanup(client.close)
                self.addCleanup(native.close_connections)
                receipt = json.dumps({'complete': True}).encode()
                if queued_first:
                    client.send(b'close')
                server.send(receipt)
                if not queued_first:
                    client.send(b'close')
                native.close_connections()
                self.assertEqual(client.recv(1024), receipt)
                self.assertEqual(client.recv(1024), b'')
                self.assertIsNone(native.control)
