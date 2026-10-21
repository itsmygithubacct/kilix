"""The restricted root prelude must finish modes before handing off ownership."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_sdk import bus_supervisor


class WorkloadPermissions(unittest.TestCase):
    def test_allocation_needs_chown_capability_but_not_fowner(self):
        boundary = SimpleNamespace(outer=SimpleNamespace(join_fd=101),
                                   inner=SimpleNamespace(join_fd=102))
        native = object.__new__(bus_supervisor.NativeAncestor)
        native.root, native.token, native.uid = object(), 'fixture', 1000
        native.session_path = None
        native.delegation = SimpleNamespace(boundary=lambda *_: boundary)
        ownership = {101: 0, 102: 0}
        modes = {}

        def chmod(fd, mode):
            # CAP_FOWNER is absent from the fixed unit's bounding set.
            if ownership[fd] != 0:
                raise PermissionError('chmod after caller ownership requires CAP_FOWNER')
            modes[fd] = mode

        def chown(fd, uid, gid):
            self.assertEqual(gid, 0)
            ownership[fd] = uid

        with mock.patch.object(bus_supervisor.os, 'fchmod', side_effect=chmod), \
                mock.patch.object(bus_supervisor.os, 'fchown', side_effect=chown):
            self.assertIs(native.allocate(), boundary)
        self.assertEqual(ownership, {101: 1000, 102: 1000})
        self.assertEqual(modes, {101: 0o660, 102: 0o660})
