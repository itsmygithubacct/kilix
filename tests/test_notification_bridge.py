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
        # The exact set: a fixture that stops early must not pass.
        self.assertEqual(proof, dict.fromkeys((
            'native_protocol_information', 'private_ids_hide_the_physical_id_namespace',
            'replacement_preserves_private_identifier', 'foreign_close_and_replace_are_denied',
            'actions_are_unicast_to_the_owner', 'closure_invalidates_private_identifier',
            'signal_before_notify_reply_is_preserved',
            'daemon_restart_invalidates_old_ids_and_allows_new_calls',
            'fire_and_forget_notifications_outlive_their_sender',
            'closure_before_replacement_reply_does_not_resurrect_id',
            'queued_requests_and_errors_preserve_client_call_order',
            'no_reply_notify_still_reaches_the_physical_daemon',
            'opaque_image_hints_survive_and_private_window_hint_is_removed'), True))
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('CRITICAL', result.stderr)


if __name__ == '__main__':
    unittest.main()
