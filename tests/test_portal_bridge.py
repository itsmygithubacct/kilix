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

    @staticmethod
    def cleared(interface, member, signature, values):
        message = Gio.DBusMessage.new_method_call(bridge.PORTAL, bridge.ROOT, interface, member)
        message.set_body(GLib.Variant(signature, values))
        bridge.clear_private_parent(message)
        return message.get_body().unpack()

    @unittest.skipUnless(Gio, "system python3-gi is required")
    def test_private_xid_is_not_used_to_parent_a_host_dialog(self):
        portal = "org.freedesktop.portal."
        session = bridge.ROOT + "/session/1_5/s"
        for interface, member, signature, values, index in (
                ("ScreenCast", "Start", "(osa{sv})", (session, "x11:1a2b", {}), 1),
                ("RemoteDesktop", "Start", "(osa{sv})", (session, "x11:1a2b", {}), 1),
                ("GlobalShortcuts", "BindShortcuts", "(oa(sa{sv})sa{sv})",
                 (session, [("id", {"description": GLib.Variant("s", "x11:keep")})], "x11:1a2b", {}), 2),
                ("FileChooser", "OpenFile", "(ssa{sv})", ("x11:1a2b", "Open", {}), 0),
                ("Print", "Print", "(ssha{sv})", ("x11:1a2b", "x11:title", 0, {}), 0),
                # Methods review A found missing from the former fixed table.
                ("Email", "ComposeEmail", "(sa{sv})", ("x11:1a2b", {}), 0),
                ("Location", "Start", "(osa{sv})", (session, "x11:1a2b", {}), 1),
                ("InputCapture", "CreateSession", "(sa{sv})", ("x11:1a2b", {}), 0),
                ("DynamicLauncher", "PrepareInstall", "(ssva{sv})",
                 ("x11:1a2b", "App", GLib.Variant("s", "icon"), {}), 0),
                ("Inhibit", "CreateMonitor", "(sa{sv})", ("x11:1a2b", {}), 0),
                # A portal method this relay has never heard of is covered too.
                ("FutureDialog", "Ask", "(usa{sv})", (7, "x11:1a2b", {}), 1)):
            with self.subTest(interface=interface, member=member):
                body = self.cleared(portal + interface, member, signature, values)
                self.assertEqual(body[index], "")
                expected = list(values)
                expected[index] = ""
                self.assertEqual(body, GLib.Variant(signature, tuple(expected)).unpack())

    @unittest.skipUnless(Gio, "system python3-gi is required")
    def test_caller_data_and_non_portal_strings_are_not_cleared(self):
        for interface, member, signature, values in (
                ("org.freedesktop.portal.Notification", "AddNotification", "(sa{sv})", ("x11:id", {})),
                ("org.freedesktop.portal.Settings", "Read", "(ss)", ("x11:ns", "key")),
                ("org.freedesktop.portal.OpenURI", "SchemeSupported", "(sa{sv})", ("x11:", {})),
                ("org.freedesktop.portal.OpenURI", "OpenURI", "(ssa{sv})", ("wayland:abc", "x11:uri", {})),
                ("org.freedesktop.DBus.Properties", "Get", "(ss)", ("x11:iface", "version")),
                ("org.a11y.Status", "Probe", "(s)", ("x11:1a2b",))):
            with self.subTest(interface=interface, member=member):
                self.assertEqual(self.cleared(interface, member, signature, values),
                                 GLib.Variant(signature, values).unpack())

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
        # Every check the fixture performs must report, and report True; a
        # fixture that returned early with fewer keys must not pass.
        self.assertEqual(proof, dict.fromkeys((
            "request_session_identity", "fd_in_and_out", "singleton_names_remain_private",
            "two_clients_have_separate_host_connections",
            "accessibility_discovery_uses_physical_registry",
            "accessibility_status_properties_and_signals_stay_typed",
            "client_disconnect_and_hard_app_death_close_host_connections"), True))


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


class FakeHostConnection:
    def __init__(self, unique=":1.40"):
        self.unique = unique
        self.sent = []
        self.pending = []
        self.closed = False

    def get_unique_name(self):
        return self.unique

    def is_closed(self):
        return False

    def send_message(self, message, _flags):
        self.sent.append(message)

    def send_message_with_reply(self, message, _flags, _timeout, _cancellable, callback, state):
        message.set_serial(len(self.sent) + 100)
        self.sent.append(message)
        self.pending.append((message, callback, state))

    def send_message_with_reply_finish(self, result):
        return result

    def close(self, *_args):
        self.closed = True

    def answer_pending(self, body=None):
        """Deliver the host's successful reply after the relay has moved on."""
        for message, callback, state in self.pending:
            reply = message.new_method_reply()
            if body is not None:
                reply.set_body(body)
            callback(self, reply, state)
        self.pending.clear()


