"""Independent D-Bus clients and an owned hostile ScreenSaver test daemon."""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _env_support import sandbox_env

from gi.repository import Gio, GLib

NAME = 'org.freedesktop.ScreenSaver'
PATHS = ('/org/freedesktop/ScreenSaver', '/ScreenSaver')
CONTROL = 'org.example.ScreenSaverFixture'
DIAG = 'org.example.KilixRelayFixture'
XML = '''<node><interface name="org.example.ScreenSaverFixture">
<method name="Control"><arg direction="in" type="s"/><arg direction="out" type="s"/></method>
</interface></node>'''
DXML = '''<node><interface name="org.example.KilixRelayFixture">
<method name="Stats"><arg direction="out" type="s"/></method>
<method name="ExhaustCookies"/><method name="Shutdown"/>
</interface></node>'''


def connection(address=None):
    bus = Gio.DBusConnection.new_for_address_sync(
        address or os.environ['DBUS_SESSION_BUS_ADDRESS'],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    bus.set_exit_on_close(False)
    return bus


def request_name(bus, name):
    reply = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                          'RequestName', GLib.Variant('(su)', (name, 4)), None,
                          Gio.DBusCallFlags.NONE, 2000, None)
    assert reply.unpack()[0] == 1, reply


def call(bus, method, args=None, path=PATHS[0], interface=NAME):
    return bus.call_sync(NAME, path, interface, method, args, None,
                         Gio.DBusCallFlags.NONE, 3000, None)


def control(bus, action='stats'):
    return json.loads(bus.call_sync(CONTROL, '/Control', CONTROL, 'Control',
                      GLib.Variant('(s)', (action,)), None, Gio.DBusCallFlags.NONE,
                      2000, None).unpack()[0])


def diag(bus, method='Stats'):
    value = bus.call_sync(DIAG, '/Diag', DIAG, method, None, None,
                          Gio.DBusCallFlags.NONE, 2000, None).unpack()
    return json.loads(value[0]) if value else None


def wait(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('condition did not become true')


def denied(function, error='InvalidArgs'):
    try:
        function()
    except GLib.Error as exc:
        assert error in str(exc), str(exc)
        return
    raise AssertionError('call unexpectedly succeeded')


def inhibit(bus, reason='normal', path=PATHS[0]):
    return call(bus, 'Inhibit', GLib.Variant('(ss)', ('Independent fixture', reason)), path).unpack()[0]


def uninhibit(bus, cookie, path=PATHS[0]):
    call(bus, 'UnInhibit', GLib.Variant('(u)', (cookie,)), path)


class Host:
    def __init__(self):
        self.control = connection()
        self.holds = {}
        self.next_cookie = {}
        self.calls = []
        self.locks = 0
        self.owner = None
        self.control.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
                                      '/org/freedesktop/DBus', None, Gio.DBusSignalFlags.NONE, self.gone)
        self.control.register_object('/Control', Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],
                                     self.command, None, None)
        request_name(self.control, CONTROL)
        self.start_service()

    def start_service(self):
        self.owner = connection()
        self.owner.add_filter(self.filter, None)
        request_name(self.owner, NAME)

    def gone(self, _bus, _sender, _path, _interface, _member, body, _data=None):
        name, old, new = body.unpack()
        if old and not new:
            self.holds.pop(name, None)
            self.next_cookie.pop(name, None)

    def stats(self):
        return {'count': sum(len(value) for value in self.holds.values()),
                'senders': sorted(name for name, value in self.holds.items() if value),
                'calls': self.calls, 'locks': self.locks,
                'owner': self.owner.get_unique_name() if self.owner else ''}

    def command(self, _bus, _sender, _path, _interface, _member, body, invocation):
        action = body.unpack()[0]
        if action == 'restart':
            self.owner.close_sync(None)
            self.start_service()
        elif action == 'release-owner':
            self.owner.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                                'ReleaseName', GLib.Variant('(s)', (NAME,)), None,
                                Gio.DBusCallFlags.NONE, 2000, None)
        elif action == 'reclaim-owner':
            request_name(self.owner, NAME)
        invocation.return_value(GLib.Variant('(s)', (json.dumps(self.stats()),)))

    def filter(self, bus, message, incoming, _data):
        if (incoming and message.get_message_type() == Gio.DBusMessageType.METHOD_CALL and
                message.get_path() in PATHS):
            GLib.idle_add(self.method, bus, message.copy())
            return None
        return message

    def reply(self, bus, message, body=None, error=None):
        response = (message.new_method_error_literal(error, 'Fixture error') if error
                    else message.new_method_reply())
        if body is not None:
            response.set_body(body)
        if not bus.is_closed():
            bus.send_message(response, Gio.DBusSendMessageFlags.NONE)
        return False

    def method(self, bus, message):
        sender, method = message.get_sender(), message.get_member()
        args = message.get_body().unpack() if message.get_body() else ()
        self.calls.append({'sender': sender, 'method': method, 'args': args,
                           'path': message.get_path(), 'destination': message.get_destination(),
                           'flags': int(message.get_flags())})
        if method == 'Inhibit':
            reason = args[1]
            cookie = self.next_cookie.get(sender, 700)
            self.next_cookie[sender] = cookie + 1
            if reason == 'collision':
                cookie = min(self.holds[sender])
            self.holds.setdefault(sender, {})[cookie] = reason
            if reason == 'hang':
                return False
            if reason == 'late':
                GLib.timeout_add(700, self.reply, bus, message, GLib.Variant('(u)', (cookie,)))
                return False
            if reason == 'noreply':
                return self.reply(bus, message, error='org.freedesktop.DBus.Error.NoReply')
            if reason == 'malformed':
                return self.reply(bus, message, GLib.Variant('(s)', ('bad type',)))
            return self.reply(bus, message, GLib.Variant('(u)', (0 if reason == 'zero' else cookie,)))
        if method == 'UnInhibit':
            holds = self.holds.get(sender, {})
            if args[0] not in holds:
                return self.reply(bus, message, error='org.freedesktop.DBus.Error.InvalidArgs')
            reason = holds[args[0]]
            if reason == 'release-hang':
                return False
            del holds[args[0]]
            return self.reply(bus, message)
        if method == 'Lock':
            self.locks += 1
            return self.reply(bus, message)
        return self.reply(bus, message, error='org.freedesktop.DBus.Error.UnknownMethod')


