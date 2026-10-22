"""Native bridge guards; the pointer-driven GTK acceptance test is separate."""
import base64
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'config'))
from Xlib import X, Xatom
from kilix_sdk import xapp, xdnd
from apprun import RunTerm


class ParserTests(unittest.TestCase):
    def test_scoped_metadata(self):
        message = xdnd.parse_message(b'72;t=M:X=10:Y=20:o=3:s=2;text/uri-list')
        self.assertEqual(message['t'], 'M')
        self.assertEqual(message['X'], 10)
        self.assertEqual(message['payload'], b'text/uri-list')
        for bad in (b'73;t=q;', b'72;t=q:t=e;', b'72;o=1:o=2;', b'72;t=zz;',
                    b'72;x=99999999999;', b'72;t=q;' + b'x' * 5000):
            self.assertIsNone(xdnd.parse_message(bad), bad[:80])

    def test_offer_limits(self):
        self.assertEqual(xdnd._types(b'text/plain text/plain UTF8_STRING'), ('text/plain', 'UTF8_STRING'))
        for value in (b'', b'not-a-mime', b'text/\xff', b'text/plain\0', b'x' * 4097,
                      b' '.join(f'application/test-{n}'.encode() for n in range(65))):
            with self.assertRaises((ValueError, UnicodeError)):
                xdnd._types(value)

    def test_fragmented_and_overlong_osc_never_types_payload(self):
        read, write = os.pipe()
        os.set_blocking(read, False)
        term = RunTerm.__new__(RunTerm)
        term.fd, term.inbuf = read, b''
        try:
            os.write(write, b'\x1b]72;t=e:x=4:o=2;')
            self.assertEqual(term.read_input(), [])
            os.write(write, b'\x1b\\')
            self.assertEqual(term.read_input()[0]['o'], 2)
            # Pipe writes stay below capacity while the parser consumes a
            # deliberately unterminated, oversized control string.
            os.write(write, b'\x1b]bad;')
            for _ in range(20):
                os.write(write, b'x' * 4096)
                self.assertEqual(term.read_input(), [])
            self.assertLessEqual(len(term.inbuf), 65536)
            os.write(write, b'NEVER_TYPE_THIS\x1b')
            self.assertEqual(term.read_input(), [])
            os.write(write, b'\\safe')
            self.assertEqual(term.read_input(), [{'kind': 'paste', 'text': 'safe'}])
        finally:
            os.close(read); os.close(write)


