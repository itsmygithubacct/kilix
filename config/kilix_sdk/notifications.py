"""Native desktop notifications with per-private-client identifiers.

Notification payloads stay typed, including opaque image bytes. Replacement,
closure, and action signals can reference only notifications belonging to the
calling private bus client. Applications and other singleton names remain on
that private bus.
"""
from dataclasses import dataclass, field

NAME = 'org.freedesktop.Notifications'
PATH = '/org/freedesktop/Notifications'
_SIGNATURES = {'Notify': '(susssasa{sv}i)', 'CloseNotification': '(u)',
               'GetCapabilities': '()', 'GetServerInformation': '()'}
_SIGNAL_TYPES = {'NotificationClosed': '(uu)', 'ActionInvoked': '(us)',
                 'ActivationToken': '(us)'}


@dataclass
class NotificationState:
    ids: dict = field(default_factory=dict)  # private ID -> physical ID
    queue: list = field(default_factory=list)
    early: list = field(default_factory=list)
    busy: bool = False
    creating: bool = False
    pending_notify: bool = False
    replacing_host_id: int = 0
    epoch: int = 0


class NativeNotifications:
    MAX_ACTIVE = 64
    MAX_TOTAL = 512
    MAX_EARLY_SIGNALS = 64

    def __init__(self, relay):
        self.relay = relay
        self.next_id = 1

    def connect(self, client):
        r = self.relay
        client.connection.signal_subscribe(
            NAME, NAME, None, PATH, None, r.Gio.DBusSignalFlags.NONE,
            self.signal, client)
        client.connection.signal_subscribe(
            'org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
            '/org/freedesktop/DBus', NAME, r.Gio.DBusSignalFlags.NONE,
            self.owner_changed, client)

    def send(self, client, original, *, queued=False):
        r, state = self.relay, client.notifications
        if state.busy or (state.queue and not queued):
            state.queue.append(original)
            return
        body = original.get_body()
        signature = body.get_type_string() if body is not None else '()'
        interface, method = original.get_interface(), original.get_member()
        allowed = (interface == NAME and method in _SIGNATURES and signature == _SIGNATURES[method])
        allowed = allowed or (interface == 'org.freedesktop.DBus.Introspectable' and method == 'Introspect' and signature == '()')
        allowed = allowed or (interface == 'org.freedesktop.DBus.Peer' and method in ('Ping','GetMachineId') and signature == '()')
        if not allowed:
            r.error(original, 'Invalid notification method or arguments',
                    'org.freedesktop.DBus.Error.InvalidArgs')
            return
        message = original.copy()
        message.set_sender(None)
        message.set_destination(NAME)
        private_id = 0
        if interface == NAME and method in ('Notify', 'CloseNotification'):
            index = 1 if method == 'Notify' else 0
            private_id = body.get_child_value(index).get_uint32()
            if private_id and private_id not in state.ids:
                r.error(original, 'Notification belongs to another client or has closed',
                        'org.freedesktop.DBus.Error.AccessDenied')
                return
            if method == 'CloseNotification' and not private_id:
                r.error(original, 'Invalid notification identifier',
                        'org.freedesktop.DBus.Error.InvalidArgs')
                return
            if method == 'Notify' and not private_id:
                total = sum(len(item.notifications.ids) + int(item.notifications.creating)
                            for item in r.clients.values())
                if len(state.ids) >= self.MAX_ACTIVE or total >= self.MAX_TOTAL or self.next_id > 0xffffffff:
                    r.error(original, 'Too many active notifications',
                            'org.freedesktop.DBus.Error.LimitsExceeded')
                    return
            children = [body.get_child_value(i) for i in range(body.n_children())]
            children[index] = r.GLib.Variant('u', state.ids.get(private_id, 0))
            if method == 'Notify':
                # A private window XID cannot designate a physical desktop
                # window. Preserve all other hints without unpacking image data.
                hints = children[6]
                entries = [hints.get_child_value(i) for i in range(hints.n_children())
                           if hints.get_child_value(i).get_child_value(0).get_string() != 'window-xid']
                children[6] = r.GLib.Variant.new_array(r.GLib.VariantType.new('{sv}'), entries)
            message.set_body(r.GLib.Variant.new_tuple(*children))
        state.busy = True
        state.pending_notify = interface == NAME and method == 'Notify'
        state.replacing_host_id = state.ids.get(private_id, 0) if state.pending_notify else 0
        state.creating = state.pending_notify and not private_id
        client.inflight += 1
        # Learn the physical ID even for fire-and-forget Notify calls.
        message.set_flags(message.get_flags() & ~r.Gio.DBusMessageFlags.NO_REPLY_EXPECTED)
        try:
            client.connection.send_message_with_reply(
                message, r.Gio.DBusSendMessageFlags.NONE, 30000, client.cancellable,
                self.replied, (client, original, private_id, state.epoch))
        except r.GLib.Error:
            client.inflight -= 1
            r.error(original, 'Desktop notification connection unavailable')
            self.finish(client)

    def replied(self, connection, result, context):
        client, original, private_id, epoch = context
        r, state = self.relay, client.notifications
        client.inflight -= 1
        try:
            response = connection.send_message_with_reply_finish(result)
            if r.clients.get(client.name) is not client:
                # Host connection closed mid-call; answer a still-live caller.
                r.error(original, 'Desktop notification connection closed')
                return
            if epoch != state.epoch:
                r.error(original, 'Desktop notification service restarted')
                return
            reply = original.new_method_reply()
            body = response.get_body()
            if response.get_message_type() == r.Gio.DBusMessageType.ERROR:
                reply.set_message_type(r.Gio.DBusMessageType.ERROR)
                reply.set_error_name(response.get_error_name())
            elif original.get_interface() == NAME and original.get_member() == 'Notify':
                if body is None or body.get_type_string() != '(u)' or not body.get_child_value(0).get_uint32():
                    r.error(original, 'Invalid desktop notification identifier')
                    return
                host_id = body.get_child_value(0).get_uint32()
                if not private_id:
                    private_id = self.next_id
                    self.next_id += 1
                # A daemon must not assign one physical ID to unrelated
                # private clients: that would expose another app's controls.
                if any(value == host_id and (item is not client or key != private_id)
                       for item in r.clients.values() for key, value in item.notifications.ids.items()):
                    r.error(original, 'Desktop notification identifier collision')
                    return
                state.ids[private_id] = host_id
                body = r.GLib.Variant('(u)', (private_id,))
            if body is not None:
                reply.set_body(body)
            if not original.get_flags() & r.Gio.DBusMessageFlags.NO_REPLY_EXPECTED:
                r.private.send_message(reply, r.Gio.DBusSendMessageFlags.NONE)
        except r.GLib.Error:
            r.error(original, 'Desktop notification connection closed')
        finally:
            r.release_message(original)
            self.finish(client)

    def finish(self, client):
        r, state = self.relay, client.notifications
        state.busy = state.creating = state.pending_notify = False
        state.replacing_host_id = 0
        early, state.early = state.early, []
        if r.clients.get(client.name) is not client:
            state.queue.clear()
            return
        for member, body in early:
            self.deliver(client, member, body)
        if state.queue:
            # Use an idle to avoid recursion when queued requests are invalid.
            r.GLib.idle_add(self.next_call, client)

    def next_call(self, client):
        if self.relay.clients.get(client.name) is client and not client.notifications.busy:
            state = client.notifications
            if state.queue:
                original = state.queue.pop(0)
                self.send(client, original, queued=True)
                if not state.busy and state.queue:
                    self.relay.GLib.idle_add(self.next_call, client)
        return False

    def signal(self, _connection, _sender, path, interface, member, body, client):
        r, state = self.relay, client.notifications
        if (r.clients.get(client.name) is not client or path != PATH or interface != NAME or
                member not in _SIGNAL_TYPES or body.get_type_string() != _SIGNAL_TYPES[member] or
                body.get_size() > 16384):
            return
        host_id = body.get_child_value(0).get_uint32()
        if state.pending_notify and host_id == state.replacing_host_id:
            # A replacement can close before Notify returns. Deliver after
            # the reply commits its mapping, so it cannot resurrect a closed
            # identifier. Keep a close signal even if actions filled the queue.
            if member == 'NotificationClosed' and len(state.early) >= self.MAX_EARLY_SIGNALS:
                state.early.pop(0)
            if len(state.early) < self.MAX_EARLY_SIGNALS:
                state.early.append((member, body))
        elif host_id in state.ids.values():
            self.deliver(client, member, body)
        elif state.pending_notify and len(state.early) < self.MAX_EARLY_SIGNALS:
            state.early.append((member, body))

    def deliver(self, client, member, body):
        r, state = self.relay, client.notifications
        host_id = body.get_child_value(0).get_uint32()
        private_id = next((key for key,value in state.ids.items() if value == host_id), None)
        if private_id is None:
            return
        children = [body.get_child_value(i) for i in range(body.n_children())]
        children[0] = r.GLib.Variant('u', private_id)
        if member == 'NotificationClosed':
            state.ids.pop(private_id, None)
        r.emit(client, PATH, NAME, member, r.GLib.Variant.new_tuple(*children))

    def owner_changed(self, _connection, _sender, _path, _interface, _member, body, client):
        _name, old, new = body.unpack()
        if old and old != new:
            self.invalidate(client)

    def invalidate(self, client, *, notify=True):
        r, state = self.relay, client.notifications
        state.epoch += 1
        if notify:
            for private_id in tuple(state.ids):
                r.emit(client, PATH, NAME, 'NotificationClosed', r.GLib.Variant('(uu)', (private_id, 4)))
        state.ids.clear()
        state.early.clear()

    def disconnect(self, client, *, notify=False):
        # Standard native notifications can outlive the command that sent
        # them (notably notify-send); client exit must not erase their display.
        self.invalidate(client, notify=notify)
        for original in client.notifications.queue:
            self.relay.error(original, 'Desktop notification connection unavailable')
        client.notifications.queue.clear()