def relay(bridge_path, fd, parent):
    sys.path.insert(0, str(Path(bridge_path).parent))
    import portal_bridge
    import screensaver_bus
    screensaver_bus.ScreenSaverRelay.TIMEOUT_MS = 400
    screensaver_bus.ScreenSaverRelay.MAX_CLIENTS = 3
    screensaver_bus.ScreenSaverRelay.MAX_COOKIES = 3
    screensaver_bus.ScreenSaverRelay.MAX_TOTAL_COOKIES = 5
    screensaver_bus.ScreenSaverRelay.MAX_CALLS = 4
    instance = portal_bridge.PortalRelay(os.environ[portal_bridge.HOST_BUS])
    def command(_bus, _sender, _path, _interface, member, _body, invocation):
        if member == 'Stats':
            value = {'clients': len(instance.screensavers.clients),
                     'cookies': sum(len(c.cookies) for c in instance.screensavers.clients.values()),
                     'pending': sum(int(c.pending is not None) + len(c.queue)
                                    for c in instance.screensavers.clients.values()),
                     'retained': instance.retained_bytes, 'queued': instance.queued,
                     'portal_clients': len(instance.clients)}
            invocation.return_value(GLib.Variant('(s)', (json.dumps(value),)))
        elif member == 'ExhaustCookies':
            instance.screensavers.next_cookie = 0xffffffff
            invocation.return_value(None)
        else:
            invocation.return_value(None)
            instance.private.flush_sync(None)
            GLib.idle_add(instance.close)
    instance.private.register_object('/Diag', Gio.DBusNodeInfo.new_for_xml(DXML).interfaces[0],
                                     command, None, None)
    request_name(instance.private, DIAG)
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (instance.close(), False)[1])
    os.write(fd, b'1')
    os.close(fd)
    instance.loop.run()
    instance.close()


