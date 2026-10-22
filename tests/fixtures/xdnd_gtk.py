#!/usr/bin/python3
"""Independent GTK3 source/target. All DND runs through normal GTK callbacks."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gdk, GdkX11, GLib, Gtk

role, folder = sys.argv[1:3]
folder = Path(folder)
sys.stderr = (folder / (role + '.stderr')).open('a', buffering=1)

def settings():
    return json.loads((folder / 'control.json').read_text())

def record(kind, **values):
    with (folder / (role + '.jsonl')).open('a') as stream:
        stream.write(json.dumps({'event': kind, 'time': time.monotonic(), 'pid': os.getpid(),
            'display': os.environ['DISPLAY'], **values}, ensure_ascii=False) + '\n')

window = Gtk.Window(title='XDND ' + role)
window.set_default_size(360, 240)
quit_handler = window.connect('destroy', Gtk.main_quit)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
window.add(box)
label = Gtk.Label(label='Native GTK ' + role + '\nDrag text / files using the pointer')
box.pack_start(label, False, False, 0)
area = Gtk.EventBox()
area.add_events(Gdk.EventMask.SCROLL_MASK)
area.connect('scroll-event', lambda widget, event: record('ordinary-scroll', direction=int(event.direction)) or True)
area.add(Gtk.Label(label='DRAG SOURCE' if role in ('source', 'physical') else 'NATIVE DROP TARGET'))
box.pack_start(area, True, True, 0)
entry = Gtk.Entry()
entry.set_placeholder_text('Ordinary keyboard input')
entry.connect('changed', lambda widget: record('entry-changed', text=widget.get_text()))
for prop in ('selection-bound', 'cursor-position'):
    entry.connect('notify::' + prop, lambda widget, _: record('entry-selection', bounds=widget.get_selection_bounds()))
entry.connect('focus-in-event', lambda *_: record('entry-focused') or False)
window.connect('key-press-event', lambda widget, event: record('key-press', key=Gdk.keyval_name(event.keyval)) or False)
box.pack_start(entry, False, False, 0)

types = settings().get('types', ['text/plain;charset=utf-8', 'text/plain', 'text/uri-list'])
targets = [Gtk.TargetEntry.new(name, 0, index) for index, name in enumerate(types)]
ops = Gdk.DragAction.COPY | Gdk.DragAction.MOVE
if role in ('source', 'physical'):
    area.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, targets, ops)
    area.connect('drag-begin', lambda *_: record('drag-begin'))
    def get_data(widget, context, selection, info, stamp):
        control = settings()
        payload = (folder / control.get('payload', 'payload')).read_bytes()
        selection.set(selection.get_target(), 8, payload)
        record('drag-data-get', type=selection.get_target().name(), size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest())
    area.connect('drag-data-get', get_data)
    area.connect('drag-end', lambda *_: record('drag-end'))
    def failed(widget, context, reason):
        record('drag-failed', reason=int(reason))
        return False
    area.connect('drag-failed', failed)
    def delete(widget, context):
        record('drag-data-delete')
        for relative in settings().get('move_files', []):
            file = (folder / relative).resolve()
            if folder.resolve() not in file.parents:
                raise ValueError('fixture path escaped')
            file.unlink()
    area.connect('drag-data-delete', delete)
else:
    area.drag_dest_set(Gtk.DestDefaults.HIGHLIGHT, targets, ops)
    def motion(widget, context, x, y, stamp):
        control = settings()
        action = context.get_suggested_action()
        if control.get('refuse') or action not in (Gdk.DragAction.COPY, Gdk.DragAction.MOVE):
            action = Gdk.DragAction(0)
        Gdk.drag_status(context, action, stamp)
        record('drag-motion', x=x, y=y, action=int(action))
        return True
    area.connect('drag-motion', motion)
    def dropped(widget, context, x, y, stamp):
        record('drag-drop', x=x, y=y)
        if not settings().get('timeout'):
            target = widget.drag_dest_find_target(context, None)
            if target != Gdk.Atom.intern('NONE', False):
                widget.drag_get_data(context, target, stamp)
        return True
    area.connect('drag-drop', dropped)
    def received(widget, context, x, y, selection, info, stamp):
        payload = bytes(selection.get_data() or b'')
        action = context.get_selected_action()
        control = settings()
        success = selection.get_length() >= 0 and not control.get('refuse_data')
        record('drag-data-received', type=selection.get_target().name(),
            size=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
            payload=base64.b64encode(payload).decode('ascii'), action=int(action), success=success)
        if success and action == Gdk.DragAction.MOVE:
            for relative in control.get('move_files', []):
                file = (folder / relative).resolve()
                if folder.resolve() not in file.parents:
                    raise ValueError('fixture path escaped')
                destination = folder / ('copied-' + file.name)
                destination.write_bytes(file.read_bytes())
        Gtk.drag_finish(context, success, success and action == Gdk.DragAction.MOVE, stamp)
    area.connect('drag-data-received', received)
    area.connect('drag-leave', lambda *_: record('drag-leave'))

clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
last_command = ''
def poll():
    global last_command, window, quit_handler
    command = folder / (role + '.command')
    if command.exists():
        text = command.read_text()
        if text != last_command:
            last_command = text
            if text.startswith('clip-read:'):
                mime = text.partition(':')[2].partition('|')[0]
                data = clipboard.wait_for_contents(Gdk.Atom.intern(mime, False))
                payload = bytes(data.get_data() or b'') if data else b''
                record('clipboard-received', type=mime, size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
            elif text.startswith('clip-copy:'):
                clipboard.set_text((folder / 'clipboard-text').read_text(), -1)
                record('clipboard-set')
            elif text == 'replace-window':
                old = window
                old.remove(box)
                old.disconnect(quit_handler)
                old.destroy()
                window = Gtk.Window(title='XDND replacement ' + role)
                window.set_default_size(360, 240)
                quit_handler = window.connect('destroy', Gtk.main_quit)
                window.add(box)
                window.show_all()
                record('window-replaced', xid=GdkX11.X11Window.get_xid(window.get_window()))
            elif text == 'steal-dnd':
                Gtk.Clipboard.get(Gdk.Atom.intern('XdndSelection', False)).set_text('replacement selection', -1)
                record('selection-stolen')
    return True
GLib.timeout_add(40, poll)

window.show_all()
def ready():
    origin = area.translate_coordinates(window, 0, 0)
    size = area.get_allocation()
    record('ready', xid=GdkX11.X11Window.get_xid(window.get_window()),
        area=[origin[0], origin[1], size.width, size.height])
    return False
GLib.timeout_add(200, ready)
record('started')
Gtk.main()
