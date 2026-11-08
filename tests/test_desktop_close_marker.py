"""Exercise the real desktop launcher without running a desktop or system action."""
import base64
import re
import subprocess
import unittest

import test_desktop_existing_provider as fixture_module


class DesktopCloseMarker(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.ExistingDesktopProviderTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def run_provider(self, command):
        env = dict(self.fixture.env, KILIX_IN_OVERLAY='1',
                   KILIX_DESKTOP_PROVIDER='command', KILIX_DESKTOP_COMMAND=command)
        return subprocess.run([str(fixture_module.ROOT / 'kilix'), 'desktop'], env=env,
                              capture_output=True, timeout=15)

    def test_custom_provider_is_marked_only_while_running(self):
        result = self.run_provider('printf desktop-body')
        self.assertEqual(result.returncode, 0, result.stderr)
        marker = self.marker(result.stdout)
        self.assertIn(b'\x07desktop-body', result.stdout)
        self.assertRegex(marker, r'^[0-9a-f]{32}$')
        self.assertTrue(result.stdout.endswith(b'\x1b]1337;SetUserVar=kilix_desktop_provider\x07'))

    def test_failed_provider_clears_marker_and_keeps_failure_status(self):
        result = self.run_provider('exit 7')
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertRegex(self.marker(result.stdout), r'^[0-9a-f]{32}$')
        self.assertTrue(result.stdout.endswith(b'\x1b]1337;SetUserVar=kilix_desktop_provider\x07'))

    def marker(self, output):
        encoded = re.search(rb'SetUserVar=kilix_desktop_provider=([^\x07]+)\x07', output)
        self.assertIsNotNone(encoded)
        return base64.b64decode(encoded.group(1), validate=True).decode('ascii')

    def test_each_provider_launch_has_a_distinct_identity(self):
        first = self.run_provider('true')
        second = self.run_provider('true')
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(self.marker(first.stdout), self.marker(second.stdout))
