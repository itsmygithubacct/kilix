"""Portal identity/type invariants and two-bus FD/lifetime integration."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
from kilix_sdk import portal_bridge as bridge

try:
    from gi.repository import Gio, GLib
except ImportError:
    Gio = GLib = None


class PortalPathTests(unittest.TestCase):
    def test_only_the_clients_request_and_session_handles_are_translated(self):
        for kind in ("request", "session"):
            path = f"{bridge.ROOT}/{kind}/1_5/test"
            self.assertEqual(bridge.translate_path(path, ":1.5", ":1.40"),
                             f"{bridge.ROOT}/{kind}/1_40/test")
            self.assertEqual(bridge.translate_path(path, ":1.6", ":1.40"), path)
        self.assertEqual(bridge.translate_path("/tmp/1_5/test", ":1.5", ":1.40"),
                         "/tmp/1_5/test")
        self.assertFalse(bridge.portal_path(bridge.ROOT + "-other"))
        self.assertTrue(bridge.portal_path(bridge.ROOT + "/request/1_5/test"))
        with self.assertRaises(ValueError):
            bridge.translate_path(bridge.ROOT, "org.example.App", ":1.5")
        with self.assertRaises(ValueError):
            bridge.translate_path(bridge.ROOT + "/session/1_6/test", ":1.5", ":1.40", strict=True)

    @unittest.skipUnless(GLib, "system python3-gi is required")
    def test_nested_variants_keep_types_fd_indices_and_legacy_handles(self):
        path = f"{bridge.ROOT}/session/1_5/test"
        value = GLib.Variant("(oa{sv}ah)", (path, {
            "session_handle": GLib.Variant("s", path),
            "nested": GLib.Variant("a(ua{sv})", [(3, {"size": GLib.Variant("(ii)", (640, 480))})]),
            "empty": GLib.Variant("a{sv}", {}),
            "image": GLib.Variant("ay", b"pixels" * 1024),
        }, [0, 2]))
        mapped = bridge.translate_variant(value, ":1.5", ":1.40")
        self.assertEqual(mapped.get_type_string(), value.get_type_string())
        self.assertEqual(mapped.unpack()[0], f"{bridge.ROOT}/session/1_40/test")
        self.assertEqual(mapped.unpack()[1]["session_handle"], mapped.unpack()[0])
        self.assertEqual(mapped.unpack()[2], [0, 2])
        self.assertEqual(bridge.translate_variant(mapped, ":1.40", ":1.5"), value)

    @unittest.skipUnless(Gio, "system python3-gi is required")
    def test_private_xid_is_not_used_to_parent_a_host_dialog(self):
        message = Gio.DBusMessage.new_method_call(bridge.PORTAL, bridge.ROOT,
                                                  "org.freedesktop.portal.ScreenCast", "Start")
        message.set_body(GLib.Variant("(osa{sv})", (bridge.ROOT, "x11:1a2b", {})))
        bridge.clear_private_parent(message)
        self.assertEqual(message.get_body().unpack()[1], "")

    @unittest.skipUnless(Gio and shutil.which("dbus-run-session"), "GI and private D-Bus needed")
    def test_real_private_buses_forward_fds_and_cleanup_client_sessions(self):
        fixture = Path(__file__).with_name("fixtures") / "portal_bridge_fixture.py"
        result = subprocess.run(["dbus-run-session", "--", "/usr/bin/python3", str(fixture),
                                 "host", str(ROOT / "config/kilix_sdk/portal_bridge.py")],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("CRITICAL", result.stderr)
        self.assertNotIn("TypeError", result.stderr)
        proof = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(all(proof.values()), proof)


if __name__ == "__main__":
    unittest.main()
