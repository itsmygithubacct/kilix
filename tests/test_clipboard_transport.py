"""Real authenticated X11 clipboard exchanges, including INCR and races."""
import os
from pathlib import Path
import select
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'config'))
from kilix_sdk import clipboard as clip, xapp
from Xlib import X, Xatom
from Xlib.protocol import event as xevent


class ContentTests(unittest.TestCase):
    def test_text_encodings_and_immutable_binary_payloads(self):
        value = clip.Content.from_text('héllo 世界')
        self.assertEqual(value.text, 'héllo 世界')
        self.assertEqual(value.get('STRING'), b'h\xe9llo ??')
        raw = bytearray(b'\x00\xffPNG')
        value = clip.Content({'image/png': raw})
        raw[0] = 9
        self.assertEqual(value.get('image/png'), b'\x00\xffPNG')
        self.assertEqual(value.text, '')
        with self.assertRaises(ValueError):
            clip.Content({'DELETE': b''})
        with mock.patch.object(clip, 'MAX_BYTES', 3):
            with self.assertRaises(ValueError):
                clip.Content({'image/png': b'abcd'})

    def test_local_files_preserve_spaces_unicode_and_cut_semantics(self):
        paths = ['/tmp/white space.txt', '/tmp/é\nname.txt']
        value = clip.Content.from_files(paths, cut=True)
        self.assertEqual(value.files, ('cut', tuple(paths)))
        self.assertEqual(clip.Content({'text/uri-list': value.get('text/uri-list')}).files,
                         ('copy', tuple(paths)))
        self.assertEqual(clip.Content({'text/uri-list': b'file://localhost/tmp/a\r\n',
            'application/x-kde-cutselection': b'1'}).files, ('cut', ('/tmp/a',)))
        self.assertEqual(clip.Content({'text/uri-list':
            b'# comment\r\nhttps://example.com/a\r\nfile://remote/tmp/a\r\nfile:///tmp/%00bad\r\n'}).files[1], ())


