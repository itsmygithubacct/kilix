"""Owned temporary buses only; no connection to the operator's session."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from gi.repository import Gio, GLib

NAME = "org.freedesktop.portal.Desktop"
ROOT = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.BridgeFixture"
A11Y = 'org.a11y.Bus'
A11Y_PATH = '/org/a11y/bus'
STATUS = 'org.a11y.Status'
A11Y_XML = '''<node><interface name="org.a11y.Bus">
<method name="GetAddress"><arg direction="out" type="s"/></method></interface>
<interface name="org.a11y.Status"><property name="IsEnabled" type="b" access="readwrite"/>
<property name="ScreenReaderEnabled" type="b" access="readwrite"/></interface></node>'''
XML = '''<node><interface name="org.freedesktop.portal.BridgeFixture">
<method name="Create"><arg direction="in" type="s"/><arg direction="out" type="o"/></method>
<method name="ExchangeFD"><arg direction="in" type="h"/><arg direction="out" type="h"/></method>
<method name="Close"><arg direction="in" type="o"/></method>
<method name="Singleton"><arg direction="out" type="b"/></method>
</interface></node>'''


def call(bus, method, body):
    return bus.call_sync(NAME, ROOT, IFACE, method, body, None,
                         Gio.DBusCallFlags.NONE, 5000, None)


def client():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    unique = bus.get_unique_name()[1:].replace('.', '_')
    session = call(bus, 'Create', GLib.Variant('(s)', ('session',))).unpack()[0]
    assert session == ROOT + '/session/' + unique + '/session', session
    singleton = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                              'org.freedesktop.DBus', 'RequestName',
                              GLib.Variant('(su)', ('org.example.Singleton', 4)),
                              None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    assert singleton == 1, 'Application singleton name escaped its private bus'
    assert call(bus, 'Singleton', None).unpack()[0] is True
    address = bus.call_sync(A11Y, A11Y_PATH, A11Y, 'GetAddress', None, None,
                           Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    assert address == os.environ['KILIX_PORTAL_HOST_BUS'], 'Accessibility registry was private'
    properties='org.freedesktop.DBus.Properties'
    changed=[]
    bus.signal_subscribe(A11Y, properties, 'PropertiesChanged', A11Y_PATH, STATUS,
                         Gio.DBusSignalFlags.NONE, lambda *_args: changed.append(_args[-1]))
    bus.call_sync(A11Y,A11Y_PATH,properties,'Set',
                  GLib.Variant('(ssv)',(STATUS,'ScreenReaderEnabled',GLib.Variant('b',True))),
                  None,Gio.DBusCallFlags.NONE,5000,None)
    enabled=bus.call_sync(A11Y,A11Y_PATH,properties,'GetAll',GLib.Variant('(s)',(STATUS,)),
                          None,Gio.DBusCallFlags.NONE,5000,None).unpack()[0]
    assert enabled['ScreenReaderEnabled'] is True
    deadline=time.monotonic()+2
    while not changed and time.monotonic()<deadline:
        GLib.MainContext.default().iteration(False);time.sleep(.01)
    assert changed and changed[-1].unpack()[1]['ScreenReaderEnabled'] is True
    with tempfile.TemporaryFile() as supplied:
        supplied.write(b'private-to-host')
        supplied.flush()
        supplied.seek(0)
        fds = Gio.UnixFDList.new()
        index = fds.append(supplied.fileno())
        body, returned = bus.call_with_unix_fd_list_sync(
            NAME, ROOT, IFACE, 'ExchangeFD', GLib.Variant('(h)', (index,)),
            GLib.VariantType.new('(h)'), Gio.DBusCallFlags.NONE, 5000, fds, None)
        fd = returned.get(body.unpack()[0])
        try:
            assert os.read(fd, 100) == b'host-to-private'
        finally:
            os.close(fd)
    call(bus, 'Close', GLib.Variant('(o)', (session,)))
    # A second separate client on the same private bus owns another host
    # connection. Disconnection must close that connection, not the first.
    other = Gio.DBusConnection.new_for_address_sync(
        os.environ['DBUS_SESSION_BUS_ADDRESS'], Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
        Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    other.set_exit_on_close(False)
    other_session = call(other, 'Create', GLib.Variant('(s)', ('other',))).unpack()[0]
    assert other_session != session
    other.close_sync(None)
    time.sleep(.15)
    Path(os.environ['BRIDGE_FIXTURE_PROOF']).write_text(json.dumps({
        'request_session_identity': True, 'fd_in_and_out': True,
        'singleton_names_remain_private': True, 'two_clients_have_separate_host_connections': True,
        'accessibility_discovery_uses_physical_registry': True,
        'accessibility_status_properties_and_signals_stay_typed': True,
        'app_pid': os.getpid()}))
    # Leave this session open; hard-killing the app must close it and relay.
    call(bus, 'Create', GLib.Variant('(s)', ('death',)))
    while True:
        time.sleep(1)


def host(bridge):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                  'RequestName', GLib.Variant('(su)', (NAME, 4)), None,
                  Gio.DBusCallFlags.NONE, 5000, None)
    bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                  'RequestName', GLib.Variant('(su)', (A11Y, 4)), None,
                  Gio.DBusCallFlags.NONE, 5000, None)
    status={'IsEnabled':False,'ScreenReaderEnabled':False}
    def a11y_dispatch(_bus,_sender,_path,_interface,method,_args,invocation):
        assert method=='GetAddress'
        invocation.return_value(GLib.Variant('(s)',(os.environ['DBUS_SESSION_BUS_ADDRESS'],)))
    def get_status(_bus,_sender,_path,_interface,name):
        return GLib.Variant('b',status[name])
    def set_status(_bus,_sender,_path,_interface,name,value):
        status[name]=value.unpack()
        bus.emit_signal(None,A11Y_PATH,'org.freedesktop.DBus.Properties','PropertiesChanged',
                         GLib.Variant('(sa{sv}as)',(STATUS,{name:value},[])))
        return True
    info=Gio.DBusNodeInfo.new_for_xml(A11Y_XML)
    bus.register_object(A11Y_PATH,info.interfaces[0],a11y_dispatch,None,None)
    bus.register_object(A11Y_PATH,info.interfaces[1],None,get_status,set_status)
    bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                  'RequestName', GLib.Variant('(su)', ('org.example.Singleton', 4)), None,
                  Gio.DBusCallFlags.NONE, 5000, None)
    active, owners, disconnected = {}, set(), set()
    def dispatch(_bus, sender, _path, _interface, method, args, invocation):
        try:
            if method == 'Create':
                token = args.unpack()[0]
                path = ROOT + '/session/' + sender[1:].replace('.', '_') + '/' + token
                active[path] = sender
                owners.add(sender)
                invocation.return_value(GLib.Variant('(o)', (path,)))
            elif method == 'Close':
                path = args.unpack()[0]
                assert active.get(path) == sender, 'Session ownership did not match'
                del active[path]
                invocation.return_value(None)
            elif method == 'ExchangeFD':
                fd = invocation.get_message().get_unix_fd_list().get(args.unpack()[0])
                try:
                    assert os.read(fd, 100) == b'private-to-host'
                finally:
                    os.close(fd)
                with tempfile.TemporaryFile() as returned:
                    returned.write(b'host-to-private')
                    returned.flush()
                    returned.seek(0)
                    fds = Gio.UnixFDList.new()
                    idx = fds.append(returned.fileno())
                    invocation.return_value_with_unix_fd_list(GLib.Variant('(h)', (idx,)), fds)
            elif method == 'Singleton':
                invocation.return_value(GLib.Variant('(b)', (True,)))
        except Exception as error:
            invocation.return_dbus_error('org.example.FixtureError', str(error))
    bus.register_object(ROOT, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], dispatch, None, None)
    def owner_changed(_bus, _sender, _path, _iface, _member, args):
        name, old, new = args.unpack()
        if name in owners and old and not new:
            disconnected.add(name)
            for path, owner in list(active.items()):
                if owner == name:
                    del active[path]
    bus.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged',
                         '/org/freedesktop/DBus', None, Gio.DBusSignalFlags.NONE, owner_changed)
    with tempfile.TemporaryDirectory(prefix='kilix-portal-fixture-') as directory:
        proof = Path(directory) / 'proof.json'
        env = dict(os.environ, KILIX_PORTAL_HOST_BUS=os.environ['DBUS_SESSION_BUS_ADDRESS'],
                   BRIDGE_FIXTURE_PROOF=str(proof))
        app = subprocess.Popen(['dbus-run-session', '--', '/usr/bin/python3', bridge, '--wrap', '--',
                                '/usr/bin/python3', __file__, 'client'], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
        try:
            deadline = time.monotonic() + 12
            while not proof.exists() and app.poll() is None and time.monotonic() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(.01)
            assert proof.exists(), 'Private client did not finish: ' + str(app.poll())
            deadline = time.monotonic() + 3
            while (len(disconnected) != 1 or len(active) != 1) and time.monotonic() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(.01)
            assert len(disconnected) == 1 and len(active) == 1, (active, disconnected)
            # dbus-run-session is the supervisor parent; the actual app's
            # process must be killed to exercise the relay parent-death path.
            result = json.loads(proof.read_text())
            app_pid = result.pop('app_pid')
            entry = Path('/proc', str(app_pid))
            args = (entry / 'cmdline').read_bytes().split(b'\0')
            assert __file__.encode() in args and b'client' in args
            assert f'PPid:\t{app.pid}\n' in (entry / 'status').read_text()
            os.kill(app_pid, 9)
            deadline = time.monotonic() + 4
            while (active or len(disconnected) < 2) and time.monotonic() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(.01)
            assert not active and len(disconnected) == 2, (active, owners, disconnected)
            result['client_disconnect_and_hard_app_death_close_host_connections'] = True
            print(json.dumps(result))
        finally:
            if app.poll() is None:
                os.killpg(app.pid, signal.SIGTERM)
            try:
                out, err = app.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(app.pid, signal.SIGKILL)
                out, err = app.communicate(timeout=2)
            if err:
                print(err, file=sys.stderr)


if __name__ == '__main__':
    if sys.argv[1] == 'host':
        host(sys.argv[2])
    else:
        client()
