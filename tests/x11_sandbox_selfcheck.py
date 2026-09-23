"""Assertions that run *inside* the X test sandbox's child.

Not collected by discovery (the name does not match ``test_*.py``);
``tests/test_x11_sandbox.py`` runs it through the sandbox and checks the
recorded outcomes.  It starts no X server and opens no connection.
"""

import os
import unittest

import x11_sandbox


class InsideTheSandbox(unittest.TestCase):
    def test_no_display_or_cookie_was_inherited(self):
        self.assertFalse("DISPLAY" in os.environ)
        self.assertFalse("XAUTHORITY" in os.environ)
        self.assertFalse("KILIX_DATA_HOME" in os.environ)
        self.assertFalse("GPU_TERMINAL_HOME" in os.environ)

    def test_the_network_namespace_is_private_and_holds_no_x11_listener(self):
        self.assertNotEqual(
            str(os.stat("/proc/self/ns/net").st_ino),
            os.environ[x11_sandbox.OUTER_NETNS_ENV])
        self.assertEqual(x11_sandbox.x11_listeners(), [])

    def test_the_harness_precondition_holds(self):
        self.assertEqual(x11_sandbox.require_private_x11(), [])