@unittest.skipUnless(shutil.which('Xvfb') and shutil.which('xauth'), 'Xvfb and xauth required')
class ClipboardTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kilix-clipboard-test-')
        self.env = mock.patch.dict(os.environ, {
            'KILIX_STORAGE_HOME': self.tmp.name, 'KILIX_SESSION_HOME': self.tmp.name,
            'KILIX_HOST_CLIP': '0'})
        self.env.start()
        self.sessions = []
        self.hubs = []
        self.bridges = []
        for name in ('first', 'second'):
            session = xapp.XAppSession('clip-'+name+'-'+str(os.getpid()), 64, 48)
            self.sessions.append(session)
            session.start_xvfb()
            session.connect()
        self.hub = clip.Hub()
        self.hubs.append(self.hub)
        for session in self.sessions:
            self.bridges.append(clip.SelectionBridge(self.hub, session.display,
                                                     session.xauthority))

    def tearDown(self):
        for bridge in reversed(self.bridges):
            bridge.close()
        for session in reversed(self.sessions):
            session.close()
        self.env.stop()
        self.tmp.cleanup()

    def pump(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for hub in self.hubs:
                for callback in tuple(hub.tick_hooks):
                    callback()
            if predicate():
                return
            time.sleep(.002)
        self.fail('clipboard exchange timed out')

    def owner(self, content, which=0):
        source_hub = clip.Hub()
        self.hubs.append(source_hub)
        session = self.sessions[which]
        owner = clip.SelectionBridge(source_hub, session.display, session.xauthority)
        self.bridges.append(owner)
        source_hub.set_clipboard_content(content)
        return owner

    def receive(self, name, which=1):
        self.pump(lambda: bool(self.sessions[which].xd.get_selection_owner(
            self.sessions[which].xd.intern_atom('CLIPBOARD'))))
        d = self.sessions[which].xd
        w = d.screen().root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
            window_class=X.InputOnly, visual=X.CopyFromParent,
            event_mask=X.PropertyChangeMask)
        target, prop = d.intern_atom(name), d.intern_atom('_TEST_CLIP_READ')
        w.convert_selection(d.intern_atom('CLIPBOARD'), target, prop, X.CurrentTime)
        d.flush()
        result = bytearray()
        incremental = False
        done = False
        def read_events():
            nonlocal done, incremental
            while d.pending_events():
                ev = d.next_event()
                if ev.type == X.SelectionNotify and ev.requestor.id == w.id:
                    if not ev.property:
                        raise AssertionError('selection refused')
                    reply = w.get_property(prop, X.AnyPropertyType, 0, clip.MAX_BYTES // 4)
                    if reply.property_type == d.intern_atom('INCR'):
                        incremental = True
                    else:
                        result.extend(reply.value)
                        done = True
                    w.delete_property(prop)
                    d.flush()
                elif (incremental and ev.type == X.PropertyNotify and ev.window.id == w.id
                      and ev.atom == prop and ev.state == X.PropertyNewValue):
                    reply = w.get_property(prop, X.AnyPropertyType, 0, clip.MAX_BYTES // 4)
                    if reply is None or reply.property_type == d.intern_atom('INCR'):
                        continue
                    self.assertEqual(reply.format, 8)
                    self.assertEqual(reply.property_type, target)
                    result.extend(reply.value)
                    done = not len(reply.value)
                    w.delete_property(prop)
                    d.flush()
            return done
        try:
            self.pump(read_events, timeout=8)
            return bytes(result), incremental
        finally:
            w.destroy()
            d.flush()

    def test_large_image_and_unicode_text_cross_servers_byte_for_byte(self):
        import io
        from PIL import Image
        image = Image.frombytes('RGB', (512, 512), os.urandom(512*512*3))
        output = io.BytesIO()
        image.save(output, format='PNG')
        raw = output.getvalue()
        self.assertGreater(len(raw), 65536)
        content = clip.Content({'image/png': raw,
                                'UTF8_STRING': '世界 héllo'.encode()})
        self.owner(content)
        self.pump(lambda: self.hub.content == content)
        self.pump(lambda: self.bridges[1]._owns_selection())
        copied, incremental = self.receive('image/png')
        self.assertTrue(incremental)
        self.assertEqual(copied, raw)
        self.assertEqual(self.receive('UTF8_STRING')[0], '世界 héllo'.encode())
        self.assertTrue(all(bridge._ok for bridge in self.bridges))

    def test_readable_callback_drains_events_buffered_during_a_reply(self):
        bridge = self.bridges[0]
        self.pump(lambda: all(item._owns_selection() for item in self.bridges))
        while bridge.d.pending_events():
            bridge._handle(bridge.d.next_event())
        first, second = [bridge.d.intern_atom(name) for name in
                         ('_BUFFERED_FIRST', '_BUFFERED_SECOND')]
        received = []
        handle = bridge._handle

        def observe(ev):
            handle(ev)
            if ev.type == X.PropertyNotify and ev.window.id == bridge.win.id:
                received.append(ev.atom)
                if ev.atom == first:
                    bridge.win.change_property(second, Xatom.INTEGER, 8, b'')
                    # Replies can read the next event into python-xlib's
                    # buffer, leaving no socket readiness for select().
                    bridge.d.sync()

        bridge.win.change_property(first, Xatom.INTEGER, 8, b'')
        bridge.d.sync()
        with mock.patch.object(bridge, '_handle', side_effect=observe):
            bridge._on_readable()
        self.assertIn(first, received)
        self.assertIn(second, received,
                      'Buffered clipboard events must not wait for another socket wakeup')

    def test_file_formats_and_reverse_direction(self):
        content = clip.Content.from_files(['/tmp/a b', '/tmp/é'], cut=True)
        self.owner(content, which=1)
        self.pump(lambda: self.hub.content == content)
        self.pump(lambda: self.bridges[0]._owns_selection())
        self.assertEqual(self.receive('text/uri-list', which=0)[0], content.get('text/uri-list'))
        self.assertEqual(self.receive('x-special/gnome-copied-files', which=0)[0],
                         content.get('x-special/gnome-copied-files'))

    def test_new_owner_replaces_a_stalled_read_without_old_data(self):
        d = self.sessions[0].xd
        stalled = d.screen().root.create_window(-10,-10,1,1,0,X.CopyFromParent,
            window_class=X.InputOnly,visual=X.CopyFromParent)
        stalled.set_selection_owner(d.intern_atom('CLIPBOARD'), X.CurrentTime)
        d.flush()
        self.pump(lambda: self.bridges[0]._incoming is not None)
        previous = self.bridges[0]._incoming['window'].id
        content = clip.Content.from_text('new copy')
        self.owner(content)
        self.pump(lambda: self.hub.content == content)
        self.assertNotEqual(self.bridges[0]._read_owner, stalled.id)
        self.pump(lambda: self.bridges[1]._owns_selection())
        self.assertEqual(self.receive('UTF8_STRING')[0], b'new copy')
        stalled.destroy()
        d.flush()

    def test_paste_during_copy_waits_for_new_complete_content(self):
        self._pending_copy_paste(abort=False)

    def test_failed_pending_copy_refuses_paste_instead_of_returning_old_data(self):
        self._pending_copy_paste(abort=True)

    def test_first_paste_in_new_empty_pane_waits_for_in_progress_copy(self):
        self._pending_copy_paste(abort=False, previous=False)

    def test_ready_format_pastes_while_another_format_is_still_being_collected(self):
        self.hub.set_clipboard_content(clip.Content.from_text('previous value'))
        self.pump(lambda: all(bridge._owns_selection() for bridge in self.bridges))
        source, destination = (session.xd for session in self.sessions)
        owner = source.screen().root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
            window_class=X.InputOnly, visual=X.CopyFromParent)
        requestor = destination.screen().root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
            window_class=X.InputOnly, visual=X.CopyFromParent)
        requests, notices = [], []

        def observe():
            while source.pending_events():
                ev = source.next_event()
                if ev.type == X.SelectionRequest:
                    requests.append(ev)
            while destination.pending_events():
                ev = destination.next_event()
                if ev.type == X.SelectionNotify and ev.requestor.id == requestor.id:
                    notices.append(ev)

        def answer(value, property_type, width):
            request = requests.pop(0)
            request.requestor.change_property(request.property, property_type, width, value)
            request.requestor.send_event(xevent.SelectionNotify(time=request.time,
                requestor=request.requestor.id, selection=request.selection,
                target=request.target, property=request.property))
            source.flush()

        try:
            owner.set_selection_owner(source.intern_atom('CLIPBOARD'), X.CurrentTime)
            source.flush()
            self.pump(lambda: (observe() or bool(requests)))
            answer([source.intern_atom('UTF8_STRING'), source.intern_atom('image/png')],
                   Xatom.ATOM, 32)
            self.pump(lambda: (observe() or bool(requests)))
            prop = destination.intern_atom('_READY_FORMAT_TARGETS')
            requestor.convert_selection(destination.intern_atom('CLIPBOARD'),
                destination.intern_atom('TARGETS'), prop, X.CurrentTime)
            destination.flush()
            self.pump(lambda: (observe() or bool(notices)))
            self.assertEqual(notices[0].property, prop)
            targets = requestor.get_property(prop, X.AnyPropertyType, 0, 256)
            self.assertIn(destination.intern_atom('UTF8_STRING'), targets.value)
            self.assertIn(destination.intern_atom('image/png'), targets.value)
            expected = 'new café 世界'.encode() * 10000
            # A complete large UTF-8 representation becomes available while
            # the owner still withholds an independent PNG representation.
            answer(expected, source.intern_atom('UTF8_STRING'), 8)
            self.pump(lambda: (observe() or bool(requests)))
            self.assertEqual(requests[0].target, source.intern_atom('image/png'))
            self.assertEqual(self.hub.content.text, 'previous value')
            self.assertIsNotNone(self.hub.clipboard_read_revision)
            self.assertEqual(self.receive('UTF8_STRING')[0], expected)
            self.assertEqual(self.hub.content.text, 'previous value')
        finally:
            requestor.destroy(); destination.flush()
            owner.destroy(); source.flush()

    def _pending_copy_paste(self, *, abort, previous=True):
        if previous:
            self.hub.set_clipboard_content(clip.Content.from_text('previous value'))
            self.pump(lambda: all(bridge._owns_selection() for bridge in self.bridges))
        else:
            deadline=time.monotonic()+.05
            self.pump(lambda:time.monotonic()>=deadline)
        source, destination = (session.xd for session in self.sessions)
        owner = source.screen().root.create_window(-10,-10,1,1,0,X.CopyFromParent,
            window_class=X.InputOnly,visual=X.CopyFromParent)
        requestor = destination.screen().root.create_window(-10,-10,1,1,0,X.CopyFromParent,
            window_class=X.InputOnly,visual=X.CopyFromParent,event_mask=X.PropertyChangeMask)
        owner.set_selection_owner(source.intern_atom('CLIPBOARD'), X.CurrentTime)
        source.flush()
        source_requests = []
        notices = []
        prop = destination.intern_atom('_PASTE_DURING_COPY')
        request_time = []
        def observe():
            while source.pending_events():
                ev = source.next_event()
                if ev.type == X.SelectionRequest: source_requests.append(ev)
            while destination.pending_events():
                ev = destination.next_event()
                if ev.type == X.SelectionNotify and ev.requestor.id == requestor.id:
                    notices.append(ev)
                if ev.type == X.PropertyNotify and ev.window.id == requestor.id:
                    request_time.append(ev.time)
            return False
        try:
            self.pump(lambda: (observe() or bool(source_requests)))
            requestor.change_property(prop,Xatom.INTEGER,8,b'timestamp')
            destination.flush()
            self.pump(lambda: (observe() or bool(request_time)))
            requestor.convert_selection(destination.intern_atom('CLIPBOARD'),
                destination.intern_atom('UTF8_STRING'), prop, request_time[-1])
            destination.flush()
            deadline = time.monotonic() + .1
            self.pump(lambda: (observe() or time.monotonic() >= deadline))
            self.assertEqual(notices, [], 'Paste returned the previous value before the new copy completed')
            answer = source_requests.pop(0)
            if not abort:
                answer.requestor.change_property(answer.property, Xatom.ATOM, 32,
                    [source.intern_atom('UTF8_STRING')])
            answer.requestor.send_event(xevent.SelectionNotify(time=answer.time,
                requestor=answer.requestor.id, selection=answer.selection,
                target=answer.target, property=0 if abort else answer.property))
            source.flush()
            if not abort:
                self.pump(lambda: (observe() or bool(source_requests)))
                answer = source_requests.pop(0)
                answer.requestor.change_property(answer.property, source.intern_atom('UTF8_STRING'),
                    8, 'new café 世界'.encode())
                answer.requestor.send_event(xevent.SelectionNotify(time=answer.time,
                    requestor=answer.requestor.id, selection=answer.selection,
                    target=answer.target, property=answer.property))
                source.flush()
            else:
                # TARGETS refusal triggers the supported legacy text fallback.
                def refuse_remaining():
                    observe()
                    for answer in source_requests[:]:
                        answer.requestor.send_event(xevent.SelectionNotify(time=answer.time,
                            requestor=answer.requestor.id,selection=answer.selection,
                            target=answer.target,property=0))
                        source_requests.remove(answer)
                    source.flush()
                    return bool(notices)
                self.pump(refuse_remaining)
            self.pump(lambda: (observe() or bool(notices)))
            if abort:
                self.assertEqual(notices[0].property, 0)
                self.assertEqual(self.hub.content.text, 'previous value')
            else:
                self.assertEqual(notices[0].property, prop)
                self.assertEqual(bytes(requestor.get_property(prop,X.AnyPropertyType,0,1024).value),
                    'new café 世界'.encode())
        finally:
            requestor.destroy();destination.flush();owner.destroy();source.flush()

    def test_pending_paste_expires_without_returning_previous_content(self):
        self.hub.set_clipboard_content(clip.Content.from_text('previous value'))
        self.pump(lambda: all(bridge._owns_selection() for bridge in self.bridges))
        source,destination=(session.xd for session in self.sessions)
        owner=source.screen().root.create_window(-10,-10,1,1,0,X.CopyFromParent,
            window_class=X.InputOnly,visual=X.CopyFromParent)
        req=destination.screen().root.create_window(-10,-10,1,1,0,X.CopyFromParent,
            window_class=X.InputOnly,visual=X.CopyFromParent)
        notices=[]
        try:
            owner.set_selection_owner(source.intern_atom('CLIPBOARD'),X.CurrentTime);source.flush()
            self.pump(lambda:self.bridges[0]._incoming is not None)
            req.convert_selection(destination.intern_atom('CLIPBOARD'),destination.intern_atom('UTF8_STRING'),
                destination.intern_atom('_EXPIRED_PASTE'),X.CurrentTime);destination.flush()
            def expired():
                while destination.pending_events():
                    ev=destination.next_event()
                    if ev.type==X.SelectionNotify and ev.requestor.id==req.id:notices.append(ev)
                return bool(notices)
            with mock.patch.object(clip,'TRANSFER_TIMEOUT',.05):self.pump(expired)
            self.assertEqual(notices[0].property,0)
            self.assertEqual(self.hub.content.text,'previous value')
        finally:
            req.destroy();destination.flush();owner.destroy();source.flush()

    def test_pending_copy_invalidates_cached_formats_before_acquisition(self):
        self.hub.set_clipboard_content(clip.Content({}))
        self.pump(lambda:all(bridge._owns_selection() for bridge in self.bridges))
        source,destination=(session.xd for session in self.sessions)
        req=destination.screen().root.create_window(-10,-10,1,1,0,X.CopyFromParent,
            window_class=X.InputOnly,visual=X.CopyFromParent)
        owner=source.screen().root.create_window(-10,-10,1,1,0,X.CopyFromParent,
            window_class=X.InputOnly,visual=X.CopyFromParent)
        selection=destination.intern_atom('CLIPBOARD')
        destination.xfixes_query_version()
        destination.xfixes_select_selection_input(req,selection,
            clip.xfixes.XFixesSetSelectionOwnerNotifyMask)
        destination.sync()
        while destination.pending_events():destination.next_event()
        notices=[];source_requests=[]
        event_type=destination.query_extension('XFIXES').first_event+clip.xfixes.XFixesSelectionNotify
        try:
            owner.set_selection_owner(source.intern_atom('CLIPBOARD'),X.CurrentTime);source.flush()
            def invalidated():
                while source.pending_events():
                    ev=source.next_event()
                    if ev.type==X.SelectionRequest:source_requests.append(ev)
                while destination.pending_events():
                    ev=destination.next_event()
                    if ev.type==event_type and ev.selection==selection:
                        notices.append(getattr(ev.owner,'id',ev.owner))
                return source_requests and self.bridges[1].win.id in notices
            self.pump(invalidated)
            self.assertEqual(self.hub.content,clip.Content({}))
            self.assertEqual(len(source_requests),1,'New formats must not arrive before the source answers')
        finally:
            req.destroy();destination.flush();owner.destroy();source.flush()

    def test_outgoing_incr_survives_new_copy_and_expires_when_abandoned(self):
        original = b'old image' * 20000
        self.hub.set_clipboard_content(clip.Content({'image/png': original}))
        self.pump(lambda: self.bridges[1]._owns_selection())
        bridge = self.bridges[1]
        d = self.sessions[1].xd
        requestor = d.screen().root.create_window(-10,-10,1,1,0,X.CopyFromParent,
            window_class=X.InputOnly,visual=X.CopyFromParent)
        prop = d.intern_atom('_TEST_ABANDONED')
        self.assertTrue(bridge._write_target(requestor, bridge.atoms['image/png'], prop))
        key = (requestor.id, prop)
        self.hub.set_clipboard_content(clip.Content.from_text('new'))
        self.assertEqual(bridge._outgoing[key]['data'], original)
        bridge._outgoing[key]['deadline'] = 0
        bridge.tick()
        self.assertNotIn(key, bridge._outgoing)
        requestor.destroy()
        d.flush()

    def test_managed_app_relay_starts_ready_mirrors_existing_copy_and_closes(self):
        for bridge in self.bridges:
            bridge.close()
        self.bridges.clear()
        content = clip.Content.from_text('initial host clipboard')
        owner = self.owner(content)
        self.pump(lambda: owner._owns_selection())
        host, private = self.sessions
        with mock.patch.dict(os.environ, {
                'KILIX_HOST_CLIP': '1', 'PLEB_DESKTOP_DISPLAY': host.display,
                'PLEB_DESKTOP_XAUTHORITY': host.xauthority}):
            private.launch_app([sys.executable, '-c', 'import time; time.sleep(20)'])
        relay = private.clipboard_process
        self.assertIsNotNone(relay)
        self.assertIsNone(relay.poll())
        self.assertEqual(self.receive('UTF8_STRING')[0], b'initial host clipboard')
        private.close()
        self.assertIsNotNone(relay.poll())

    def test_multiple_requests_return_each_format_and_refuse_side_effects(self):
        self.hub.set_clipboard_content(clip.Content.from_text('hello'))
        bridge = self.bridges[0]
        self.pump(lambda: bridge._owns_selection())
        d = self.sessions[0].xd
        req = d.screen().root.create_window(-10,-10,1,1,0,X.CopyFromParent,
            window_class=X.InputOnly,visual=X.CopyFromParent)
        prop, text, timestamp, denied = [d.intern_atom(name) for name in
            ('_MULTIPLE_TEST','_MULTIPLE_TEXT','_MULTIPLE_TIME','_MULTIPLE_DENIED')]
        targets = [d.intern_atom('UTF8_STRING'),text,d.intern_atom('TIMESTAMP'),timestamp,
                   d.intern_atom('DELETE'),denied]
        req.change_property(prop,d.intern_atom('ATOM_PAIR'),32,targets)
        req.convert_selection(d.intern_atom('CLIPBOARD'),d.intern_atom('MULTIPLE'),prop,X.CurrentTime)
        d.flush()
        notices=[]
        def ready():
            while d.pending_events():
                ev=d.next_event()
                if ev.type==X.SelectionNotify and ev.requestor.id==req.id:notices.append(ev)
            return bool(notices)
        self.pump(ready)
        pairs=req.get_property(prop,X.AnyPropertyType,0,64)
        self.assertEqual(list(pairs.value)[4],0)
        self.assertEqual(bytes(req.get_property(text,X.AnyPropertyType,0,64).value),b'hello')
        value=req.get_property(timestamp,X.AnyPropertyType,0,64)
        self.assertEqual(value.property_type,Xatom.INTEGER)
        self.assertGreater(value.value[0],0)
        self.assertIsNone(req.get_property(denied,X.AnyPropertyType,0,64))
        req.destroy()
        d.flush()

    def test_oversized_incr_is_rejected_without_replacing_current_content(self):
        baseline=clip.Content.from_text('existing clipboard')
        self.hub.set_clipboard_content(baseline)
        self.pump(lambda: self.bridges[0]._owns_selection())
        d=self.sessions[0].xd
        owner=d.screen().root.create_window(-10,-10,1,1,0,X.CopyFromParent,
            window_class=X.InputOnly,visual=X.CopyFromParent)
        owner.set_selection_owner(d.intern_atom('CLIPBOARD'),X.CurrentTime)
        d.flush()
        served=[]
        def respond():
            while d.pending_events():
                ev=d.next_event()
                if ev.type!=X.SelectionRequest:continue
                if ev.target==d.intern_atom('TARGETS'):
                    ev.requestor.change_property(ev.property,Xatom.ATOM,32,[d.intern_atom('UTF8_STRING')])
                else:
                    ev.requestor.change_property(ev.property,d.intern_atom('INCR'),32,[clip.MAX_BYTES+1])
                ev.requestor.send_event(xevent.SelectionNotify(time=ev.time,requestor=ev.requestor.id,
                    selection=ev.selection,target=ev.target,property=ev.property))
                served.append(ev.target)
                d.flush()
            return len(served)==2 and self.bridges[0]._incoming is None
        self.pump(respond)
        self.assertEqual(self.hub.content,baseline)
        self.assertTrue(self.bridges[0]._ok)
        owner.destroy()
        d.flush()


if __name__ == '__main__':
    unittest.main()
