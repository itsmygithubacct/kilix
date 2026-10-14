"""Caller-owned ScreenSaver inhibitors on the physical session bus.

These connections are deliberately independent of portal/capture connections:
an uncertain inhibitor call must be cleaned up by closing its owning connection.
Calls target a resolved unique service owner, never a replacement's cookie space.
"""
from dataclasses import dataclass, field
import os
import secrets

NAME = 'org.freedesktop.ScreenSaver'
LOSS_INTERFACE = 'org.kilix.ScreenSaver'
CONTEXT_INTERFACE = 'org.pleb.ScreenSaver1'
PATHS = ('/org/freedesktop/ScreenSaver', '/ScreenSaver')
SIGNATURES = {'Inhibit': '(ss)', 'UnInhibit': '(u)', 'Lock': '()'}
CONTEXT_SIGNATURES = {'GetContext': '()', 'LockForContext': '(sss)'}
XML = '''<node><interface name="org.freedesktop.ScreenSaver">
<method name="Inhibit"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="out" type="u"/></method>
<method name="UnInhibit"><arg direction="in" type="u"/></method>
<method name="Lock"/>
</interface><interface name="org.pleb.ScreenSaver1">
<method name="GetContext"><arg direction="out" type="s"/><arg direction="out" type="s"/><arg direction="out" type="s"/><arg direction="out" type="s"/></method>
<method name="LockForContext"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="in" type="s"/></method>
</interface></node>'''


@dataclass
class Caller:
    name: str
    cancellable: object
    connection: object = None
    owner: str = ''
    cookies: dict = field(default_factory=dict)
    queue: list = field(default_factory=list)
    pending: object = None
    pending_cookie: int = 0
    timer: int = 0


