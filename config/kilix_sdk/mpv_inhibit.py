"""mpv's native IPC script owns an inhibitor while private video is playing.

mpv X11 otherwise suspends only its private X server. No IPC listening socket,
global player configuration, X timer changes, or synthetic activity is needed.
The inherited IPC descriptor closes on player death, including SIGKILL.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal

if __package__:
    from . import screensaver_bus as saver
else:
    import screensaver_bus as saver

PROPERTIES = ('pause', 'paused-for-cache', 'idle-active', 'eof-reached',
              'video-out-params', 'current-vo', 'options/stop-screensaver')


def prepare_player(argv, env):
    """Use the supported subprocess-script interface without replacing config."""
    command = list(argv)
    if (not command or Path(command[0]).name != 'mpv' or
            env.get('KILIX_PRIVATE_XAPP') != '1' or
            not env.get('KILIX_PORTAL_HOST_BUS')):
        return command
    separator = command.index('--') if '--' in command else len(command)
    script = Path(__file__).with_name('mpv_screensaver.run')
    command.insert(separator, '--script=' + str(script))
    return command


def playing(properties):
    # Missing/unknown properties never grant an inhibitor. audio-only,
    # --video=no, --vo=null, paused, cache-paused, idle and EOF all release it.
    return (properties.get('pause') is False and
            properties.get('paused-for-cache') is False and
            properties.get('idle-active') is False and
            properties.get('eof-reached') is False and
            isinstance(properties.get('video-out-params'), dict) and
            properties.get('current-vo') not in (None, '', 'null') and
            properties.get('options/stop-screensaver') is True)


class PlayerInhibitor:
    TIMEOUT_MS = 5000
    MAX_LINE = 256 * 1024

    def __init__(self, read_fd, write_fd):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.loop = GLib.MainLoop()
        self.read_fd, self.write_fd = read_fd, write_fd
        self.properties = {}
        self.buffer = b''
        self.output = b''
        self.output_watch = 0
        self.bus = None
        self.cancellable = None
        self.cookie = 0
        self.early_loss = 0
        self.pending = False
        self.stopped = False
        self.retry = 0
        self.timer = 0
        os.set_blocking(read_fd, False)
        os.set_blocking(write_fd, False)
        GLib.io_add_watch(read_fd, GLib.PRIORITY_DEFAULT,
                          GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.readable)
        for index, name in enumerate(PROPERTIES):
            self.output += (json.dumps({'command': ['observe_property', index, name]}) + '\n').encode()
        self.output_watch = GLib.io_add_watch(write_fd, GLib.PRIORITY_DEFAULT,
                                              GLib.IO_OUT | GLib.IO_ERR | GLib.IO_HUP, self.writable)

    def writable(self, _fd, condition):
        if condition & (self.GLib.IO_ERR | self.GLib.IO_HUP):
            self.close()
            return False
        try:
            self.output = self.output[os.write(self.write_fd, self.output):]
        except BlockingIOError:
            return True
        except OSError:
            self.close()
            return False
        if not self.output:
            self.output_watch = 0
            return False
        return True

    def readable(self, _fd, condition):
        if condition & (self.GLib.IO_HUP | self.GLib.IO_ERR):
            self.close()
            return False
        try:
            data = os.read(self.read_fd, 65536)
        except BlockingIOError:
            return True
        except OSError:
            data = b''
        if not data:
            self.close()
            return False
        self.buffer += data
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if len(line) > self.MAX_LINE:
                self.close()
                return False
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                self.close()
                return False
            if not isinstance(event, dict):
                self.close()
                return False
            if event.get('event') == 'shutdown':
                self.close()
                return False
            if event.get('event') == 'property-change' and event.get('name') in PROPERTIES:
                self.properties[event['name']] = event.get('data')
        if len(self.buffer) > self.MAX_LINE:
            self.close()
            return False
        self.reconcile()
        return not self.stopped

    def reconcile(self):
        if self.stopped:
            return
        if not playing(self.properties):
            self.cancel_retry()
            if self.pending:
                # Especially important for pause/death while Inhibit is in
                # flight: cancelling a call alone cannot undo a host hold.
                self.drop_bus()
            elif self.bus is not None:
                if self.cookie:
                    cookie, self.cookie = self.cookie, 0
                    self.pending = True
                    self.bus.call(saver.NAME, saver.PATHS[0], saver.NAME, 'UnInhibit',
                                  self.GLib.Variant('(u)', (cookie,)), self.GLib.VariantType.new('()'),
                                  self.Gio.DBusCallFlags.NONE, self.TIMEOUT_MS, self.cancellable,
                                  self.released, self.bus)
                else:
                    self.drop_bus()
            return
        if self.pending or self.cookie or self.retry:
            return
        self.pending = True
        self.cancellable = self.Gio.Cancellable()
        self.timer = self.GLib.timeout_add(self.TIMEOUT_MS, self.connection_timeout)
        self.Gio.DBusConnection.new_for_address(
            os.environ['DBUS_SESSION_BUS_ADDRESS'],
            self.Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
            self.Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, self.cancellable,
            self.connected, self.cancellable)

    def connection_timeout(self):
        self.timer = 0
        self.failed()
        return False

    def connected(self, _source, result, cancellable):
        try:
            bus = self.Gio.DBusConnection.new_for_address_finish(result)
        except self.GLib.Error:
            if self.cancellable is cancellable:
                self.failed()
            return
        bus.set_exit_on_close(False)
        if self.stopped or self.cancellable is not cancellable or not playing(self.properties):
            bus.close(None, None, None)
            return
        self.stop_timer()
        self.bus = bus
        bus.connect('closed', lambda *_: self.bus_closed(bus))
        bus.signal_subscribe(saver.NAME, saver.LOSS_INTERFACE, 'InhibitorLost', saver.PATHS[0],
                             None, self.Gio.DBusSignalFlags.NONE, self.lost)
        bus.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
                             '/org/freedesktop/DBus', saver.NAME, self.Gio.DBusSignalFlags.NONE,
                             self.owner_changed)
        bus.call(saver.NAME, saver.PATHS[0], saver.NAME, 'Inhibit',
                 self.GLib.Variant('(ss)', ('mpv', 'Video playback in Kilix')),
                 self.GLib.VariantType.new('(u)'), self.Gio.DBusCallFlags.NONE,
                 self.TIMEOUT_MS, self.cancellable, self.inhibited, bus)

    def inhibited(self, connection, result, bus):
        try:
            cookie = connection.call_finish(result).unpack()[0]
        except self.GLib.Error:
            if self.bus is bus:
                self.failed()
            return
        if self.bus is not bus:
            return
        if not cookie or cookie == self.early_loss:
            self.failed()
            return
        self.pending = False
        self.cookie = cookie
        self.reconcile()

    def released(self, connection, result, bus):
        try:
            connection.call_finish(result)
        except self.GLib.Error:
            pass  # Closing the caller connection also releases uncertain holds.
        if self.bus is bus:
            self.drop_bus()
            self.reconcile()

    def lost(self, connection, _sender, _path, _interface, _member, body):
        if self.bus is connection and body.get_type_string() == '(u)':
            cookie = body.unpack()[0]
            if cookie == self.cookie:
                self.failed()
            elif self.pending and not self.cookie:
                # Signals and asynchronous call callbacks can enter the main
                # context in either order. A loss before Inhibit's callback
                # must not commit a cookie already invalidated by the relay.
                self.early_loss = cookie

    def owner_changed(self, connection, _sender, _path, _interface, _member, body):
        _name, old, new = body.unpack()
        if self.bus is connection and old and old != new:
            self.failed()

    def bus_closed(self, bus):
        if self.bus is bus:
            self.failed()

    def stop_timer(self):
        if self.timer:
            self.GLib.source_remove(self.timer)
            self.timer = 0

    def drop_bus(self):
        self.stop_timer()
        bus, self.bus = self.bus, None
        if self.cancellable is not None:
            self.cancellable.cancel()
        self.cancellable = None
        self.cookie = 0
        self.early_loss = 0
        self.pending = False
        if bus is not None and not bus.is_closed():
            bus.close(None, None, None)

    def failed(self):
        self.drop_bus()
        if not self.stopped and playing(self.properties) and not self.retry:
            self.retry = self.GLib.timeout_add(1000, self.retry_call)

    def retry_call(self):
        self.retry = 0
        self.reconcile()
        return False

    def cancel_retry(self):
        if self.retry:
            self.GLib.source_remove(self.retry)
            self.retry = 0

    def close(self):
        if self.stopped:
            return
        self.stopped = True
        self.cancel_retry()
        self.drop_bus()
        self.loop.quit()


def main(argv):
    if os.environ.get('KILIX_PRIVATE_XAPP') != '1' or not os.environ.get('KILIX_PORTAL_HOST_BUS'):
        return 0
    if len(argv) != 1 or not argv[0].startswith('--mpv-ipc-fd='):
        raise RuntimeError('mpv inhibitor requires its inherited IPC descriptor')
    descriptors = [int(value) for value in argv[0].split('=', 1)[1].split(':')]
    if len(descriptors) not in (1, 2) or any(fd < 3 for fd in descriptors):
        raise RuntimeError('invalid mpv IPC descriptor')
    instance = PlayerInhibitor(descriptors[0], descriptors[-1])
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        instance.GLib.unix_signal_add(instance.GLib.PRIORITY_DEFAULT, sig,
                                      lambda: (instance.close(), False)[1])
    try:
        instance.loop.run()
    finally:
        instance.close()
        for fd in set(descriptors):
            os.close(fd)
    return 0
