"""Native notification identifiers, signal ordering and caller ownership."""
from pathlib import Path
import json
import shutil
import subprocess
import unittest

try:
    from gi.repository import Gio
except ImportError:
    Gio = None

ROOT = Path(__file__).resolve().parents[1]


class NotificationBridgeTests(unittest.TestCase):
    @unittest.skipUnless(Gio and shutil.which('dbus-run-session'),
                         'System GI and private D-Bus required')
    def test_owned_buses_preserve_native_notifications_and_client_isolation(self):
        result = subprocess.run([
            'dbus-run-session', '--', '/usr/bin/python3',
            str(ROOT / 'tests/fixtures/notification_bridge_fixture.py'),
            'host', str(ROOT / 'config/kilix_sdk/portal_bridge.py'),
        ], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        proof = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(all(proof.values()), proof)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('CRITICAL', result.stderr)


if __name__ == '__main__':
    unittest.main()
