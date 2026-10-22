"""Ancestor event ordering when a primary exits during the blocking wait."""
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_sdk import bus_supervisor


class AncestorEvents(unittest.TestCase):
    def native(self, observations):
        native = object.__new__(bus_supervisor.NativeAncestor)
        native.sdk_fd, native.owner_fd, native.primary_fd = 11, 12, 13
        native.control = object()
        native.stopping = False
        native.controller_fd = None
        native.ops = mock.Mock()
        native.ops.ready.side_effect = observations
        native.packet_ready = mock.Mock(return_value=False)
        native.collect_primary = mock.Mock()
        return native

    def test_primary_exit_during_wait_is_not_misreported_as_owner_loss(self):
        native = self.native([False, True])
        with mock.patch.object(bus_supervisor.select, 'select',
                               return_value=([native.owner_fd], [], [])) as wait:
            self.assertEqual(native.wait_event(), 'primary-exit')
        self.assertIn(native.primary_fd, wait.call_args.args[0])
        native.collect_primary.assert_called_once_with()

    def test_actual_owner_loss_with_a_live_primary_remains_a_fault(self):
        native = self.native([False, False])
        with mock.patch.object(bus_supervisor.select, 'select',
                               return_value=([native.owner_fd], [], [])):
            self.assertEqual(native.wait_event(), 'owner-loss')
        native.collect_primary.assert_not_called()

    def test_queued_close_still_precedes_simultaneous_primary_exit(self):
        native = self.native([False])
        with mock.patch.object(bus_supervisor.select, 'select',
                               return_value=([native.control, native.primary_fd], [], [])):
            self.assertEqual(native.wait_event(), 'packet')
        native.collect_primary.assert_not_called()
