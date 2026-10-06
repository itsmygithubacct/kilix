"""Forward desktop services while keeping application singleton buses private.

Each private caller has a separate physical-session bus connection. Portal
request/session paths are translated between those connections' unique names;
other names (including GtkApplication names) stay on the private bus. The relay
uses GIO messages so Unix file descriptors retain their handle indices.

Native notifications use caller-specific identifiers and signal delivery.
Accessibility discovery reaches the physical session's AT-SPI registry.
The launcher waits for ownership of the private service names before execing
the app. Linux parent-death notification ties the relay to that app, even on SIGKILL.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import threading


if __package__:
    from . import accessibility_bus
    from .notifications import (
        NativeNotifications, NotificationState,
        NAME as NOTIFICATIONS, PATH as NOTIFICATION_PATH,
    )
else:
    import accessibility_bus
    from notifications import (
        NativeNotifications, NotificationState,
        NAME as NOTIFICATIONS, PATH as NOTIFICATION_PATH,
    )

PORTAL = "org.freedesktop.portal.Desktop"
ROOT = "/org/freedesktop/portal/desktop"
HOST_BUS = "KILIX_PORTAL_HOST_BUS"
_UNIQUE = re.compile(r"^:[0-9]+(?:\.[0-9]+)+$")
# Every org.freedesktop.portal.* method that names a parent window takes it as
# its first top-level string argument (ScreenCast.Start (osa{sv}), Location.Start,
# GlobalShortcuts.BindShortcuts (oa(sa{sv})sa{sv}), Email.ComposeEmail,
# InputCapture.CreateSession, DynamicLauncher.PrepareInstall, Usb.AcquireDevices,
# ...). Clear that argument when it carries an x11: handle for every portal
# method, including ones added after this was written, except the methods whose
# first string is caller data rather than a window.
_FIRST_STRING_IS_DATA = frozenset({
    ("Notification", "AddNotification"),
    ("Notification", "RemoveNotification"),
    ("DynamicLauncher", "RequestInstallToken"),
    ("DynamicLauncher", "Install"),
    ("DynamicLauncher", "Uninstall"),
    ("DynamicLauncher", "GetDesktopEntry"),
    ("DynamicLauncher", "GetIcon"),
    ("DynamicLauncher", "Launch"),
    ("Settings", "Read"),
    ("Settings", "ReadOne"),
    ("OpenURI", "SchemeSupported"),
    ("ProxyResolver", "Lookup"),
    ("NetworkMonitor", "CanReach"),
})


def portal_path(path: str) -> bool:
    return path == ROOT or path.startswith(ROOT + "/")


def translate_path(path: str, source: str, target: str, *, strict=False) -> str:
    """Translate only this caller's handles, never another caller's paths."""
    if not _UNIQUE.fullmatch(source) or not _UNIQUE.fullmatch(target):
        raise ValueError("portal clients need unique bus names")
    source, target = source[1:].replace(".", "_"), target[1:].replace(".", "_")
    for kind in ("request", "session"):
        prefix = f"{ROOT}/{kind}/{source}/"
        if path.startswith(prefix):
            return f"{ROOT}/{kind}/{target}/" + path[len(prefix):]
        if strict and path.startswith(f"{ROOT}/{kind}/"):
            raise ValueError("portal handle belongs to another private client")
    return path


def own_handle_or_shared(path: str, unique: str) -> bool:
    """A request/session path must belong to this host connection.

    Signals the portal broadcasts rather than unicasts reach every host
    connection; another caller's Response or Closed is not this client's.
    Paths outside request/session (Settings, Notification) are shared.
    """
    owner = unique[1:].replace(".", "_")
    for kind in ("request", "session"):
        if path.startswith(f"{ROOT}/{kind}/"):
            return path.startswith(f"{ROOT}/{kind}/{owner}/")
    return True


def translate_variant(value, source: str, target: str, *, strict=False):
    """Keep exact GVariant types, including legacy string session handles."""
    from gi.repository import GLib
    signature = value.get_type_string()
    if not any(kind in signature for kind in "sov"):
        # In particular, don't allocate one Python/GVariant object per byte
        # of a notification icon or other opaque binary portal value.
        return value
    if signature in ("s", "o"):
        original = value.get_string()
        mapped = translate_path(original, source, target, strict=strict and signature == "o")
        return GLib.Variant(signature, mapped) if mapped != original else value
    if signature == "v":
        return GLib.Variant.new_variant(translate_variant(value.get_variant(), source, target, strict=strict))
    children = [translate_variant(value.get_child_value(i), source, target, strict=strict)
                for i in range(value.n_children())] if value.is_container() else []
    if signature.startswith("a"):
        return GLib.Variant.new_array(GLib.VariantType.new(signature[1:]), children)
    if signature.startswith("("):
        return GLib.Variant.new_tuple(*children)
    if signature.startswith("{"):
        return GLib.Variant.new_dict_entry(*children)
    return value


def clear_private_parent(message):
    """A private XID cannot identify a parent on the physical X server."""
    from gi.repository import GLib
    interface = message.get_interface() or ""
    if not interface.startswith("org.freedesktop.portal."):
        return
    if (interface.removeprefix("org.freedesktop.portal."), message.get_member()) in _FIRST_STRING_IS_DATA:
        return
    body = message.get_body()
    if body is None or not body.get_type_string().startswith("("):
        return
    children = [body.get_child_value(i) for i in range(body.n_children())]
    index = next((i for i, child in enumerate(children) if child.get_type_string() == "s"), None)
    if index is not None and children[index].get_string().startswith("x11:"):
        children[index] = GLib.Variant("s", "")
        message.set_body(GLib.Variant.new_tuple(*children))


@dataclass
class Client:
    name: str
    cancellable: object
    connection: object = None
    waiting: list = field(default_factory=list)
    inflight: int = 0
    sessions: set = field(default_factory=set)
    requests: set = field(default_factory=set)
    responses: set = field(default_factory=set)
    notifications: NotificationState = field(default_factory=NotificationState)


class PortalRelay:
    MAX_CLIENTS = 64
    MAX_CALLS = 64
    MAX_QUEUED = 512
    MAX_MESSAGE_BYTES = 16 * 1024 * 1024
    MAX_RETAINED_BYTES = 64 * 1024 * 1024

    def __init__(self, address: str):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.address = address
        self.loop = GLib.MainLoop()
        self.clients = {}
        self.notifications = NativeNotifications(self)
        self.stopped = False
        self.queued = 0
        self.queue_lock = threading.Lock()
        self.payload_sizes = {}
        self.retained_bytes = 0
        self.private = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.private.set_exit_on_close(False)
        self.private.connect("closed", lambda *_: self.close())
        self.private.signal_subscribe(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
            "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE, self.owner_changed)
        self.filter_id = self.private.add_filter(self.filter_message, None)
        for name in (PORTAL, NOTIFICATIONS, accessibility_bus.NAME):
            response = self.private.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "RequestName", GLib.Variant("(su)", (name, 4)), GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE, 5000, None)
            if response.unpack()[0] != 1:
                raise RuntimeError("private desktop service name is already owned: " + name)

    def release_message(self, original):
        key = (original.get_sender(), original.get_serial())
        with self.queue_lock:
            self.retained_bytes -= self.payload_sizes.pop(key, 0)

    def error(self, original, message, name="org.freedesktop.DBus.Error.Failed", *, release=True):
        if release:
            self.release_message(original)
        if original.get_flags() & self.Gio.DBusMessageFlags.NO_REPLY_EXPECTED:
            return
        try:
            self.private.send_message(original.new_method_error_literal(name, message),
                                      self.Gio.DBusSendMessageFlags.NONE)
        except self.GLib.Error:
            pass  # Caller or private bus has already gone away.

    def filter_message(self, connection, message, incoming, _data):
        if (not incoming or message.get_message_type() != self.Gio.DBusMessageType.METHOD_CALL
                or message.get_destination() not in (PORTAL, NOTIFICATIONS, accessibility_bus.NAME,
                                                     connection.get_unique_name())):
            return message
        # GIO invokes filters on its message thread. All relay state and I/O
        # work runs in the main context, without blocking that thread.
        body = message.get_body()
        size = (body.get_size() if body is not None else 0) + 1024
        key = (message.get_sender(), message.get_serial())
        with self.queue_lock:
            if (self.queued >= self.MAX_QUEUED or size > self.MAX_MESSAGE_BYTES or
                    self.retained_bytes + size > self.MAX_RETAINED_BYTES or key in self.payload_sizes):
                rejected = True
            else:
                rejected = False
                self.queued += 1
                self.payload_sizes[key] = size
                self.retained_bytes += size
        if rejected:
            self.error(message, "Too much pending desktop service data",
                       "org.freedesktop.DBus.Error.LimitsExceeded", release=False)
            return None
        self.GLib.idle_add(self.forward, message.copy())
        return None

    def forward(self, original):
        with self.queue_lock:
            self.queued -= 1
        if self.stopped:
            self.release_message(original)
            return False
        name = original.get_sender() or ""
        if not _UNIQUE.fullmatch(name) or not (portal_path(original.get_path() or "")
                or original.get_path() in (NOTIFICATION_PATH, accessibility_bus.PATH)):
            self.error(original, "Not a desktop portal object",
                       "org.freedesktop.DBus.Error.UnknownObject")
            return False
        client = self.clients.get(name)
        if client is None:
            if len(self.clients) >= self.MAX_CLIENTS:
                self.error(original, "Too many portal clients", "org.freedesktop.DBus.Error.LimitsExceeded")
                return False
            client = Client(name, self.Gio.Cancellable())
            self.clients[name] = client
            # A caller can disconnect between filter dispatch and this idle.
            # Check liveness asynchronously before opening a host connection.
            self.private.call(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", self.GLib.Variant("(s)", (name,)), self.GLib.VariantType.new("(b)"),
                self.Gio.DBusCallFlags.NONE, 5000, client.cancellable,
                self.checked_owner, client)
        if client.inflight + len(client.waiting) + len(client.notifications.queue) >= self.MAX_CALLS:
            self.error(original, "Too many portal calls", "org.freedesktop.DBus.Error.LimitsExceeded")
        elif client.connection is None:
            client.waiting.append(original)
        else:
            self.send(client, original)
        return False

    def checked_owner(self, connection, result, client):
        try:
            alive = connection.call_finish(result).unpack()[0]
        except self.GLib.Error:
            alive = False
        if self.clients.get(client.name) is not client:
            return
        if not alive:
            self.close_client(client)
            return
        timeout = self.GLib.timeout_add_seconds(5, self.connection_timeout, client)
        self.Gio.DBusConnection.new_for_address(
            self.address, self.Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
            self.Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, client.cancellable,
            self.connected, (client, timeout))

    def connection_timeout(self, client):
        if self.clients.get(client.name) is client and client.connection is None:
            self.close_client(client)
        return False

    def connected(self, _source, result, state):
        client, timeout = state
        source = self.GLib.MainContext.default().find_source_by_id(timeout)
        if source is not None:
            source.destroy()
        try:
            connection = self.Gio.DBusConnection.new_for_address_finish(result)
        except self.GLib.Error:
            self.close_client(client)
            return
        connection.set_exit_on_close(False)
        if self.clients.get(client.name) is not client:
            connection.close(None, None, None)
            return
        client.connection = connection
        connection.connect("closed", lambda *_: self.close_client(client, notify=True))
        connection.signal_subscribe(
            PORTAL, None, None, None, None, self.Gio.DBusSignalFlags.NONE,
            self.portal_signal, client)
        connection.signal_subscribe(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
            "/org/freedesktop/DBus", PORTAL, self.Gio.DBusSignalFlags.NONE,
            self.frontend_changed, client)
        self.notifications.connect(client)
        connection.signal_subscribe(
            accessibility_bus.NAME, accessibility_bus.PROPERTIES, 'PropertiesChanged',
            accessibility_bus.PATH, accessibility_bus.STATUS, self.Gio.DBusSignalFlags.NONE,
            self.accessibility_status_changed, client)
        waiting, client.waiting = client.waiting, []
        for original in waiting:
            self.send(client, original)

    def send(self, client, original):
        connection = client.connection
        if (self.clients.get(client.name) is not client or connection is None
                or connection.is_closed()):
            self.error(original, "Desktop portal connection unavailable")
            return
        if original.get_path() == NOTIFICATION_PATH:
            self.notifications.send(client, original)
            return
        accessibility = original.get_path() == accessibility_bus.PATH
        if accessibility and not accessibility_bus.allowed(original):
            self.error(original, 'Invalid accessibility discovery method or arguments',
                       'org.freedesktop.DBus.Error.InvalidArgs')
            return
        message = original.copy()
        message.set_sender(None)
        message.set_destination(accessibility_bus.NAME if accessibility else PORTAL)
        try:
            message.set_path(translate_path(message.get_path(), client.name, connection.get_unique_name(), strict=True))
            if message.get_body() is not None:
                message.set_body(translate_variant(message.get_body(), client.name, connection.get_unique_name(), strict=True))
        except ValueError:
            self.error(original, "Portal handle belongs to another private client",
                       "org.freedesktop.DBus.Error.AccessDenied")
            return
        clear_private_parent(message)
        expects_reply = not message.get_flags() & self.Gio.DBusMessageFlags.NO_REPLY_EXPECTED
        try:
            if not expects_reply:
                connection.send_message(message, self.Gio.DBusSendMessageFlags.NONE)
                self.release_message(original)
                return
            client.inflight += 1
            connection.send_message_with_reply(
                message, self.Gio.DBusSendMessageFlags.NONE, 1500 if accessibility else 2 ** 31 - 1,
                client.cancellable, self.replied, (client, original))
        except self.GLib.Error:
            if expects_reply:
                client.inflight -= 1
            self.error(original, "Desktop portal connection unavailable")
            self.close_client(client, notify=True)

    def accessibility_status_changed(self, _connection, _sender, _path, _interface, _member, body, client):
        if self.clients.get(client.name) is client and accessibility_bus.status_signal(body):
            self.emit(client, accessibility_bus.PATH, accessibility_bus.PROPERTIES, 'PropertiesChanged', body)

    def replied(self, connection, result, state):
        client, original = state
        self.release_message(original)
        client.inflight -= 1
        try:
            response = connection.send_message_with_reply_finish(result)
        except self.GLib.Error:
            self.error(original, "Desktop portal connection closed", release=False)
            return
        if self.clients.get(client.name) is not client:
            # The host connection closed while this call was in flight. The
            # caller may still be alive and must not wait out its own timeout.
            self.error(original, "Desktop portal connection closed", release=False)
            return
        reply = original.new_method_reply()
        if response.get_message_type() == self.Gio.DBusMessageType.ERROR:
            reply.set_message_type(self.Gio.DBusMessageType.ERROR)
            reply.set_error_name(response.get_error_name())
        elif original.get_member() == "Close":
            if original.get_interface() == "org.freedesktop.portal.Session":
                client.sessions.discard(original.get_path())
            elif original.get_interface() == "org.freedesktop.portal.Request":
                client.requests.discard(original.get_path())
        body = response.get_body()
        if body is not None:
            body = translate_variant(body, connection.get_unique_name(), client.name)
            reply.set_body(body)
            if body.get_type_string() == "(o)":
                path = body.unpack()[0]
                if path.startswith(ROOT + "/request/") and path not in client.responses:
                    client.requests.add(path)
        if response.get_unix_fd_list() is not None:
            reply.set_unix_fd_list(response.get_unix_fd_list())
        try:
            self.private.send_message(reply, self.Gio.DBusSendMessageFlags.NONE)
        except self.GLib.Error:
            self.close_client(client)

    def emit(self, client, path, interface, member, body):
        if self.private.is_closed():
            return
        message = self.Gio.DBusMessage.new_signal(path, interface, member)
        message.set_destination(client.name)
        message.set_body(body)
        try:
            self.private.send_message(message, self.Gio.DBusSendMessageFlags.NONE)
        except self.GLib.Error:
            pass  # Private caller/bus can vanish after the liveness check.

    def portal_signal(self, connection, sender, path, interface, member, body, client):
        if (self.clients.get(client.name) is not client or not portal_path(path)
                or not interface.startswith("org.freedesktop.portal.")
                or not own_handle_or_shared(path, connection.get_unique_name())):
            return
        path = translate_path(path, connection.get_unique_name(), client.name)
        body = translate_variant(body, connection.get_unique_name(), client.name)
        if interface == "org.freedesktop.portal.Request" and member == "Response":
            client.requests.discard(path)
            if len(client.responses) >= 512:
                client.responses.clear()
            client.responses.add(path)
            code, values = body.unpack()
            if code == 0 and "session_handle" in values:
                client.sessions.add(values["session_handle"])
        elif interface == "org.freedesktop.portal.Session" and member == "Closed":
            client.sessions.discard(path)
        self.emit(client, path, interface, member, body)

    def owner_changed(self, _connection, _sender, _path, _interface, _member, body, _data=None):
        name, old, new = body.unpack()
        if old and not new and name in self.clients:
            self.close_client(self.clients[name])

    def frontend_changed(self, _connection, _sender, _path, _interface, _member, body, client):
        _name, old, new = body.unpack()
        if old and old != new:
            self.close_client(client, notify=True)

    def close_client(self, client, notify=False):
        if self.clients.get(client.name) is not client:
            return
        del self.clients[client.name]
        self.notifications.disconnect(client, notify=notify)
        client.cancellable.cancel()
        for original in client.waiting:
            self.error(original, "Desktop portal connection unavailable")
        client.waiting.clear()
        if notify:
            for path in client.sessions:
                self.emit(client, path, "org.freedesktop.portal.Session", "Closed", self.GLib.Variant("(a{sv})", ({},)))
            for path in client.requests:
                self.emit(client, path, "org.freedesktop.portal.Request", "Response", self.GLib.Variant("(ua{sv})", (2, {})))
        if client.connection is not None and not client.connection.is_closed():
            client.connection.close(None, None, None)

    def close(self):
        if self.stopped:
            return
        self.stopped = True
        for client in list(self.clients.values()):
            self.close_client(client)
        self.loop.quit()


def relay(ready_fd: int, parent: int):
    # Do this before importing GI or connecting either bus; a hard-killed app
    # must never leave a relay holding screen-sharing sessions alive.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot bind portal relay lifetime")
    if os.getppid() != parent:
        return 1
    instance = PortalRelay(os.environ[HOST_BUS])
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        instance.GLib.unix_signal_add(instance.GLib.PRIORITY_DEFAULT, sig, lambda: (instance.close(), False)[1])
    os.write(ready_fd, b"1")
    os.close(ready_fd)
    try:
        instance.loop.run()
    finally:
        instance.close()
    return 0


def launch(command):
    if not command or not os.environ.get(HOST_BUS):
        raise RuntimeError("portal launcher needs an app and its desktop bus")
    read_fd, write_fd = os.pipe()
    helper = None
    try:
        helper = subprocess.Popen(
            ["/usr/bin/python3", str(Path(__file__).resolve()), "--relay", str(write_fd), str(os.getpid())],
            pass_fds=(write_fd,), stdin=subprocess.DEVNULL)
        os.close(write_fd)
        write_fd = -1
        readable, _, _ = select.select([read_fd], [], [], 6)
        if not readable or os.read(read_fd, 1) != b"1" or helper.poll() is not None:
            raise RuntimeError("private desktop portal relay failed to start (requires python3-gi)")
        os.close(read_fd)
        read_fd = -1
        os.execvpe(command[0], command, os.environ)
    finally:
        for fd in (read_fd, write_fd):
            if fd >= 0:
                os.close(fd)
        if helper is not None:
            if helper.poll() is None:
                helper.terminate()
                try:
                    helper.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    helper.kill()
                    helper.wait(timeout=1)


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["--relay"] and len(sys.argv) == 4:
            raise SystemExit(relay(int(sys.argv[2]), int(sys.argv[3])))
        if sys.argv[1:3] == ["--wrap", "--"]:
            launch(sys.argv[3:])
        else:
            raise RuntimeError("invalid portal bridge invocation")
    except (OSError, RuntimeError, ImportError, KeyError) as error:
        print(f"kilix: {error}", file=sys.stderr)
        raise SystemExit(1)