def start_relay(bridge_path):
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen(['/usr/bin/python3', __file__, 'relay', bridge_path,
                                str(write_fd), str(os.getpid())], pass_fds=(write_fd,))
    os.close(write_fd)
    try:
        assert select.select([read_fd], [], [], 5)[0] and os.read(read_fd, 1) == b'1'
    finally:
        os.close(read_fd)
    return process


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def client(bridge_path):
    host = connection(os.environ['KILIX_PORTAL_HOST_BUS'])
    a, b = connection(), connection()
    process = start_relay(bridge_path)
    proof = {}
    try:
        first = inhibit(a)
        second = inhibit(a, path=PATHS[1])
        third = inhibit(b)
        assert len({first, second, third}) == 3
        assert all(cookie not in (700, 701) for cookie in (first, second, third))
        stats = control(host)
        assert stats['count'] == 3 and len(stats['senders']) == 2, stats
        assert {item['path'] for item in stats['calls']} == set(PATHS)
        assert all(item['destination'] == stats['owner'] for item in stats['calls'])
        assert diag(a)['portal_clients'] == 0
        proof['both_paths_caller_connections_private_cookies_and_pinned_owner'] = True
        denied(lambda: uninhibit(b, first))
        denied(lambda: uninhibit(a, 0))
        denied(lambda: call(a, 'Inhibit', GLib.Variant('(s)', ('wrong',))))
        denied(lambda: call(a, 'Activate'))
        assert control(host)['count'] == 3
        for path in PATHS:
            call(a, 'Lock', path=path)
        assert control(host)['count'] == 3 and control(host)['locks'] == 2
        xml = call(a, 'Introspect', interface='org.freedesktop.DBus.Introspectable').unpack()[0]
        assert 'UnInhibit' in xml and 'Lock' in xml
        proof['cross_caller_invalid_signatures_and_explicit_lock_while_inhibited'] = True
        message = Gio.DBusMessage.new_method_call(NAME, PATHS[0], NAME, 'Inhibit')
        message.set_body(GLib.Variant('(ss)', ('No recipient', 'must not acquire')))
        message.set_flags(Gio.DBusMessageFlags.NO_REPLY_EXPECTED)
        a.send_message(message, Gio.DBusSendMessageFlags.NONE)
        call(a, 'Lock')
        assert control(host)['count'] == 3
        message = Gio.DBusMessage.new_method_call(NAME, PATHS[1], NAME, 'UnInhibit')
        message.set_body(GLib.Variant('(u)', (third,)))
        message.set_flags(Gio.DBusMessageFlags.NO_REPLY_EXPECTED)
        b.send_message(message, Gio.DBusSendMessageFlags.NONE)
        b.flush_sync(None)
        wait(lambda: control(host)['count'] == 2)
        denied(lambda: uninhibit(b, third))
        assert not any(item['flags'] & int(Gio.DBusMessageFlags.NO_REPLY_EXPECTED)
                       for item in control(host)['calls'])
        proof['no_reply_inhibit_does_not_leak_and_uninhibit_is_acknowledged'] = True
        fourth = inhibit(b)
        losses_a, losses_b = [], []
        for bus, losses in ((a, losses_a), (b, losses_b)):
            bus.signal_subscribe(NAME, 'org.kilix.ScreenSaver', 'InhibitorLost', PATHS[0], None,
                                 Gio.DBusSignalFlags.NONE,
                                 lambda _b, _s, _p, _i, _m, body, values: values.append(body.unpack()[0]), losses)
        control(host, 'restart')
        wait(lambda: control(host)['count'] == 0 and len(losses_a) == 2 and len(losses_b) == 1)
        assert set(losses_a) == {first, second} and losses_b == [fourth]
        denied(lambda: uninhibit(a, first))
        fresh = inhibit(a)
        assert fresh not in (first, second, third, fourth)
        uninhibit(a, fresh)
        denied(lambda: uninhibit(a, fresh))
        proof['owner_restart_invalidates_cookies_and_unicasts_losses'] = True
        held = inhibit(a)
        control(host, 'release-owner')
        wait(lambda: control(host)['count'] == 0)
        control(host, 'reclaim-owner')
        denied(lambda: uninhibit(a, held))
        proof['owner_loss_even_if_original_daemon_stays_connected_releases'] = True
        inhibit(b)
        b.close_sync(None)
        wait(lambda: control(host)['count'] == 0)
        proof['caller_disconnect_releases_physical_connection'] = True
        for reason in ('hang', 'late', 'noreply', 'zero', 'malformed'):
            started = time.monotonic()
            denied(lambda: inhibit(a, reason), 'NoReply' if reason == 'noreply' else 'Failed')
            assert time.monotonic() - started < 2
            wait(lambda: control(host)['count'] == 0)
        time.sleep(.75)
        assert control(host)['count'] == 0
        inhibit(a)
        denied(lambda: inhibit(a, 'collision'), 'Failed')
        wait(lambda: control(host)['count'] == 0)
        held = inhibit(a, 'release-hang')
        denied(lambda: uninhibit(a, held), 'Failed')
        wait(lambda: control(host)['count'] == 0)
        proof['timeouts_late_reply_no_reply_bad_reply_and_cookie_collision_fail_closed'] = True
        c = connection()
        c.call(NAME, PATHS[0], NAME, 'Inhibit', GLib.Variant('(ss)', ('Dying', 'hang')),
               None, Gio.DBusCallFlags.NONE, 3000, None, lambda *_: None, None)
        wait(lambda: control(host)['count'] == 1)
        c.close_sync(None)
        wait(lambda: control(host)['count'] == 0)
        proof['caller_death_during_pending_inhibit_releases'] = True
        for reason in ('normal', 'hang'):
            child = subprocess.Popen(['/usr/bin/python3', __file__, 'hold-client', reason],
                                     stdout=subprocess.PIPE, text=True)
            try:
                assert select.select([child.stdout], [], [], 3)[0]
                assert child.stdout.readline().strip() == 'ready'
                wait(lambda: control(host)['count'] == 1)
                child.kill()
                child.wait(timeout=3)
                wait(lambda: control(host)['count'] == 0)
            finally:
                stop(child)
        proof['independent_caller_sigkill_before_and_after_inhibit_reply_releases'] = True
        results = []
        def finished(bus, result, label):
            try:
                bus.call_finish(result)
                results.append((label, 'ok'))
            except GLib.Error as exc:
                results.append((label, str(exc)))
        before_locks = control(host)['locks']
        for index in range(6):
            a.call(NAME, PATHS[0], NAME, 'Inhibit' if index == 0 else 'Lock',
                   GLib.Variant('(ss)', ('Queued', 'hang')) if index == 0 else None,
                   None, Gio.DBusCallFlags.NONE, 3000, None, finished, index)
        wait(lambda: len(results) == 6)
        assert any('LimitsExceeded' in value for _index, value in results), results
        assert all(value != 'ok' for _index, value in results), results
        assert control(host)['locks'] == before_locks
        wait(lambda: diag(a)['retained'] == 0 and diag(a)['clients'] == 0)
        proof['queued_call_limit_timeout_cleanup_and_no_retained_messages'] = True
        b, c, d = connection(), connection(), connection()
        cookies = [inhibit(a), inhibit(a), inhibit(a)]
        # Exercise the caller bound while the aggregate bound still has room.
        denied(lambda: inhibit(a), 'LimitsExceeded')
        uninhibit(a, cookies.pop())
        uninhibit(a, cookies.pop())
        cookies += [inhibit(b), inhibit(c)]
        denied(lambda: inhibit(d), 'LimitsExceeded')
        cookies += [inhibit(a), inhibit(a)]
        denied(lambda: inhibit(a), 'LimitsExceeded')
        denied(lambda: inhibit(b), 'LimitsExceeded')
        denied(lambda: inhibit(d), 'LimitsExceeded')
        call(a, 'Lock')
        assert control(host)['count'] == 5
        c.close_sync(None)
        wait(lambda: control(host)['count'] == 4)
        inhibit(d)
        for bus in (a, b, d):
            bus.close_sync(None)
        a = connection()
        wait(lambda: control(host)['count'] == 0 and diag(a)['clients'] == 0)
        proof['client_cookie_total_limits_recover_and_allow_lock'] = True
        diag(a, 'ExhaustCookies')
        final_cookie = inhibit(a)
        assert final_cookie == 0xffffffff
        denied(lambda: inhibit(a), 'LimitsExceeded')
        uninhibit(a, final_cookie)
        wait(lambda: diag(a)['retained'] == 0)
        proof['cookie_exhaustion_never_wraps_or_reuses_authority'] = True
        diag(a, 'Shutdown')
        process.wait(timeout=3)
        process = start_relay(bridge_path)
        held = inhibit(a)
        diag(a, 'Shutdown')
        process.wait(timeout=3)
        wait(lambda: control(host)['count'] == 0)
        process = start_relay(bridge_path)
        denied(lambda: uninhibit(a, held))
        fresh = inhibit(a)
        assert fresh != held
        process.kill()
        process.wait(timeout=3)
        wait(lambda: control(host)['count'] == 0)
        proof['graceful_shutdown_hard_relay_death_and_restart_do_not_inherit_cookies'] = True
        print(json.dumps(proof), flush=True)
    finally:
        stop(process)
        if not a.is_closed():
            a.close_sync(None)


