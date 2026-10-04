"""Portal identity/type invariants and two-bus FD/lifetime integration."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
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


@unittest.skipUnless(Gio, "system python3-gi is required")
class DesktopMessageLimitsTests(unittest.TestCase):
    def setUp(self):
        self.idles = []
        self.sent = []
        self.relay = bridge.PortalRelay.__new__(bridge.PortalRelay)
        self.relay.Gio = Gio
        self.relay.GLib = SimpleNamespace(
            Error=GLib.Error, idle_add=lambda *args: self.idles.append(args))
        self.relay.private = SimpleNamespace(
            get_unique_name=lambda: ":1.1",
            send_message=lambda message, _flags: self.sent.append(message))
        self.relay.clients = {}
        self.relay.queue_lock = threading.Lock()
        self.relay.payload_sizes = {}
        self.relay.retained_bytes = self.relay.queued = 0
        self.relay.stopped = False

    def message(self, serial, size=64, *, no_reply=False, path=bridge.ROOT):
        message = Gio.DBusMessage.new_method_call(
            bridge.PORTAL, path, "org.freedesktop.portal.Notification", "AddNotification")
        message.set_sender(":1.5")
        message.set_serial(serial)
        message.set_body(GLib.Variant("(ay)", (bytes(size),)))
        if no_reply:
            message.set_flags(Gio.DBusMessageFlags.NO_REPLY_EXPECTED)
        return message

    def retain(self, message):
        self.assertIsNone(self.relay.filter_message(self.relay.private, message, True, None))

    def test_single_and_aggregate_limits_recover_after_completed_calls(self):
        self.relay.MAX_MESSAGE_BYTES = 2048
        self.relay.MAX_RETAINED_BYTES = 2600
        oversized = self.message(1, 2048)
        self.retain(oversized)
        self.assertEqual(self.relay.retained_bytes, 0)
        self.assertEqual(self.sent[-1].get_error_name(), "org.freedesktop.DBus.Error.LimitsExceeded")
        first, second, third = (self.message(serial) for serial in (2, 3, 4))
        self.retain(first)
        self.retain(second)
        retained = self.relay.retained_bytes
        self.retain(third)
        self.assertEqual(self.relay.retained_bytes, retained)
        self.assertEqual(len(self.idles), 2)
        self.relay.release_message(first)
        self.retain(third)
        self.assertEqual(len(self.idles), 3)
        self.relay.release_message(second)
        self.relay.release_message(third)
        self.assertEqual(self.relay.retained_bytes, 0)
        self.assertFalse(self.relay.payload_sizes)

    def test_duplicate_serial_does_not_release_the_original_reservation(self):
        original = self.message(1)
        self.retain(original)
        retained = self.relay.retained_bytes
        self.retain(original.copy())
        self.assertEqual(self.relay.retained_bytes, retained)
        self.assertEqual(self.relay.queued, 1)
        self.assertEqual(len(self.idles), 1)
        self.relay.stopped = True
        callback, pending = self.idles.pop()
        callback(pending)
        self.assertEqual(self.relay.retained_bytes, 0)
        self.assertEqual(self.relay.queued, 0)

    def test_fire_and_forget_error_and_unknown_object_release_their_data(self):
        original = self.message(1, no_reply=True)
        self.retain(original)
        self.relay.error(original, "connection closed")
        self.assertEqual(self.relay.retained_bytes, 0)
        self.assertFalse(self.sent)
        unknown = self.message(2, path="/org/example/Unknown")
        self.retain(unknown)
        callback, pending = self.idles[-1]
        callback(pending)
        self.assertEqual(self.relay.retained_bytes, 0)
        self.assertEqual(self.sent[-1].get_error_name(), "org.freedesktop.DBus.Error.UnknownObject")

    def test_notification_queue_counts_toward_each_clients_call_limit(self):
        client = bridge.Client(":1.5", Gio.Cancellable(), connection=object())
        client.notifications.queue = [object()] * self.relay.MAX_CALLS
        self.relay.clients[client.name] = client
        self.retain(self.message(1))
        callback, pending = self.idles.pop()
        callback(pending)
        self.assertEqual(self.relay.retained_bytes, 0)
        self.assertEqual(self.sent[-1].get_error_name(), "org.freedesktop.DBus.Error.LimitsExceeded")


if __name__ == "__main__":
    unittest.main()
