"""Received leases survive transfer without requiring a producer registry."""
import os
from pathlib import Path
import socket
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_sdk.bus_supervision import Handles
from kilix_sdk.private_bus import receive_pidfd, release_received, send_pidfd


class ReceivedDescriptorRelease(unittest.TestCase):
    def test_transferred_boundary_lease_releases_without_acquisition_owner(self):
        handles = Handles(os.close, os)
        self.addCleanup(handles.close)
        fd = handles.allocate(os.open, os.devnull, os.O_RDONLY)
        lease = fd.acquisition_lease
        handles.transfer_many([fd])
        self.assertFalse(hasattr(fd, 'acquisition_owner'))
        release_received(fd)
        self.assertIsNone(lease.fd)
        release_received(fd)
        self.assertEqual(handles.fds, [])

    def test_single_received_descriptor_retires_its_retained_registry(self):
        sender, receiver = socket.socketpair(type=socket.SOCK_SEQPACKET)
        self.addCleanup(sender.close)
        self.addCleanup(receiver.close)
        with open(os.devnull, 'rb') as source:
            send_pidfd(sender, b'A', source.fileno())
            data, fd = receive_pidfd(receiver, 1)
        self.addCleanup(fd.acquisition_owner.close)
        self.assertEqual(data, b'A')
        release_received(fd)
        self.assertIsNone(fd.acquisition_lease.fd)
        self.assertEqual(fd.acquisition_owner.fds, [])
        release_received(fd)
