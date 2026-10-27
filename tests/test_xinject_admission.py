"""Finite retained input, whole-item refusal and native release integrity."""
import json
import os
import select
import signal
import socket
import threading
import time
from unittest.mock import patch

import private_input_fixture as fixture
import test_xinject_dispatch as dispatch
import test_xinject_held_native as native
import apprun
import xinject
from Xlib import X


class Admission(fixture.NativeInputFixture):
    def pane(self):
        reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        return dispatch.NativeInput.pane(self, reader), reader, writer

    def key(self, pane, key, event=1, mods=1):
        return pane.on_key({'key': key, 'mods': mods, 'event': event})

    def retained(self):
        # Independently recompute what is retained, including the full string
        # after partial dispatch. Dispatch counts are not an admission bound.
        return sum(len(row[1].encode('utf-8')) if row[0] == 'paste'
                   else xinject.INPUT_GESTURE_BYTES for row in self.inj._input)

    def assert_bound(self):
        self.assertLessEqual(len(self.inj._input), xinject.INPUT_ITEMS + xinject.INPUT_RELEASES)
        self.assertLessEqual(self.retained(), xinject.INPUT_BYTES +
                             xinject.INPUT_RELEASES * xinject.INPUT_GESTURE_BYTES)
        self.assertEqual(self.inj.queued_input_bytes, self.retained())
        self.assertLessEqual(self.inj._work_items, xinject.INPUT_ITEMS)
        self.assertLessEqual(self.inj._work_bytes, xinject.INPUT_BYTES)
        self.assertLessEqual(len(self.inj._release_reservations) + self.inj._queued_releases,
                             xinject.INPUT_RELEASES)

    def drain(self):
        seen = []
        until = time.monotonic() + 10
        while self.inj._input:
            self.assertLess(time.monotonic(), until, 'input did not recover')
            self.inj.dispatch_pending()
            # dispatch_pending only flushes the injector's connection; sync it
            # so the server has processed that input before the app round trip.
            self.xd.sync()
            self.app.sync()
            self.events(seen)
            self.assert_bound()
            time.sleep(self.inj.input_timeout(.001))
        self.assertEqual(self.inj.queued_input_bytes, 0)
        return seen

    def test_actual_pane_bursts_bound_total_items_and_bytes_without_dispatch(self):
        pane, _, _ = self.pane()
        accepted, refused, counts = 0, 0, []
        previous = 0
        for stop in (2000, 4000, 8000):
            for i in range(previous, stop):
                if pane.on_paste('漢' + str(i)):
                    accepted += 1
                else:
                    refused += 1
            previous = stop
            counts.append({'attempted': stop, 'accepted': accepted, 'refused': refused,
                           'items': len(self.inj._input), 'retained_utf8_bytes': self.retained()})
        print(json.dumps({'no_dispatch': True, 'growth': counts}), flush=True)
        self.assertEqual(len(self.inj._input), xinject.INPUT_ITEMS)
        self.assertEqual(accepted, xinject.INPUT_ITEMS)
        self.assertEqual(refused, 8000 - accepted)
        self.assertEqual(len(self.inj._input), accepted)
        self.assertEqual(pane._input_notice, 'queue full')
        self.assert_bound()

    def test_aggregate_unicode_byte_bound_is_distinct_from_item_bound(self):
        pane, _, _ = self.pane()
        text = '漢' * 10000  # 30,000 UTF-8 bytes per item, 10,000 characters
        accepted = sum(bool(pane.on_paste(text)) for _ in range(200))
        print(json.dumps({'items': len(self.inj._input), 'accepted': accepted,
                          'retained_utf8_bytes': self.retained(),
                          'retained_characters': sum(len(row[1]) for row in self.inj._input)}), flush=True)
        self.assertLessEqual(self.retained(), xinject.INPUT_BYTES)
        self.assertEqual(accepted, xinject.INPUT_BYTES // len(text.encode('utf-8')))
        self.assertLess(len(self.inj._input), xinject.INPUT_ITEMS)
        self.assertEqual(self.retained(), accepted * 30000)
        self.assert_bound()

    def test_single_oversized_ascii_and_unicode_items_have_no_injected_prefix(self):
        pane, _, _ = self.pane()
        for text in ('x' * (xinject.INPUT_BYTES + 1),
                     '😀' * (xinject.INPUT_BYTES // 4 + 1)):
            with self.subTest(chars=len(text)):
                self.assertFalse(pane.on_paste(text))
                self.assertEqual(len(self.inj._input), 0)
                self.assertEqual(self.inj.last_input_refusal, 'item too large')
                self.inj.dispatch_pending()
                self.assertEqual(native.HeldNative.read_edges(self), [])
                self.assert_bound()
        self.assertTrue(pane.on_paste('R'))
        self.assertEqual(self.drain(), [ord('R')])

    def test_exact_byte_limit_and_partial_dispatch_keep_full_retained_charge(self):
        pane, _, _ = self.pane()
        text = 'x' * xinject.INPUT_BYTES
        self.assertTrue(pane.on_paste(text))
        self.assertFalse(pane.on_paste('tail'))
        self.inj.dispatch_pending()
        self.xd.sync()
        self.app.sync()
        seen = []
        self.events(seen)
        self.assertGreater(len(seen), 0)
        self.assertLessEqual(len(seen), xinject.INPUT_BATCH)
        self.assertLess(self.inj._input[0][2], len(text))
        self.assertEqual(self.inj.queued_input_bytes, len(text.encode('utf-8')))
        self.assertFalse(pane.on_paste('x'))
        self.assert_bound()
        pane.on_focus({'in': False})
        self.assertEqual(self.inj.queued_input_bytes, 0)
        self.assertFalse(self.inj._release_reservations)
        self.assertTrue(pane.on_paste('ok'))
        self.assertEqual(self.drain(), [ord('o'), ord('k')])

    def test_practical_multiline_paste_and_refused_items_preserve_accepted_fifo(self):
        pane, _, _ = self.pane()
        text = ('Ab!/ü漢界\n\t' * 512)
        self.assertTrue(pane.on_paste(text))
        self.assertFalse(pane.on_paste('x' * (xinject.INPUT_BYTES + 1)))
        self.assertTrue(pane.on_paste('tail'))
        seen = self.drain()
        expected = [self.inj.keysym_for(xinject.PASTE_KEYS.get(c, c)) for c in text + 'tail']
        self.assertEqual(seen, expected)
        self.assertEqual(self.held(), [])

    def test_keyboard_release_admitted_when_paste_full_preserves_native_alt_and_fifo(self):
        pane, _, _ = self.pane()
        # This is an actual native Unicode row, not an owned scratch binding.
        code = self.xd.display.info.max_keycode
        sym = self.inj.keysym_for('漢')
        self.xd.change_keyboard_mapping(code, [(sym, sym)])
        self.xd.sync()
        self.xd._update_keymap(code, 1)
        self.inj.chord('漢', xinject.MOD_ALT, 1)
        native.HeldNative.read_edges(self)
        for _ in range(xinject.INPUT_ITEMS):
            self.assertTrue(pane.on_paste('界'))
        self.assertFalse(pane.on_paste('refused'))
        self.assertFalse(self.key(pane, 'b'))
        self.assertTrue(self.key(pane, '漢', 3))
        self.assertEqual(len(self.inj._input), xinject.INPUT_ITEMS + 1)
        self.assert_bound()
        edges = []
        while self.inj._input:
            self.inj.dispatch_pending()
            new = native.HeldNative.read_edges(self)
            edges.extend(new)
            if self.inj._input:
                self.assertIn(code, self.held())
            self.assertEqual(self.xd.get_keyboard_mapping(code, 1)[0][0], sym)
            self.assert_bound()
        pasted = [e for e in edges if e[0] == X.KeyPress]
        self.assertEqual([e[2] for e in pasted], [self.inj.keysym_for('界')] * xinject.INPUT_ITEMS)
        self.assertTrue(all(e[3] & X.Mod1Mask for e in pasted))
        release_at = next(i for i, e in enumerate(edges) if e[:3] == (X.KeyRelease, code, sym))
        self.assertEqual(sum(e[0] == X.KeyPress for e in edges[:release_at]), xinject.INPUT_ITEMS)
        self.assertEqual(self.held(), [])
        self.assertFalse(self.inj._mod_holds)
        self.assertFalse(self.key(pane, 'b', 3))
        self.assertEqual(self.inj.last_input_refusal, 'no accepted press')
        print(json.dumps({'accepted_pastes_before_native_release': xinject.INPUT_ITEMS,
                          'native_code': code, 'retained_bound': True, 'held_after': self.held()}), flush=True)

    def test_pending_press_and_mouse_release_use_reserved_slots_at_full_admission(self):
        pane, _, _ = self.pane()
        pane.box = (0, 0, 640, 480)
        self.assertTrue(self.key(pane, 'a', mods=5))
        self.assertTrue(pane.on_mouse({'b': 8, 'x': 1, 'y': 1, 'press': True}))
        for _ in range(xinject.INPUT_ITEMS - 2):
            self.assertTrue(pane.on_paste('x'))
        self.assertFalse(pane.on_paste('refused'))
        self.assertTrue(self.key(pane, 'a', 3))
        self.assertTrue(pane.on_mouse({'b': 0, 'x': 1, 'y': 1, 'press': False}))
        self.assert_bound()
        self.assertEqual(self.drain(), [ord('a')] + [ord('x')] * (xinject.INPUT_ITEMS - 2))
        self.assertEqual(self.held(), [])
        self.assertFalse(self.inj._btns_down)
        self.assertFalse(self.inj._mod_holds)

    def test_press_release_bursts_bound_reserved_and_queued_edges_and_recover(self):
        pane, _, _ = self.pane()
        for _ in range(xinject.INPUT_RELEASES):
            self.assertTrue(self.key(pane, 'a'))
            self.assertTrue(self.key(pane, 'a', 3))
        self.assertEqual(len(self.inj._input), xinject.INPUT_ITEMS + xinject.INPUT_RELEASES)
        self.assert_bound()
        with patch.object(xinject, 'INPUT_BATCH', 1):
            self.inj.dispatch_pending()  # work slot free; all release slots occupied
            self.xd.sync()
            self.app.sync()
            seen = []
            self.events(seen)
            self.assertEqual(seen, [ord('a')])
            self.assertFalse(self.key(pane, 'b'))
            self.assertEqual(self.inj.last_input_refusal, 'release capacity full')
            self.inj.dispatch_pending()  # the first matching release frees a slot
        self.assertTrue(self.key(pane, 'b'))
        self.assertTrue(self.key(pane, 'b', 3))
        self.assert_bound()
        self.assertEqual(self.drain(), [ord('a')] * (xinject.INPUT_RELEASES - 1) + [ord('b')])
        self.assertEqual(self.held(), [])
        self.assertFalse(self.inj._release_reservations)
        self.assertFalse(self.inj._release_pending)

    def test_failed_alias_press_cannot_release_an_existing_native_owner(self):
        pane, _, _ = self.pane()
        self.inj.chord('a', xinject.MOD_ALT, 1)
        code = self.inj._key_presses['a']
        native.HeldNative.read_edges(self)
        self.assertTrue(self.key(pane, 'A'))  # same native keycode as held 'a'
        self.assertTrue(self.key(pane, 'A', 3))
        self.drain()
        self.assertIn(code, self.held())
        self.assertEqual(self.inj._key_presses, {'a': code})
        self.assertTrue(self.key(pane, 'a', 3))
        self.drain()
        self.assertEqual(self.held(), [])

    def test_gesture_payloads_cannot_retain_unbounded_strings_or_caller_dicts(self):
        pane, _, _ = self.pane()
        huge = 'x' * (xinject.INPUT_BYTES + 1)
        self.assertFalse(self.key(pane, huge))
        self.assertFalse(pane.on_key({'key': 'a', 'mods': 1, 'event': 1, 'shifted': huge}))
        self.assertFalse(pane.on_paste('\ud800'))
        pane.box = (0, 0, 640, 480)
        ev = {'b': 32, 'x': 1, 'y': 1, 'press': True, 'unbounded-extra': huge}
        self.assertTrue(pane.on_mouse(ev))
        ev['x'] = huge
        stored = self.inj._input[0][1][0]
        self.assertEqual(stored, {'b': 32, 'x': 1, 'y': 1, 'press': True})
        self.assertIsNot(stored, ev)
        self.assertFalse(pane.on_mouse({'b': 0, 'x': 1 << 10000, 'y': 1, 'press': True}))
        self.assert_bound()
        self.drain()

    def test_refusal_is_visible_and_retry_clears_the_notice(self):
        pane, _, _ = self.pane()
        out = []
        pane.term.write = out.append
        pane.term.cols, pane.term.rows = 100, 30
        pane.debug = False
        pane.cmd, pane.disp, pane.app_w, pane.app_h, pane.frames = ['fixture'], ':private', 640, 480, 0
        self.assertFalse(pane.on_paste('x' * (xinject.INPUT_BYTES + 1)))
        apprun.AppPane.render_status(pane)
        self.assertIn('input refused: item too large; reduce or split paste', out[-1])
        self.assertNotIn('retry after drain', out[-1])
        self.assertTrue(pane.on_paste('ok'))
        self.assertIsNone(pane._input_notice)
        self.assertEqual(self.drain(), [ord('o'), ord('k')])

    def test_whole_oversized_bracketed_paste_refusal_keeps_following_release(self):
        pane, _, _ = self.pane()
        self.inj.chord('a', xinject.MOD_ALT, 1)
        native.HeldNative.read_edges(self)
        term = object.__new__(apprun.RunTerm)
        term.fd = -1
        term.inbuf = (b'\x1b[200~prefix\x1b[31m' + b'x' * xinject.INPUT_BYTES +
                      b'\x1b[201~\x1b[97;1:3u')
        with patch.object(apprun.browse.os, 'read', return_value=b''):
            events = term.read_input()
        self.assertEqual([ev['kind'] for ev in events], ['paste', 'key'])
        self.assertTrue(events[0]['text'].startswith('prefix\x1b[31m'))
        self.assertFalse(pane.on_paste(events[0]['text']))
        self.assertTrue(pane.on_key(events[1]))
        self.assertEqual(self.drain(), [])
        self.assertEqual(term.inbuf, b'')
        self.assertEqual(self.held(), [])

    def test_full_admission_during_rebind_wait_keeps_deadline_fifo_and_release(self):
        pane, _, _ = self.pane()
        code = next(self.xd.display.info.min_keycode + i
                    for i, row in enumerate(self.original) if not any(row))
        self.inj._spare = [code]
        self.inj.chord('c', xinject.MOD_ALT, 1)
        native.HeldNative.read_edges(self)
        self.assertTrue(pane.on_paste('漢界'))
        self.inj.dispatch_pending()
        self.xd.sync()
        self.app.sync()
        seen = []
        self.events(seen)
        self.assertEqual(seen, [self.inj.keysym_for('漢')])
        deadline = self.inj._rebind_pending
        for _ in range(xinject.INPUT_ITEMS - 1):
            self.assertTrue(pane.on_paste('a'))
        self.assertFalse(pane.on_paste('refused'))
        self.assertTrue(self.key(pane, 'c', 3))
        self.assertEqual(self.inj._rebind_pending, deadline)
        self.assertEqual(self.xd.get_keyboard_mapping(code, 1)[0][0], self.inj.keysym_for('漢'))
        self.assert_bound()
        self.assertEqual(self.drain(), [self.inj.keysym_for('界')] +
                         [ord('a')] * (xinject.INPUT_ITEMS - 1))
        self.assertEqual(self.held(), [])

    def run_overload_change(self, close):
        pane, reader, writer = self.pane()
        self.inj.chord('a', xinject.MOD_ALT, 1)
        self.app.sync()
        self.events([])
        chars, seen, heartbeats, notices, errors = [], [], [], [], []
        resize_sent, resize_seen = [], []
        stop = threading.Event()
        accepted = []
        original = pane.on_paste
        pane.on_paste = lambda text: (accepted.append(original(text)) or accepted[-1])
        pane.tick_capture = lambda *a: heartbeats.append(time.monotonic())
        pane.render_status = lambda: notices.append(getattr(pane, '_input_notice', None))
        pane.do_resize = lambda: resize_seen.append(time.monotonic())
        text = ''.join(chr(0x4e00 + i) for i in range(40))
        writer.send(json.dumps([{'kind': 'paste', 'text': text}] * (xinject.INPUT_ITEMS + 44)).encode())

        def client():
            try:
                sent = False
                until = time.monotonic() + 5
                while not stop.is_set() and time.monotonic() < until:
                    if self.app.pending_events() or select.select([self.app], [], [], .001)[0]:
                        self.events(seen)
                    if not sent and len(seen) >= 3:
                        resize_sent.append(time.monotonic())
                        os.kill(os.getpid(), signal.SIGWINCH)
                        if close:
                            writer.send(json.dumps([{'kind': 'key', 'key': 'q', 'mods': 5, 'event': 1}]).encode())
                            return
                        writer.send(json.dumps([{'kind': 'focus', 'in': False},
                                                {'kind': 'focus', 'in': True},
                                                {'kind': 'paste', 'text': 'R'}]).encode())
                        sent = True
                    if sent and seen and seen[-1] == ord('R'):
                        writer.send(json.dumps([{'kind': 'key', 'key': 'q', 'mods': 5, 'event': 1}]).encode())
                        return
                raise AssertionError('concurrent client timed out')
            except BaseException as error:
                errors.append(error)
                writer.send(json.dumps([{'kind': 'key', 'key': 'q', 'mods': 5, 'event': 1}]).encode())

        started = time.monotonic()
        thread = threading.Thread(target=client)
        thread.start()
        try:
            pane.run()
        finally:
            stop.set()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(accepted[:xinject.INPUT_ITEMS + 44],
                         [True] * xinject.INPUT_ITEMS + [False] * 44)
        if not close:
            self.assertTrue(accepted[-1])
            self.assertEqual(seen[-1], ord('R'))
        self.assertIn('queue full', notices)
        self.assertLess(len(seen), len(text) * xinject.INPUT_ITEMS)
        self.assertFalse(self.inj._input)
        self.assertFalse(self.inj._release_reservations)
        self.assertEqual(self.inj.queued_input_bytes, 0)
        self.assertEqual(self.held(), [])
        latency = time.monotonic() - resize_sent[0] if close else resize_seen[0] - resize_sent[0]
        gaps = [b - a for a, b in zip([started] + heartbeats, heartbeats)]
        self.assertLess(latency, .15)
        self.assertLess(max(gaps, default=0), .35)
        print(json.dumps({'close': close, 'accepted': accepted.count(True), 'refused': accepted.count(False),
                          'consumed': len(seen), 'change_latency': latency,
                          'max_loop_gap': max(gaps, default=0), 'final_retained_bytes': self.retained()}), flush=True)

    def test_concurrent_full_admission_focus_loss_and_return_recover_in_actual_loop(self):
        self.run_overload_change(close=False)

    def test_concurrent_full_admission_close_cancels_and_releases_in_actual_loop(self):
        self.run_overload_change(close=True)


def load_tests(loader, tests, pattern):
    return fixture.x11_sandbox.sandbox_load_tests(loader, tests, __name__)