def host(bridge_path):
    fixture = Host()
    loop = GLib.MainLoop()
    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    process = subprocess.Popen(['dbus-run-session', '--', '/usr/bin/python3', __file__, 'client', bridge_path],
                                env=sandbox_env(KILIX_PORTAL_HOST_BUS=os.environ['DBUS_SESSION_BUS_ADDRESS']),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        output, errors = process.communicate(timeout=40)
        assert process.returncode == 0, output + errors
        assert 'Traceback' not in errors and 'CRITICAL' not in errors, errors
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and fixture.stats()['count']:
            time.sleep(.01)
        assert fixture.stats()['count'] == 0, fixture.stats()
        proof = json.loads(output.strip().splitlines()[-1])
        primary = subprocess.Popen(['dbus-run-session', '--', '/usr/bin/python3', bridge_path,
                                    '--wrap', '--', '/usr/bin/python3', __file__, 'primary-app'],
                                   env=sandbox_env(KILIX_PORTAL_HOST_BUS=os.environ['DBUS_SESSION_BUS_ADDRESS']),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert select.select([primary.stdout], [], [], 6)[0]
            identity = json.loads(primary.stdout.readline())
            assert fixture.stats()['count'] == 1, fixture.stats()
            pid = identity['pid']
            assert process_start(pid) == identity['start']
            os.kill(pid, signal.SIGKILL)
            primary.communicate(timeout=5)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and fixture.stats()['count']:
                time.sleep(.01)
            assert fixture.stats()['count'] == 0, fixture.stats()
            proof['production_launcher_primary_sigkill_kills_relay_and_releases'] = True
        finally:
            stop(primary)
        # AF_UNIX paths are limited to 108 bytes, independent of TMPDIR.
        with tempfile.TemporaryDirectory(prefix='kilix-saver-bus-', dir='/tmp') as directory:
            address = 'unix:path=' + str(Path(directory) / 'bus')
            daemon = start_bus(address)
            old_address = os.environ['DBUS_SESSION_BUS_ADDRESS']
            os.environ['DBUS_SESSION_BUS_ADDRESS'] = address
            restart_client = None
            try:
                dead_fixture = Host()
                restart_client = subprocess.Popen(['dbus-run-session', '--', '/usr/bin/python3', __file__,
                                                   'deadbus-client', bridge_path],
                                                  env=sandbox_env(KILIX_PORTAL_HOST_BUS=address),
                                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                  stderr=subprocess.PIPE, text=True)
                assert select.select([restart_client.stdout], [], [], 5)[0]
                assert restart_client.stdout.readline().strip() == 'held'
                assert dead_fixture.stats()['count'] == 1
                stop(daemon)
                time.sleep(.15)
                daemon = start_bus(address)
                new_fixture = Host()
                restart_client.stdin.write('restarted\n')
                restart_client.stdin.flush()
                restart_output, restart_errors = restart_client.communicate(timeout=6)
                assert restart_client.returncode == 0, restart_output + restart_errors
                assert new_fixture.stats()['count'] == 0, new_fixture.stats()
                proof['physical_bus_death_and_same_address_restart_erase_old_authority'] = True
            finally:
                if restart_client is not None:
                    stop(restart_client)
                stop(daemon)
                os.environ['DBUS_SESSION_BUS_ADDRESS'] = old_address
        print(json.dumps(proof), flush=True)
    finally:
        stop(process)
        loop.quit()
        thread.join(timeout=2)


def process_start(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def start_bus(address):
    daemon = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address=1',
                               '--address=' + address], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([daemon.stdout], [], [], 3)[0]
        reported = daemon.stdout.readline().strip()
        assert reported.startswith(address), (reported, daemon.stderr.read())
    except BaseException:
        stop(daemon)
        raise
    return daemon


def deadbus_client(bridge_path):
    bus = connection()
    relay_process = start_relay(bridge_path)
    try:
        stale = inhibit(bus)
        print('held', flush=True)
        assert sys.stdin.readline().strip() == 'restarted'
        wait(lambda: diag(bus)['clients'] == 0 and diag(bus)['retained'] == 0)
        denied(lambda: uninhibit(bus, stale))
        fresh = inhibit(bus)
        assert stale != fresh
        uninhibit(bus, fresh)
        diag(bus, 'Shutdown')
        relay_process.wait(timeout=3)
    finally:
        stop(relay_process)
        bus.close_sync(None)


if __name__ == '__main__':
    if sys.argv[1] == 'relay':
        relay(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    elif sys.argv[1] == 'client':
        client(sys.argv[2])
    elif sys.argv[1] == 'deadbus-client':
        deadbus_client(sys.argv[2])
    elif sys.argv[1] == 'primary-app':
        bus = connection()
        inhibit(bus)
        print(json.dumps({'pid': os.getpid(), 'start': process_start(os.getpid())}), flush=True)
        time.sleep(20)
    elif sys.argv[1] == 'hold-client':
        bus = connection()
        print('ready', flush=True)
        inhibit(bus, sys.argv[2])
        time.sleep(20)
    else:
        host(sys.argv[2])
