"""Truthful whole-paste capacity, retained delivery and restoring release edges."""
import json
import socket
import time

import private_input_fixture as fixture
import test_xinject_dispatch as dispatch
import test_xinject_held_native as native
import xinject
from Xlib import X


class Capacity(fixture.NativeInputFixture):
    read_edges = native.HeldNative.read_edges
    native_row = native.HeldNative.native_row

    def pane(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        p = dispatch.NativeInput.pane(self, reader)
        p.box = (0, 0, 640, 480)
        return p

    def drain(self):
        out = []
        until = time.monotonic() + 4
        while self.inj._input:
            self.assertLess(time.monotonic(), until, 'accepted paste/release deadlocked')
            self.inj.dispatch_pending()
            out.extend(self.read_edges())
            time.sleep(self.inj.input_timeout(.005))
        return out

    def hold_one(self):
        code, sym = self.native_row()
        self.inj._spare = [code]
        self.assertTrue(self.inj.chord('漢', xinject.MOD_ALT, 1))
        self.read_edges()
        return code, sym

    def assert_whole_refusal(self, pane, text):
        before = (len(self.inj._input), self.inj.queued_input_bytes)
        self.assertFalse(pane.on_paste(text), 'no-capacity paste claimed admission')
        self.assertEqual(self.inj.last_input_refusal, 'key capacity unavailable')
        self.assertEqual((len(self.inj._input), self.inj.queued_input_bytes), before)
        self.inj.dispatch_pending()
        self.assertEqual(self.read_edges(), [])

    def test_one_row_all_held_refuses_whole_mixed_paste_and_recovers_after_real_release(self):
        pane = self.pane()
        code, sym = self.hold_one()
        self.assert_whole_refusal(pane, 'a界z漢')
        self.assertIn(code, self.held())
        self.assertEqual(self.xd.get_keyboard_mapping(code, 1)[0][0], sym)
        self.assertTrue(pane.on_key({'key': '漢', 'mods': 1, 'event': 3}))
        releases = self.drain()
        self.assertEqual([e[:3] for e in releases if e[1] == code], [(X.KeyRelease, code, sym)])
        self.assertTrue(pane.on_paste('a界z漢'))
        edges = self.drain()
        self.assertEqual([e[2] for e in edges if e[0] == X.KeyPress],
                         [ord('a'), self.inj.keysym_for('界'), ord('z'), sym])
        self.assertEqual(self.held(), [])
        self.assertEqual(self.inj.queued_input_bytes, 0)

    def test_all_actual_discovered_rows_held_refuse_without_mapping_or_owner_changes(self):
        pane = self.pane()
        codes = self.inj._find_spare()
        self.assertTrue(codes)
        syms = [self.inj.keysym_for(chr(0x6000 + i)) for i in range(len(codes))]
        for code, sym in zip(codes, syms):
            self.xd.change_keyboard_mapping(code, [(sym, sym)])
        self.xd.sync()
        for code in codes:
            self.xd._update_keymap(code, 1)
        self.assertEqual(self.inj._find_spare(), codes)
        for i in range(len(codes)):
            self.assertTrue(self.inj.chord(chr(0x6000 + i), xinject.MOD_ALT, 1))
        self.read_edges()
        self.assert_whole_refusal(pane, 'a界' + chr(0x6000))
        self.assertTrue(set(codes) <= set(self.held()))
        for code, sym in zip(codes, syms):
            self.assertEqual(self.xd.get_keyboard_mapping(code, 1)[0][0], sym)
        print(json.dumps({'all_discovered_rows': codes, 'paste_refused': True,
                          'retained_bytes': self.inj.queued_input_bytes}), flush=True)
        self.assertTrue(pane.on_key({'key': chr(0x6000), 'mods': 1, 'event': 3}))
        self.drain()
        self.assertTrue(pane.on_paste('界' + chr(0x6001)))
        edges = self.drain()
        self.assertEqual([e[2] for e in edges if e[0] == X.KeyPress], [self.inj.keysym_for('界'), syms[1]])
        self.assertTrue(set(codes[1:]) <= set(self.held()))

    def test_safe_existing_native_paste_still_works_with_all_scratch_rows_held(self):
        pane = self.pane()
        code, sym = self.hold_one()
        self.assertTrue(pane.on_paste('a!\t\n'))
        edges = self.drain()
        self.assertEqual([e[2] for e in edges if e[0] == X.KeyPress and e[2] != xinject.XK.XK_Shift_L],
                         [ord('a'), ord('!'), xinject.XK.XK_Tab, xinject.XK.XK_Return])
        self.assertIn(code, self.held())
        self.assertFalse(any(e[0] == X.KeyRelease and e[1] == code for e in edges))
        self.assertEqual(self.xd.get_keyboard_mapping(code, 1)[0][0], sym)

    def test_same_glyph_no_alternate_refuses_instead_of_releasing_owner(self):
        pane = self.pane()
        code, sym = self.hold_one()
        self.assert_whole_refusal(pane, '漢')
        self.assertIn(code, self.held())
        self.assertEqual(self.inj._key_presses['漢'], code)
        self.assertEqual(self.xd.get_keyboard_mapping(code, 1)[0][0], sym)

    def pending_capacity_loss(self, partial=False):
        pane = self.pane()
        code, _ = self.native_row()
        self.inj._spare = [code]
        text = 'a' * 20 + '界文' if partial else '界文'
        self.assertTrue(pane.on_paste(text))
        expected_charge = len(text.encode('utf-8'))
        prefix = []
        if partial:
            self.inj.dispatch_pending()
            prefix = self.read_edges()
            self.assertEqual(len([e for e in prefix if e[0] == X.KeyPress]), xinject.INPUT_BATCH)
        # Another synchronous owner acquired the last row after admission.
        self.assertTrue(self.inj.chord('漢', xinject.MOD_ALT, 1))
        self.read_edges()
        self.inj.dispatch_pending()
        blocked = self.read_edges()
        self.assertTrue(self.inj._input, 'accepted suffix disappeared at capacity loss')
        self.assertEqual(self.inj.queued_input_bytes, expected_charge)
        self.assertTrue(self.inj.input_capacity_pending)
        self.assertGreater(self.inj.input_timeout(), 0, 'blocked paste spins instead of yielding')
        self.assertFalse(any(e[2] == self.inj.keysym_for('界') for e in blocked))
        self.assertTrue(pane.on_paste('b'))
        self.assertTrue(pane.on_key({'key': '漢', 'mods': 1, 'event': 3}))
        # The legitimate restoring release is reachable behind the blocked
        # paste, without discarding its prefix or retained full string charge.
        edges = prefix + blocked + self.drain()
        expected = [ord(c) for c in text if ord(c) < 256]
        expected += [self.inj.keysym_for('界'), self.inj.keysym_for('文'), ord('b')]
        self.assertEqual([e[2] for e in edges if e[0] == X.KeyPress], expected)
        actual_release = [e for e in edges if e[0] == X.KeyRelease and e[1] == code
                          and e[2] == self.inj.keysym_for('漢')]
        self.assertEqual(len(actual_release), 1)
        self.assertEqual(self.held(), [])
        self.assertEqual(self.inj.queued_input_bytes, 0)
        self.assertFalse(self.inj._release_pending)
        self.assertFalse(self.inj.input_capacity_pending)

    def test_capacity_lost_after_admission_retains_paste_and_restoring_release_cannot_deadlock(self):
        self.pending_capacity_loss()

    def test_partially_delivered_paste_keeps_full_charge_and_suffix_until_release_recovery(self):
        self.pending_capacity_loss(partial=True)

    def test_queued_press_that_uses_last_row_then_paste_then_release_is_delivered(self):
        pane = self.pane()
        code, sym = self.native_row()
        self.inj._spare = [code]
        self.assertTrue(pane.on_key({'key': '漢', 'mods': 3, 'event': 1}))
        self.assertTrue(pane.on_paste('界'))
        self.assertTrue(pane.on_key({'key': '漢', 'mods': 1, 'event': 3}))
        edges = self.drain()
        self.assertEqual([e[2] for e in edges if e[0] == X.KeyPress and e[2] != xinject.XK.XK_Alt_L],
                         [sym, self.inj.keysym_for('界')])
        self.assertEqual([e[0] for e in edges if e[2] == xinject.XK.XK_Alt_L],
                         [X.KeyPress, X.KeyRelease])
        self.assertEqual([e[:3] for e in edges if e[0] == X.KeyRelease and e[2] == sym], [(X.KeyRelease, code, sym)])
        self.assertEqual(self.held(), [])

    def test_blocked_paste_focus_cancellation_clears_charges_and_real_owners(self):
        pane = self.pane()
        code, _ = self.native_row()
        self.inj._spare = [code]
        self.assertTrue(pane.on_paste('界'))
        self.inj.chord('漢', xinject.MOD_ALT, 1)
        self.read_edges()
        self.inj.dispatch_pending()
        self.assertTrue(self.inj._input)
        pane.on_focus({'in': False})
        self.assertFalse(self.inj._input)
        self.assertEqual(self.inj.queued_input_bytes, 0)
        self.assertEqual(self.held(), [])
        self.assertFalse(self.inj.input_capacity_pending)


def load_tests(loader, tests, pattern):
    return fixture.x11_sandbox.sandbox_load_tests(loader, tests, __name__)