@unittest.skipUnless(Gio, "system python3-gi is required")
class RelayRoutingTests(unittest.TestCase):
    """Drive the relay's own send/signal/reply paths, not their helpers."""

    def setUp(self):
        self.sent = []
        self.relay = bridge.PortalRelay.__new__(bridge.PortalRelay)
        self.relay.Gio, self.relay.GLib = Gio, GLib
        self.relay.private = SimpleNamespace(
            is_closed=lambda: False,
            send_message=lambda message, _flags: self.sent.append(message))
        self.relay.queue_lock = threading.Lock()
        self.relay.payload_sizes = {}
        self.relay.retained_bytes = 0
        self.relay.notifications = bridge.NativeNotifications(self.relay)
        self.host = FakeHostConnection()
        self.client = bridge.Client(":1.5", Gio.Cancellable(), connection=self.host)
        self.relay.clients = {self.client.name: self.client}

    def call(self, interface, member, signature=None, values=None, path=bridge.ROOT, serial=7):
        message = Gio.DBusMessage.new_method_call(bridge.PORTAL, path, interface, member)
        message.set_sender(self.client.name)
        message.set_serial(serial)
        if signature:
            message.set_body(GLib.Variant(signature, values))
        return message

    def denied(self):
        self.assertFalse(self.host.sent)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0].get_error_name(), "org.freedesktop.DBus.Error.AccessDenied")

    def test_another_clients_object_path_is_refused_not_forwarded(self):
        self.relay.send(self.client, self.call(
            "org.freedesktop.portal.Session", "Close", path=bridge.ROOT + "/session/1_6/theirs"))
        self.denied()

    def test_another_clients_handle_in_the_body_is_refused_not_forwarded(self):
        self.relay.send(self.client, self.call(
            "org.freedesktop.portal.ScreenCast", "Start", "(osa{sv})",
            (bridge.ROOT + "/session/1_6/theirs", "", {})))
        self.denied()

    def test_forwarded_calls_use_host_handles_and_lose_private_parents(self):
        self.relay.send(self.client, self.call(
            "org.freedesktop.portal.ScreenCast", "Start", "(osa{sv})",
            (bridge.ROOT + "/session/1_5/mine", "x11:1a2b", {})))
        self.assertFalse(self.sent)
        (forwarded,) = self.host.sent
        self.assertEqual(forwarded.get_destination(), bridge.PORTAL)
        self.assertEqual(forwarded.get_body().unpack(), (bridge.ROOT + "/session/1_40/mine", "", {}))
        self.assertEqual(self.client.inflight, 1)

    def test_calls_in_flight_when_the_host_connection_closes_get_an_error_reply(self):
        portal = self.call("org.freedesktop.portal.Screenshot", "Screenshot", "(sa{sv})", ("", {}), serial=7)
        notify = self.call(bridge.NOTIFICATIONS, "Notify", "(susssasa{sv}i)",
                           ("app", 0, "", "summary", "body", [], {}, -1),
                           path=bridge.NOTIFICATION_PATH, serial=8)
        self.relay.send(self.client, portal)
        self.relay.send(self.client, notify)
        self.assertEqual(len(self.host.sent), 2)
        self.assertFalse(self.sent)
        # The host connection's "closed" handler runs before GIO dispatches
        # the replies that were already on their way.
        self.relay.close_client(self.client, notify=True)
        self.host.answer_pending(GLib.Variant("(u)", (9,)))
        replies = {m.get_reply_serial(): m for m in self.sent
                   if m.get_message_type() == Gio.DBusMessageType.ERROR}
        self.assertEqual(sorted(replies), [7, 8])
        for reply in replies.values():
            self.assertEqual(reply.get_error_name(), "org.freedesktop.DBus.Error.Failed")
            self.assertEqual(reply.get_destination(), self.client.name)
        self.assertEqual(self.client.inflight, 0)

    def signal(self, path, interface, member, signature, values):
        self.relay.portal_signal(self.host, ":1.2", path, interface, member,
                                 GLib.Variant(signature, values), self.client)

    def test_only_this_connections_request_and_session_signals_are_relayed(self):
        response = ("org.freedesktop.portal.Request", "Response", "(ua{sv})")
        for foreign in (bridge.ROOT + "/request/1_41/t", bridge.ROOT + "/request/1_4/t"):
            self.signal(foreign, *response, (0, {"session_handle": GLib.Variant("s", foreign)}))
        self.signal(bridge.ROOT + "/session/1_41/s", "org.freedesktop.portal.Session", "Closed",
                    "(a{sv})", ({},))
        self.assertFalse(self.sent)
        self.assertFalse(self.client.responses or self.client.sessions)
        session = bridge.ROOT + "/session/1_40/s"
        self.signal(bridge.ROOT + "/request/1_40/t", *response,
                    (0, {"session_handle": GLib.Variant("s", session)}))
        self.signal(bridge.ROOT, "org.freedesktop.portal.Settings", "SettingChanged",
                    "(ssv)", ("org.example", "key", GLib.Variant("b", True)))
        self.assertEqual([(m.get_path(), m.get_member(), m.get_destination()) for m in self.sent], [
            (bridge.ROOT + "/request/1_5/t", "Response", ":1.5"),
            (bridge.ROOT, "SettingChanged", ":1.5")])
        self.assertEqual(self.client.responses, {bridge.ROOT + "/request/1_5/t"})
        self.assertEqual(self.client.sessions, {bridge.ROOT + "/session/1_5/s"})


if __name__ == "__main__":
    unittest.main()
