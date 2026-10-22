"""Paste pairs may never release a native or scratch key owned by a gesture."""
import json
import socket
import time

import private_input_fixture as fixture
import test_xinject_dispatch as dispatch
import test_xinject_held_native as native
import xinject
from Xlib import X


class SameHeld(fixture.NativeInputFixture):
    read_edges = native.HeldNative.read_edges
    native_row = native.HeldNative.native_row

    def drain(self):
        edges = []
        until = time.monotonic() + 4
        while self.inj._input:
            self.assertLess(time.monotonic(), until)
            self.inj.dispatch_pending()
            edges.extend(self.read_edges())
            time.sleep(self.inj.input_timeout(.005))
        return edges

    def paste_while_held(self, glyph, owned=False, shifted=False):
        if owned:
            codes = self.inj._find_spare()[:2]
            self.assertEqual(len(codes), 2)
            self.inj._spare = codes
        else:
            self.native_row(glyph)
        mods = xinject.MOD_ALT | (xinject.MOD_SHIFT if shifted else 0)
        self.assertTrue(self.inj.chord(glyph, mods, 1))
        code = self.inj._key_presses[glyph]
        sym = self.inj.keysym_for(glyph)
        initial = self.read_edges()
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        pane = dispatch.NativeInput.pane(self, reader)
        self.assertTrue(pane.on_paste(glyph + '界' + glyph))
        edges = self.drain()
        self.assertIn(code, self.held())
        self.assertEqual(self.inj._key_presses[glyph], code)
        self.assertEqual(self.xd.get_keyboard_mapping(code, 1)[0][0], sym)
        self.assertFalse(any(e[0] == X.KeyRelease and e[1] == code for e in edges))
        self.assertEqual([e[2] for e in edges if e[0] == X.KeyPress],
                         [sym, self.inj.keysym_for('界'), sym])
        self.assertTrue(all(e[3] & X.Mod1Mask for e in edges))
        self.assertTrue(all(e[1] != code for e in edges))
        print(json.dumps({'glyph': glyph, 'owned_scratch': owned, 'held_code': code,
                          'initial': initial, 'consumed_pairs': edges}), flush=True)
        return pane, code, sym

    def test_native_unicode_and_latin1_repeated_paste_preserve_real_release(self):
        for glyph in ('漢', 'ä'):
            with self.subTest(glyph=glyph):
                self.inj.release_all()
                self.inj = xinject.Injector(self.xd, 640, 480)
                self.addCleanup(self.inj.release_all)
                _, code, sym = self.paste_while_held(glyph)
                self.assertTrue(self.inj.chord(glyph, 0, 3))
                released = self.read_edges()
                self.assertEqual([e[:3] for e in released if e[1] == code], [(X.KeyRelease, code, sym)])
                self.assertEqual(self.held(), [])

    def test_owned_scratch_repeated_paste_keeps_owner_mapping_and_lru_grace(self):
        _, code, sym = self.paste_while_held('漢', owned=True)
        self.assertIn(code, self.inj._scratch.values())
        self.assertEqual(len(set(self.inj._scratch.values())), 2)
        self.assertTrue(self.inj.chord('漢', 0, 3))
        self.assertEqual([e[:3] for e in self.read_edges() if e[1] == code], [(X.KeyRelease, code, sym)])
        self.assertEqual(self.held(), [])

    def test_shift_and_alt_ownership_survives_paste_pair(self):
        _, code, sym = self.paste_while_held('漢', shifted=True)
        self.assertTrue(self.inj.chord('漢', 0, 3))
        self.assertEqual([e[:3] for e in self.read_edges() if e[1] == code], [(X.KeyRelease, code, sym)])
        self.assertEqual(self.held(), [])
        self.assertFalse(self.inj._mod_holds)

    def test_focus_cleanup_is_the_first_release_of_the_original_owner(self):
        pane, code, sym = self.paste_while_held('漢')
        pane.on_focus({'in': False})
        self.assertEqual([e[:3] for e in self.read_edges() if e[1] == code], [(X.KeyRelease, code, sym)])
        self.assertEqual(self.held(), [])
        self.assertFalse(self.inj._input)

    def test_disconnect_cleanup_is_the_first_release_of_the_original_owner(self):
        _, code, sym = self.paste_while_held('漢', owned=True)
        self.inj.release_all()  # Injector's actual client-disconnect cleanup
        self.assertEqual([e[:3] for e in self.read_edges() if e[1] == code], [(X.KeyRelease, code, sym)])
        self.assertEqual(self.held(), [])
        self.assertFalse(self.inj._mod_holds)

    def test_existing_unheld_duplicate_native_mapping_avoids_a_rebind(self):
        code, sym = self.native_row()
        alternate = self.inj._find_spare()[1]
        self.xd.change_keyboard_mapping(alternate, [(sym, sym)])
        self.xd.sync()
        self.xd._update_keymap(alternate, 1)
        self.inj.chord('漢', xinject.MOD_ALT, 1)
        held = self.inj._key_presses['漢']
        self.read_edges()
        self.assertTrue(self.inj.queue_input('paste', '漢'))
        edges = self.drain()
        self.assertIn(held, self.held())
        self.assertEqual([e[2] for e in edges if e[0] == X.KeyPress], [sym])
        self.assertTrue(all(e[1] != held for e in edges))
        self.assertFalse(self.inj._scratch)
        self.assertEqual({code, alternate}, {held, edges[0][1]})


def load_tests(loader, tests, pattern):
    return fixture.x11_sandbox.sandbox_load_tests(loader, tests, __name__)