@unittest.skipUnless(shutil.which('Xvfb') and shutil.which('xauth'), 'owned Xvfb required')
class NativeGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='xdnd-unit-')
        cls.environment = patch.dict(os.environ, {'KILIX_STORAGE_HOME': cls.temp.name,
            'KILIX_SESSION_HOME': cls.temp.name, 'KILIX_HOST_CLIP': '0'})
        cls.environment.start()
        cls.session = xapp.XAppSession('xdnd-unit-' + str(os.getpid()), 360, 240)
        cls.session.start_xvfb(); cls.session.connect()

    @classmethod
    def tearDownClass(cls):
        cls.session.close(); cls.environment.stop(); cls.temp.cleanup()

    def setUp(self):
        self.messages = []
        self.box = [10, 20, 720, 480]
        self.injector = SimpleNamespace(_btns_down=set(), app_w=360, app_h=240,
            chord=Mock(), release_all=Mock(), move_click=Mock())
        self.bridge = xdnd.NativeDndBridge(self.session.display, self.session.xauthority,
            self.messages.append, lambda: self.box, self.injector)
        self.bridge.handle({'t': 'q', 'payload': b'native=1'})
        self.messages.clear()

    def tearDown(self):
        self.bridge.close()

    def target(self, **values):
        bridge = self.bridge
        bridge.d.sync()  # Collect errors from the preceding fixture first.
        self.assertTrue(bridge._refresh_epoch())
        if values.get('dropped'):
            values.setdefault('action', 1)  # A valid Drop has a negotiated action.
        if values.get('reading', -1) >= 0:
            values.setdefault('idle', time.monotonic() + xdnd.IDLE_TIMEOUT)
            values.setdefault('hard', time.monotonic() + xdnd.TRANSFER_TIMEOUT)
        target = xdnd._Target(('text/plain',), 3, 1, **values)
        target.owner = bridge.root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
            X.InputOnly, X.CopyFromParent, event_mask=X.PropertyChangeMask)
        bridge.target = target
        target.owner.change_property(bridge.atoms['_KILIX_DND_TIME'], Xatom.INTEGER, 8, b'1')
        until = time.monotonic() + 1
        while not target.acquired and time.monotonic() < until:
            bridge.d.sync()
            bridge.tick()
        self.assertNotEqual(target.acquired, X.CurrentTime)
        if target.deadline > time.monotonic():
            self.assertTrue(bridge._target_current(target))
        return target

    def test_engine_capability_is_required(self):
        self.bridge.enabled = False
        self.bridge.handle({'t': 'q', 'payload': b''})
        self.assertFalse(self.bridge.enabled)
        self.assertEqual(self.messages, [])

    def test_coordinates_follow_live_image_and_refuse_letterbox(self):
        point = self.bridge._point
        self.assertEqual(point({'X': 370, 'Y': 260}), (180, 120))
        for x, y in ((9, 20), (730, 20), (10, 500), (10, 19)):
            self.assertIsNone(point({'X': x, 'Y': y}))
        self.box[:] = [0, 0, 360, 240]
        self.assertEqual(point({'X': 180, 'Y': 120}), (180, 120))

    def test_owner_without_forwarded_press_cannot_export(self):
        owner = self.bridge.root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
            X.InputOnly, X.CopyFromParent)
        owner.set_selection_owner(self.bridge.atoms['XdndSelection'], X.CurrentTime)
        self.bridge.d.sync()
        self.bridge._native_owner_changed(SimpleNamespace(owner=owner, selection_timestamp=0))
        self.assertIsNone(self.bridge.source)
        self.assertEqual(self.messages, [])
        owner.destroy()

    def test_native_atom_name_cannot_be_retokenized_as_two_types(self):
        owner = self.bridge.root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
            X.InputOnly, X.CopyFromParent)
        self.injector._btns_down.add(1)
        owner.set_selection_owner(self.bridge.atoms['XdndSelection'], X.CurrentTime)
        self.bridge.d.sync()
        self.bridge.tick()
        self.assertIsNotNone(self.bridge.source)
        atom = self.bridge.d.intern_atom('text/plain injected/offer')
        try:
            with self.assertRaises(ValueError):
                self.bridge._source_enter([owner.id, 5 << 24, atom, 0, 0])
            self.assertEqual(self.bridge.source.types, ())
        finally:
            owner.destroy()

    def test_stale_owner_notification_does_not_cancel_current_target(self):
        target = self.target()
        self.bridge._native_owner_changed(SimpleNamespace(owner=0, selection_timestamp=0))
        self.assertIs(self.bridge.target, target)

    def test_finished_requires_drop_and_correct_peer(self):
        target = self.target(receiver=1234, action=1, dropped=True)
        self.bridge._target_finished([9999, 1, self.bridge.atoms['XdndActionCopy'], 0, 0])
        self.assertIs(self.bridge.target, target)
        self.assertEqual(self.messages, [])
        target.dropped = False
        self.bridge._target_finished([1234, 1, self.bridge.atoms['XdndActionCopy'], 0, 0])
        self.assertIs(self.bridge.target, target)

    def test_refusal_or_wrong_action_never_becomes_success(self):
        for flags, action in ((0, 'XdndActionCopy'), (1, 'XdndActionMove'), (1, 'XdndActionLink')):
            self.target(receiver=1234, action=1, dropped=True, cache={'text/plain': b'complete'})
            self.bridge._target_finished([1234, flags, self.bridge.atoms[action], 0, 0])
            self.assertIn(b't=r:o=0;', self.messages[-1])
            self.assertIsNone(self.bridge.target)

    def test_unfinished_incremental_transfer_cannot_complete(self):
        self.target(receiver=1234, action=1, dropped=True, cache={'text/plain': b'complete'})
        self.bridge.outgoing[(1, 2)] = {'data': b'unfinished'}
        self.bridge._target_finished([1234, 1, self.bridge.atoms['XdndActionCopy'], 0, 0])
        self.assertIn(b't=r:o=0;', self.messages[-1])

    def test_finished_without_payload_delivery_is_refused(self):
        self.target(receiver=1234, action=1, dropped=True)
        self.bridge._target_finished([1234, 1, self.bridge.atoms['XdndActionCopy'], 0, 0])
        self.assertIn(b't=r:o=0;', self.messages[-1])

    def test_unpadded_data_requires_matching_index_and_empty_eod(self):
        target = self.target(dropped=True, reading=0)
        payload = '你好🙂'.encode()
        self.bridge._target_data({'t': 'r', 'x': 2, 'm': 0, 'payload': base64.b64encode(payload)})
        self.assertEqual(target.data, b'')
        self.bridge._target_data({'t': 'r', 'x': 1, 'm': 0, 'payload': base64.b64encode(payload).rstrip(b'=')})
        self.assertEqual(bytes(target.data), payload)
        self.assertEqual(target.cache, {})
        self.bridge._target_data({'t': 'r', 'x': 1, 'm': 0, 'payload': b''})
        self.assertEqual(target.cache['text/plain'], payload)

    def test_payload_limit_is_enforced_before_publication(self):
        self.target(dropped=True, reading=0)
        with patch.object(xdnd, 'MAX_BYTES', 3), self.assertRaises(ValueError):
            self.bridge._target_data({'t': 'r', 'x': 1, 'm': 0, 'payload': b'YWJjZA=='})
        self.assertEqual(self.bridge.target.cache, {})

    def test_absolute_timeout_refuses_target(self):
        self.target(dropped=True, deadline=time.monotonic() - 1)
        self.bridge.tick()
        self.assertIsNone(self.bridge.target)
        self.assertTrue(any(b't=r:o=0;' in message for message in self.messages))

    def test_source_delete_requires_confirmed_move_and_native_drop(self):
        source = xdnd._Source(1234, 0, operations=3, started=True, finish=2)
        self.bridge.source = source
        self.bridge._source_finished()
        self.assertIsNone(source.delete_window)
        source.native_drop = True
        source.finish = 1
        with patch.object(self.bridge, '_message'), patch.object(self.bridge, '_clear_source'):
            self.bridge._source_finished()
        self.assertIsNone(source.delete_window)
        self.bridge.source = None


if __name__ == '__main__':
    unittest.main()
