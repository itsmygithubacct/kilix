"""Rendered recovery instructions follow the actual admission failure."""
import socket
import time

import private_input_fixture as fixture
import test_xinject_dispatch as dispatch
import test_xinject_held_native as native
import apprun
import xinject
from Xlib import X


class RefusalNotice(fixture.NativeInputFixture):
    read_edges = native.HeldNative.read_edges

    def pane(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        pane = dispatch.NativeInput.pane(self, reader)
        pane.term.cols, pane.term.rows = 100, 30
        pane.debug = False
        pane.cmd, pane.disp, pane.app_w, pane.app_h, pane.frames = ['fixture'], ':private', 640, 480, 0
        self.output = []
        pane.term.write = self.output.append
        return pane

    def status(self, pane):
        apprun.AppPane.render_status(pane)
        return self.output[-1]

    def drain(self):
        edges = []
        until = time.monotonic() + 4
        while self.inj._input:
            self.assertLess(time.monotonic(), until)
            self.inj.dispatch_pending()
            edges.extend(self.read_edges())
            time.sleep(self.inj.input_timeout(.005))
        return edges

    def test_oversize_empty_queue_requires_reduction_even_after_retry(self):
        pane = self.pane()
        for text in ('x' * (xinject.INPUT_BYTES + 1), '😀' * (xinject.INPUT_BYTES // 4 + 1)):
            for _ in range(2):
                self.assertFalse(pane.on_paste(text))
                self.assertEqual(self.inj.queued_input_bytes, 0)
                self.assertFalse(self.inj._input)
                status = self.status(pane)
                self.assertIn('item too large; reduce or split paste', status)
                self.assertNotIn('retry after drain', status)
                self.inj.dispatch_pending()
                self.assertEqual(self.read_edges(), [])

    def test_queue_full_advises_drain_and_successful_retry_clears_notice(self):
        pane = self.pane()
        for _ in range(xinject.INPUT_ITEMS):
            self.assertTrue(pane.on_paste('a'))
        self.assertFalse(pane.on_paste('b'))
        self.assertIn('queue full; retry after drain', self.status(pane))
        self.assertEqual([e[2] for e in self.drain() if e[0] == X.KeyPress], [ord('a')] * xinject.INPUT_ITEMS)
        self.assertTrue(pane.on_paste('b'))
        self.assertIsNone(pane._input_notice)
        self.assertNotIn('input refused', self.status(pane))
        self.assertEqual([e[2] for e in self.drain() if e[0] == X.KeyPress], [ord('b')])

    def test_invalid_unicode_input_or_unknown_reason_never_advises_drain(self):
        pane = self.pane()
        cases = [('paste', ('\ud800',), 'use valid Unicode'),
                 ('paste', (123,), 'correct input'),
                 ('chord', ('a', 0, 3), 'check press/release pair')]
        for kind, args, action in cases:
            self.assertFalse(pane._queue_input(kind, *args))
            status = self.status(pane)
            self.assertIn(action, status)
            self.assertNotIn('retry after drain', status)
            self.assertEqual(self.inj.queued_input_bytes, 0)
        pane._input_notice = 'unknown refusal'
        self.assertIn('unknown refusal; check input', self.status(pane))
        self.assertNotIn('retry after drain', self.status(pane))
        self.assertEqual(self.read_edges(), [])

    def test_capacity_refusal_requests_release_then_explicit_retry_delivers(self):
        pane = self.pane()
        code, _ = native.HeldNative.native_row(self)
        self.inj._spare = [code]
        self.inj.chord('漢', xinject.MOD_ALT, 1)
        self.read_edges()
        self.assertFalse(pane.on_paste('界'))
        self.assertIn('key capacity unavailable; release held keys; retry', self.status(pane))
        self.assertNotIn('retry after drain', self.status(pane))
        self.assertIn(code, self.held())
        self.assertTrue(pane.on_key({'key': '漢', 'mods': 1, 'event': 3}))
        self.drain()
        self.assertTrue(pane.on_paste('界'))
        self.assertIsNone(pane._input_notice)
        self.assertEqual([e[2] for e in self.drain() if e[0] == X.KeyPress], [self.inj.keysym_for('界')])

    def test_accepted_capacity_wait_is_visible_then_recovers_without_a_refusal(self):
        pane = self.pane()
        code, _ = native.HeldNative.native_row(self)
        self.inj._spare = [code]
        self.assertTrue(pane.on_paste('界'))
        self.inj.chord('漢', xinject.MOD_ALT, 1)
        self.read_edges()
        self.inj.dispatch_pending()
        self.assertIn('input waiting: key capacity; release held keys', self.status(pane))
        self.assertNotIn('input refused', self.status(pane))
        self.assertEqual(self.inj.queued_input_bytes, 3)
        self.assertTrue(pane.on_key({'key': '漢', 'mods': 1, 'event': 3}))
        self.assertEqual([e[2] for e in self.drain() if e[0] == X.KeyPress], [self.inj.keysym_for('界')])
        self.assertNotIn('input waiting', self.status(pane))
        self.assertEqual(self.held(), [])


def load_tests(loader, tests, pattern):
    return fixture.x11_sandbox.sandbox_load_tests(loader, tests, __name__)
