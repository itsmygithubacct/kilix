"""Use the physical accessibility registry while app singleton buses stay private.

GTK/ATK discovers its AT-SPI connection through org.a11y.Bus.GetAddress.
Forward only this discovery/status API, never arbitrary session-bus names.
The accessible objects themselves use the toolkit's ordinary AT-SPI transport.
"""

NAME = 'org.a11y.Bus'
PATH = '/org/a11y/bus'
STATUS = 'org.a11y.Status'
PROPERTIES = 'org.freedesktop.DBus.Properties'
STATUS_NAMES = frozenset(('IsEnabled', 'ScreenReaderEnabled'))


def allowed(message):
    body = message.get_body()
    signature = body.get_type_string() if body is not None else '()'
    interface, method = message.get_interface(), message.get_member()
    fds = message.get_unix_fd_list()
    if fds is not None and fds.get_length():
        return False
    if interface == NAME:
        return method == 'GetAddress' and signature == '()'
    if interface == 'org.freedesktop.DBus.Introspectable':
        return method == 'Introspect' and signature == '()'
    if interface == 'org.freedesktop.DBus.Peer':
        return method in ('Ping', 'GetMachineId') and signature == '()'
    if interface != PROPERTIES:
        return False
    signatures = {'Get': '(ss)', 'GetAll': '(s)', 'Set': '(ssv)'}
    if method not in signatures or signature != signatures[method]:
        return False
    if body.get_child_value(0).get_string() != STATUS:
        return False
    if method == 'GetAll':
        return True
    if body.get_child_value(1).get_string() not in STATUS_NAMES:
        return False
    return method != 'Set' or body.get_child_value(2).get_variant().get_type_string() == 'b'


def status_signal(body):
    if body is None or body.get_type_string() != '(sa{sv}as)':
        return False
    if body.get_child_value(0).get_string() != STATUS:
        return False
    changed = body.get_child_value(1)
    for index in range(changed.n_children()):
        entry = changed.get_child_value(index)
        if (entry.get_child_value(0).get_string() not in STATUS_NAMES
                or entry.get_child_value(1).get_variant().get_type_string() != 'b'):
            return False
    return all(name in STATUS_NAMES for name in body.get_child_value(2).unpack())
