"""Typed accessibility discovery stays scoped to the physical status service."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from kilix_sdk import accessibility_bus as service

try:
    from gi.repository import Gio, GLib
except ImportError:
    Gio = GLib = None


@unittest.skipUnless(Gio, 'system python3-gi is required')
class AccessibilityBusTests(unittest.TestCase):
    def message(self, interface, method, body=None):
        message = Gio.DBusMessage.new_method_call(service.NAME, service.PATH, interface, method)
        if body is not None:
            message.set_body(body)
        return message

    def test_discovery_status_and_explicit_boolean_changes_are_allowed(self):
        for interface, method, body in [
            (service.NAME, 'GetAddress', None),
            ('org.freedesktop.DBus.Introspectable', 'Introspect', None),
            ('org.freedesktop.DBus.Peer', 'Ping', None),
            ('org.freedesktop.DBus.Peer', 'GetMachineId', None),
            (service.PROPERTIES, 'GetAll', GLib.Variant('(s)', (service.STATUS,))),
            (service.PROPERTIES, 'Get', GLib.Variant('(ss)', (service.STATUS, 'IsEnabled'))),
            (service.PROPERTIES, 'Set', GLib.Variant('(ssv)', (service.STATUS, 'ScreenReaderEnabled', GLib.Variant('b', True))))]:
            with self.subTest(method=method):
                self.assertTrue(service.allowed(self.message(interface, method, body)))

    def test_other_services_methods_properties_and_types_are_refused(self):
        for interface, method, body in [
            (service.NAME, 'GetAddress', GLib.Variant('(s)', ('other',))),
            (service.NAME, 'Other', None),
            ('org.example.Application', 'GetAddress', None),
            (service.PROPERTIES, 'GetAll', GLib.Variant('(s)', ('org.example.Application',))),
            (service.PROPERTIES, 'Get', GLib.Variant('(ss)', (service.STATUS, 'Other'))),
            (service.PROPERTIES, 'Set', GLib.Variant('(ssv)', (service.STATUS, 'IsEnabled', GLib.Variant('s', 'true')))),
            (service.PROPERTIES, 'Set', GLib.Variant('(ss)', (service.STATUS, 'IsEnabled')))]:
            with self.subTest(method=method, interface=interface):
                self.assertFalse(service.allowed(self.message(interface, method, body)))

    def test_discovery_cannot_transport_file_descriptors(self):
        message=self.message(service.NAME, 'GetAddress')
        import os
        read, write=os.pipe()
        try:
            fds=Gio.UnixFDList.new();fds.append(read);message.set_unix_fd_list(fds)
            self.assertFalse(service.allowed(message))
        finally:
            os.close(read);os.close(write)

    def test_status_signals_preserve_only_the_typed_status_contract(self):
        self.assertTrue(service.status_signal(GLib.Variant('(sa{sv}as)',
            (service.STATUS, {'IsEnabled':GLib.Variant('b', True)}, ['ScreenReaderEnabled']))))
        for body in [None, GLib.Variant('(s)', (service.STATUS,)),
                     GLib.Variant('(sa{sv}as)', ('org.example.Application', {}, [])),
                     GLib.Variant('(sa{sv}as)', (service.STATUS, {'Other':GLib.Variant('b', True)}, [])),
                     GLib.Variant('(sa{sv}as)', (service.STATUS, {'IsEnabled':GLib.Variant('s', 'true')}, [])),
                     GLib.Variant('(sa{sv}as)', (service.STATUS, {}, ['Other']))]:
            self.assertFalse(service.status_signal(body))


if __name__=='__main__':unittest.main()