class ScreenSaverRelay:
    MAX_CLIENTS = 64
    MAX_CALLS = 64
    MAX_COOKIES = 64
    MAX_TOTAL_COOKIES = 512
    TIMEOUT_MS = 5000

    def __init__(self, relay):
        self.relay = relay
        self.clients = {}
        # Never reuse a cookie in this relay's lifetime. A fresh unpredictable
        # namespace also makes a stale cookie from a previous relay useless.
        self.next_cookie = secrets.randbelow(0x7fffffff) + 1

    def current(self, client):
        return self.clients.get(client.name) is client

    def forward(self, original):
        r = self.relay
        body = original.get_body()
        signature = body.get_type_string() if body is not None else '()'
        interface, method = original.get_interface(), original.get_member()
        if (interface == 'org.freedesktop.DBus.Introspectable' and
                method == 'Introspect' and signature == '()'):
            self.reply(original, r.GLib.Variant('(s)', (XML,)))
            return
        methods = SIGNATURES if interface == NAME else CONTEXT_SIGNATURES if interface == CONTEXT_INTERFACE else {}
        if (method not in methods or
                signature != methods[method] or
                (body is not None and body.get_size() > 8192)):
            r.error(original, 'Invalid ScreenSaver method or arguments',
                    'org.freedesktop.DBus.Error.InvalidArgs')
            return
        if method == 'Inhibit' and original.get_flags() & r.Gio.DBusMessageFlags.NO_REPLY_EXPECTED:
            # There is no cookie recipient; acquiring a hold would make it
            # impossible for this live app to release it normally.
            r.error(original, 'Inhibit requires a reply for its cookie',
                    'org.freedesktop.DBus.Error.InvalidArgs')
            return
        name = original.get_sender()
        client = self.clients.get(name)
        if method == 'UnInhibit' and (client is None or
                body.unpack()[0] not in client.cookies):
            r.error(original, 'Unknown ScreenSaver cookie for this caller',
                    'org.freedesktop.DBus.Error.InvalidArgs')
            return
        if client is None:
            if len(self.clients) >= self.MAX_CLIENTS:
                r.error(original, 'Too many ScreenSaver callers',
                        'org.freedesktop.DBus.Error.LimitsExceeded')
                return
            client = Caller(name, r.Gio.Cancellable())
            self.clients[name] = client
            client.timer = r.GLib.timeout_add(self.TIMEOUT_MS, self.setup_timeout, client)
            r.private.call(
                'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                'NameHasOwner', r.GLib.Variant('(s)', (name,)), r.GLib.VariantType.new('(b)'),
                r.Gio.DBusCallFlags.NONE, self.TIMEOUT_MS, client.cancellable,
                self.checked_caller, client)
        if len(client.queue) + int(client.pending is not None) >= self.MAX_CALLS:
            r.error(original, 'Too many pending ScreenSaver calls',
                    'org.freedesktop.DBus.Error.LimitsExceeded')
            return
        client.queue.append(original)
        self.next_call(client)

    def stop_timer(self, client):
        if client.timer:
            source = self.relay.GLib.MainContext.default().find_source_by_id(client.timer)
            if source is not None:
                source.destroy()
            client.timer = 0

    def setup_timeout(self, client):
        client.timer = 0
        self.disconnect(client, 'ScreenSaver connection timed out')
        return False

    def checked_caller(self, connection, result, client):
        r = self.relay
        try:
            alive = connection.call_finish(result).unpack()[0]
        except r.GLib.Error:
            alive = False
        if not self.current(client):
            return
        if not alive:
            self.disconnect(client, 'ScreenSaver caller disconnected')
            return
        r.Gio.DBusConnection.new_for_address(
            r.address, r.Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
            r.Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, client.cancellable,
            self.connected, client)

    def connected(self, _source, result, client):
        r = self.relay
        try:
            connection = r.Gio.DBusConnection.new_for_address_finish(result)
        except r.GLib.Error:
            self.disconnect(client, 'ScreenSaver connection unavailable')
            return
        connection.set_exit_on_close(False)
        if not self.current(client):
            connection.close(None, None, None)
            return
        client.connection = connection
        connection.connect('closed', lambda *_: self.disconnect(client, 'ScreenSaver bus closed'))
        connection.signal_subscribe(
            'org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
            '/org/freedesktop/DBus', NAME, r.Gio.DBusSignalFlags.NONE,
            self.owner_changed, client)
        self.resolve_owner(client)

    def resolve_owner(self, client, *, activated=False):
        r = self.relay
        client.connection.call(
            'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
            'GetNameOwner', r.GLib.Variant('(s)', (NAME,)), r.GLib.VariantType.new('(s)'),
            r.Gio.DBusCallFlags.NONE, self.TIMEOUT_MS, client.cancellable,
            self.resolved_owner, (client, activated))

    def resolved_owner(self, connection, result, state):
        client, activated = state
        r = self.relay
        try:
            owner = connection.call_finish(result).unpack()[0]
        except r.GLib.Error as error:
            if (self.current(client) and not activated and
                    r.Gio.DBusError.get_remote_error(error) == 'org.freedesktop.DBus.Error.NameHasNoOwner'):
                connection.call(
                    'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                    'StartServiceByName', r.GLib.Variant('(su)', (NAME, 0)),
                    r.GLib.VariantType.new('(u)'), r.Gio.DBusCallFlags.NONE,
                    self.TIMEOUT_MS, client.cancellable, self.activated, client)
            else:
                self.disconnect(client, 'Physical ScreenSaver service unavailable')
            return
        if not self.current(client):
            return
        client.owner = owner
        self.stop_timer(client)
        self.next_call(client)

    def activated(self, connection, result, client):
        try:
            connection.call_finish(result)
        except self.relay.GLib.Error:
            self.disconnect(client, 'Physical ScreenSaver service unavailable')
            return
        if self.current(client):
            self.resolve_owner(client, activated=True)

    def owner_changed(self, _connection, _sender, _path, _interface, _member, body, client):
        _name, old, new = body.unpack()
        if old and old != new:
            self.disconnect(client, 'Physical ScreenSaver service restarted')

    def next_call(self, client):
        if not self.current(client) or not client.owner or client.pending is not None:
            return False
        if not client.queue:
            if not client.cookies:
                self.disconnect(client)
            return False
        r = self.relay
        original = client.queue.pop(0)
        method = original.get_member()
        body = original.get_body()
        if method == 'Inhibit':
            count = sum(len(item.cookies) + int(item.pending is not None and
                        item.pending.get_member() == 'Inhibit') for item in self.clients.values())
            if (len(client.cookies) >= self.MAX_COOKIES or count >= self.MAX_TOTAL_COOKIES or
                    self.next_cookie > 0xffffffff):
                r.error(original, 'Too many ScreenSaver inhibitors',
                        'org.freedesktop.DBus.Error.LimitsExceeded')
                r.GLib.idle_add(self.next_call, client)
                return False
            client.pending_cookie = self.next_cookie
            self.next_cookie += 1
        elif method == 'UnInhibit':
            cookie = body.unpack()[0]
            if cookie not in client.cookies:
                r.error(original, 'Unknown ScreenSaver cookie for this caller',
                        'org.freedesktop.DBus.Error.InvalidArgs')
                r.GLib.idle_add(self.next_call, client)
                return False
            body = r.GLib.Variant('(u)', (client.cookies[cookie],))
        message = original.copy()
        message.set_sender(None)
        message.set_destination(client.owner)
        message.set_flags(message.get_flags() & ~r.Gio.DBusMessageFlags.NO_REPLY_EXPECTED)
        if body is not None:
            message.set_body(body)
        client.pending = original
        try:
            client.connection.send_message_with_reply(
                message, r.Gio.DBusSendMessageFlags.NONE, self.TIMEOUT_MS, client.cancellable,
                self.replied, (client, original))
        except r.GLib.Error:
            self.disconnect(client, 'Physical ScreenSaver connection unavailable')
        return False

    def replied(self, connection, result, state):
        client, original = state
        r = self.relay
        try:
            response = connection.send_message_with_reply_finish(result)
        except r.GLib.Error:
            self.disconnect(client, 'Physical ScreenSaver call timed out or disconnected')
            return
        if not self.current(client) or client.pending is not original:
            return  # Disconnect already answered and released the message.
        body = response.get_body()
        if response.get_message_type() == r.Gio.DBusMessageType.ERROR:
            client.pending = None
            r.error(original, 'Physical ScreenSaver call failed',
                    response.get_error_name() or 'org.freedesktop.DBus.Error.Failed')
            self.disconnect(client, 'Physical ScreenSaver call failed')
            return
        method = original.get_member()
        expected = '(u)' if method == 'Inhibit' else '(ssss)' if method == 'GetContext' else '()'
        if (body.get_type_string() if body is not None else '()') != expected:
            self.disconnect(client, 'Invalid physical ScreenSaver reply')
            return
        if method == 'Inhibit':
            host_cookie = body.unpack()[0]
            if not host_cookie or host_cookie in client.cookies.values():
                self.disconnect(client, 'Invalid physical ScreenSaver cookie')
                return
            cookie = client.pending_cookie
            client.cookies[cookie] = host_cookie
            body = r.GLib.Variant('(u)', (cookie,))
        elif method == 'UnInhibit':
            del client.cookies[original.get_body().unpack()[0]]
        elif method == 'GetContext':
            generation, session_id, physical_display, host_route = body.unpack()
            # The relay's display belongs to the primary app that owns this
            # private bus. Never take a private route display from app markers.
            route = os.environ.get('DISPLAY', '')
            if not generation or not session_id or not physical_display or host_route != physical_display or not route:
                self.disconnect(client, 'Invalid physical Pleb context')
                return
            body = r.GLib.Variant('(ssss)', (generation, session_id, physical_display, route))
        client.pending = None
        client.pending_cookie = 0
        if not self.reply(original, body):
            self.disconnect(client, 'ScreenSaver caller disconnected')
            return
        r.GLib.idle_add(self.next_call, client)

    def reply(self, original, body):
        r = self.relay
        r.release_message(original)
        if original.get_flags() & r.Gio.DBusMessageFlags.NO_REPLY_EXPECTED:
            return True
        reply = original.new_method_reply()
        if body is not None:
            reply.set_body(body)
        try:
            r.private.send_message(reply, r.Gio.DBusSendMessageFlags.NONE)
        except r.GLib.Error:
            return False
        return True

    def disconnect(self, client, reason='ScreenSaver connection closed'):
        if not self.current(client):
            return
        del self.clients[client.name]
        self.stop_timer(client)
        client.cancellable.cancel()
        pending, client.pending = client.pending, None
        if pending is not None:
            self.relay.error(pending, reason)
        for original in client.queue:
            self.relay.error(original, reason)
        client.queue.clear()
        for cookie in client.cookies:
            self.relay.emit(client, PATHS[0], LOSS_INTERFACE, 'InhibitorLost',
                            self.relay.GLib.Variant('(u)', (cookie,)))
        client.cookies.clear()
        # The physical service owns inhibits by this connection's unique name.
        # Closing it releases even a successful Inhibit whose reply was lost.
        if client.connection is not None and not client.connection.is_closed():
            client.connection.close(None, None, None)

    def caller_gone(self, name):
        if name in self.clients:
            self.disconnect(self.clients[name], 'ScreenSaver caller disconnected')

    def close(self):
        for client in list(self.clients.values()):
            self.disconnect(client)
