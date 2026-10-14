"""Actual AppPane and consuming X clients across promoted owner generations."""
import socket
import time
import unittest
from unittest.mock import patch

from Xlib import X, XK
from private_input_fixture import NativeInputFixture
import x11_sandbox
import test_xinject_held_native as native
import test_xinject_dispatch as dispatch
import xinject


class PromotedOwners(NativeInputFixture):
    native_row = native.HeldNative.native_row
    read_edges = native.HeldNative.read_edges

    def pane(self):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        p = dispatch.NativeInput.pane(self, reader)
        p.box = (0, 0, 640, 480)
        return p

    def key(self, p, key, event, mods=1, shifted=None):
        return p.on_key(dict(key=key, event=event, mods=mods, shifted=shifted))

    def drain(self):
        edges = []
        deadline = time.monotonic() + 5
        while self.inj._input:
            self.assertLess(time.monotonic(), deadline, 'accepted input did not recover')
            self.inj.dispatch_pending()
            edges += self.read_edges()
            self.assertLessEqual(len(self.inj._superseded_presses), self.inj._work_items)
            time.sleep(self.inj.input_timeout(.002))
        self.assertFalse(self.inj._superseded_presses)
        self.assertEqual(self.inj.queued_input_bytes, 0)
        self.assertFalse(self.inj._release_pending)
        return edges

    def glyphs(self, edges):
        modifiers = {XK.XK_Alt_L, XK.XK_Control_L, XK.XK_Shift_L, XK.XK_Super_L}
        return [e[2] for e in edges if e[0] == X.KeyPress and e[2] not in modifiers]

    def duplicate_span(self, p, text='界'):
        self.assertTrue(self.key(p, '漢', 1, 3))
        self.assertTrue(p.on_paste(text))
        # A changed modifier report must still be a duplicate of the owner.
        self.assertTrue(self.key(p, '漢', 1, 5))
        self.assertTrue(self.key(p, '漢', 3))

    def test_prior_duplicate_keeps_original_native_release_and_modifiers(self):
        p = self.pane(); code, sym = self.native_row(); self.inj._spare = [code]
        self.duplicate_span(p)
        edges = self.drain()
        self.assertEqual(self.glyphs(edges), [sym, self.inj.keysym_for('界')])
        self.assertEqual([e[:3] for e in edges if e[1] == code][:2],
                         [(X.KeyPress, code, sym), (X.KeyRelease, code, sym)])
        self.assertFalse(any(e[2] == XK.XK_Control_L for e in edges))
        self.assertEqual(self.held(), [])
        self.assertFalse(self.inj._key_presses)

    def test_ordinary_spare_capacity_preserves_the_same_duplicate_verdict(self):
        p = self.pane(); _, sym = self.native_row()
        self.duplicate_span(p)
        self.assertEqual(self.glyphs(self.drain()), [sym, self.inj.keysym_for('界')])
        self.assertEqual(self.held(), [])

    def test_legitimate_press_after_release_is_a_new_generation(self):
        p = self.pane(); code, sym = self.native_row(); self.inj._spare = [code]
        self.duplicate_span(p)
        self.assertTrue(self.key(p, '漢', 1, 5)); self.assertTrue(self.key(p, '漢', 3))
        edges = self.drain()
        self.assertEqual(self.glyphs(edges), [sym, self.inj.keysym_for('界'), sym])
        self.assertTrue(any(e[0] == X.KeyPress and e[2] == XK.XK_Control_L for e in edges))
        self.assertEqual(self.held(), [])

    def test_many_generations_do_not_share_duplicate_suppression(self):
        p = self.pane(); code, sym = self.native_row(); self.inj._spare = [code]
        for _ in range(12): self.duplicate_span(p)
        self.assertEqual(self.glyphs(self.drain()), [sym, self.inj.keysym_for('界')] * 12)
        self.assertEqual(self.held(), [])
        self.assertFalse(self.inj._release_reservations)

    def test_native_alias_and_its_orphan_up_stay_rejected_before_original_up(self):
        p = self.pane(); code, _ = self.native_row(); self.inj._spare = [code]
        self.assertTrue(self.key(p, 'a', 1, 3)); self.inj.dispatch_pending(); self.read_edges()
        self.assertTrue(p.on_paste('a'))
        self.assertTrue(self.inj.chord('漢', xinject.MOD_ALT, 1)); self.read_edges()
        self.assertTrue(self.key(p, 'A', 1)); self.assertTrue(self.key(p, 'A', 3))
        self.assertTrue(self.key(p, 'a', 3))
        self.assertTrue(self.key(p, 'A', 1)); self.assertTrue(self.key(p, 'A', 3))
        edges = self.drain()
        self.assertEqual(self.glyphs(edges), [ord('a'), ord('A')])
        self.assertEqual(set(self.inj._key_presses), {'漢'})
        self.assertTrue(self.inj.chord('漢', 0, 3)); self.read_edges()
        self.assertEqual(self.held(), [])

    def test_shifted_alias_uses_pane_mapping_and_keeps_later_generation(self):
        p = self.pane(); code, sym = self.native_row(); self.inj._spare = [code]
        self.assertTrue(self.key(p, '漢', 1, 3)); self.assertTrue(p.on_paste('界'))
        self.assertTrue(self.key(p, 'x', 1, 2, '漢')); self.assertTrue(self.key(p, 'x', 3))
        self.assertTrue(self.key(p, '漢', 3))
        self.assertTrue(self.key(p, 'x', 1, 2, '漢')); self.assertTrue(self.key(p, 'x', 3))
        self.assertEqual(self.glyphs(self.drain()), [sym, self.inj.keysym_for('界'), sym])
        self.assertEqual(self.held(), [])

    def test_owned_scratch_alias_keeps_verdict_through_rebinding(self):
        p = self.pane(); code, _ = self.native_row(); self.inj._spare = [code]
        self.assertTrue(p.on_paste('文'))
        self.assertTrue(self.inj.chord('界', xinject.MOD_ALT, 1)); self.read_edges()
        self.assertTrue(self.inj._scratch)
        self.assertTrue(self.key(p, 'x', 1, 2, '界')); self.assertTrue(self.key(p, 'x', 3))
        self.assertTrue(self.key(p, '界', 3))
        self.assertTrue(self.key(p, 'x', 1, 2, '界')); self.assertTrue(self.key(p, 'x', 3))
        self.assertEqual(self.glyphs(self.drain()), [self.inj.keysym_for(c) for c in '文界'])
        self.assertEqual(self.held(), [])

    def test_unrelated_future_owner_in_overtaken_span_is_delivered(self):
        p = self.pane(); code, sym = self.native_row(); self.inj._spare = [code]
        self.assertTrue(self.key(p, '漢', 1, 3)); self.assertTrue(p.on_paste('界'))
        self.assertTrue(self.key(p, '文', 1)); self.assertTrue(self.key(p, '文', 3))
        self.assertTrue(self.key(p, '漢', 3))
        self.assertEqual(self.glyphs(self.drain()), [sym] + [self.inj.keysym_for(c) for c in '界文'])
        self.assertEqual(self.held(), [])

    def test_successive_aliases_preserve_projected_scratch_lookup_order(self):
        p = self.pane(); code, _ = self.native_row(); other = code - 1
        self.xd.change_keyboard_mapping(other, [(0, 0)]); self.xd.sync()
        self.xd._update_keymap(other, 1); self.read_edges()
        self.inj._spare = [code, other]
        symbol = self.inj.keysym_for('界')
        self.assertTrue(p.on_paste('界'))
        self.assertTrue(self.inj.chord('界', xinject.MOD_ALT, 1))
        self.assertTrue(self.inj.paste('界'))
        # A rejected alias moves the first owned binding to the LRU end;
        # the next alias then legitimately acquires the safe alternate row.
        self.assertFalse(self.inj.chord('x', xinject.MOD_SHIFT, 1, '界'))
        self.assertTrue(self.inj.chord('y', xinject.MOD_SHIFT, 1, '界'))
        self.read_edges()
        self.assertEqual(self.inj._key_presses, {'界': code, 'y': other})
        self.assertEqual(list(self.inj._scratch), [symbol, (symbol, other)])
        self.assertTrue(self.key(p, 'q', 1, 2, '界')); self.assertTrue(self.key(p, 'q', 3))
        self.assertTrue(self.key(p, 'r', 1, 2, '界'))
        self.assertTrue(self.key(p, 'y', 3))
        edges = self.drain()
        self.assertEqual(self.glyphs(edges), [symbol], 'successive aliases changed ownership after promotion')
        self.assertEqual(self.inj._key_presses, {'界': code})
        self.assertNotIn(other, self.held())
        self.assertIn(code, self.held())
        self.assertEqual(list(self.inj._scratch), [symbol, (symbol, other)])
        self.assertTrue(self.key(p, 'a', 1)); self.assertTrue(self.key(p, 'a', 3))
        self.assertEqual(self.glyphs(self.drain()), [ord('a')])
        self.assertEqual(self.inj._key_presses, {'界': code})

    def test_latched_alias_survives_a_second_restoring_generation(self):
        p = self.pane(); code, _ = self.native_row(); other = code - 1
        self.xd.change_keyboard_mapping(other, [(0, 0)]); self.xd.sync()
        self.xd._update_keymap(other, 1); self.read_edges()
        self.inj._spare = [code, other]; symbol = self.inj.keysym_for('界')
        self.assertTrue(p.on_paste('界')); self.assertTrue(self.inj.chord('界', xinject.MOD_ALT, 1))
        self.assertTrue(self.inj.paste('界'))
        self.assertFalse(self.inj.chord('x', xinject.MOD_SHIFT, 1, '界'))
        self.assertTrue(self.inj.chord('y', xinject.MOD_SHIFT, 1, '界')); self.read_edges()
        self.assertTrue(self.key(p, 'q', 1, 2, '界')); self.assertTrue(self.key(p, 'q', 3))
        self.assertTrue(self.key(p, 'r', 1, 2, '界')); self.assertTrue(self.key(p, 'y', 3))
        with patch.object(xinject, 'INPUT_BATCH', 1): self.inj.dispatch_pending()
        self.read_edges(); self.assertTrue(self.inj._superseded_presses)
        # A synchronous caller acquires the newly released alternate row
        # before the accepted head can use it. Its new real release restores
        # capacity again; old queued alias decisions must retain their origin.
        self.assertFalse(self.inj.chord('r', xinject.MOD_SHIFT, 1, '界'))
        self.assertTrue(self.inj.chord('r', xinject.MOD_SHIFT, 1, '界')); self.read_edges()
        self.assertEqual(self.inj._key_presses, {'界': code, 'r': other})
        self.assertTrue(self.key(p, 'r', 3)); edges = self.drain()
        self.assertEqual(self.glyphs(edges), [symbol])
        self.assertEqual(self.inj._key_presses, {'界': code}); self.assertNotIn(other, self.held())
        self.assertEqual(list(self.inj._scratch), [symbol, (symbol, other)])
        self.assertTrue(self.key(p, 'r', 1, 2, '界')); self.assertTrue(self.key(p, 'r', 3))
        self.assertEqual(self.glyphs(self.drain()), [])
        self.assertNotIn(other, self.held())
        self.assertIn(code, self.held())
        # This is a fresh lookup, so its rejected alias still moves the held
        # row to the LRU end. The next genuine press can use the safe row.
        self.assertEqual(list(self.inj._scratch), [(symbol, other), symbol])
        self.assertTrue(self.key(p, 'r', 1, 2, '界')); self.assertTrue(self.key(p, 'r', 3))
        self.assertEqual(self.glyphs(self.drain()), [symbol])
        self.assertNotIn(other, self.held())
        self.assertIn(code, self.held())
        self.assertEqual(list(self.inj._scratch), [symbol, (symbol, other)])
        self.assertTrue(self.key(p, 'a', 1)); self.assertTrue(self.key(p, 'a', 3))
        self.assertEqual(self.glyphs(self.drain()), [ord('a')])
        self.assertEqual(self.inj._key_presses, {'界': code})

    def test_partial_text_retains_exact_index_and_full_charge_during_promotion(self):
        p = self.pane(); code, sym = self.native_row(); self.inj._spare = [code]
        text = 'a' * 20 + '界文'
        self.assertTrue(p.on_paste(text)); self.inj.dispatch_pending(); edges = self.read_edges()
        self.assertGreater(self.inj._input[0][2], 0)
        self.assertTrue(self.inj.chord('漢', xinject.MOD_ALT, 1)); self.read_edges()
        self.assertTrue(self.key(p, '漢', 1)); self.assertTrue(self.key(p, '漢', 3))
        self.inj.dispatch_pending(); edges += self.read_edges()
        self.assertEqual(self.inj._input[0][1:3], [text, 20])
        self.assertEqual(self.inj.queued_input_bytes, len(text.encode()) + xinject.INPUT_GESTURE_BYTES)
        self.assertEqual(len(self.inj._superseded_presses), 1)
        self.assertEqual(self.xd.get_keyboard_mapping(code, 1)[0][0], sym)
        self.assertGreater(self.inj._rebind_pending[2] - time.monotonic(), .01)
        edges += self.drain()
        self.assertEqual(self.glyphs(edges), [ord('a')] * 20 + [self.inj.keysym_for(c) for c in '界文'])
        self.assertEqual(self.held(), [])

    def test_focus_cancels_overtaken_verdicts_and_new_input_recovers(self):
        p = self.pane(); code, _ = self.native_row(); self.inj._spare = [code]
        self.duplicate_span(p); self.inj.dispatch_pending(); self.read_edges()
        self.assertTrue(self.inj._superseded_presses)
        p.on_focus({'in': False}); self.read_edges()
        self.assertFalse(self.inj._superseded_presses); self.assertFalse(self.inj._input)
        self.assertEqual(self.held(), []); self.assertEqual(self.inj.queued_input_bytes, 0)
        p.on_focus({'in': True})
        self.assertTrue(self.key(p, '漢', 1)); self.assertTrue(self.key(p, '漢', 3))
        self.assertEqual(self.glyphs(self.drain()), [self.inj.keysym_for('漢')])
        self.assertEqual(self.held(), [])

    def test_full_work_admission_retains_overtaken_work_charge_until_dispatch(self):
        p = self.pane(); code, sym = self.native_row(); self.inj._spare = [code]
        self.duplicate_span(p)
        for _ in range(xinject.INPUT_ITEMS - 3): self.assertTrue(p.on_paste('z'))
        self.assertFalse(p.on_paste('overflow'))
        self.assertEqual(self.inj._work_items, xinject.INPUT_ITEMS)
        charge = self.inj.queued_input_bytes
        # This assertion is about retained byte/work accounting after the
        # promoted pair is dispatched. Bound this fixture to those two items;
        # the production time slice is intentionally load-sensitive.
        with patch.object(xinject, 'INPUT_BATCH', 2):
            self.inj.dispatch_pending()
        edges = self.read_edges()
        self.assertEqual(self.inj.queued_input_bytes, charge - 2 * xinject.INPUT_GESTURE_BYTES)
        self.assertEqual(self.inj._work_items, xinject.INPUT_ITEMS - 1)
        self.assertEqual(len(self.inj._superseded_presses), 1)
        edges += self.drain()
        self.assertEqual(self.glyphs(edges), [sym, self.inj.keysym_for('界')] + [ord('z')] * (xinject.INPUT_ITEMS - 3))
        self.assertEqual(self.held(), []); self.assertFalse(self.inj._release_reservations)


def load_tests(loader, tests, pattern):
    return x11_sandbox.sandbox_load_tests(loader, tests, __name__)


if __name__ == '__main__': unittest.main()
