"""Initial scratch claims must preserve real native holds and release edges."""
import json
import socket

import private_input_fixture as fixture
import test_xinject_dispatch as dispatch
import xinject
from Xlib import X


class HeldNative(fixture.NativeInputFixture):
    def native_row(self, glyph='漢'):
        code = self.xd.display.info.max_keycode
        sym = self.inj.keysym_for(glyph)
        self.xd.change_keyboard_mapping(code, [(sym, sym)])
        self.xd.sync()
        self.xd._update_keymap(code, 1)
        self.app.sync()
        self.read_edges()
        self.assertEqual(self.inj._keycode_for(sym), (code, 0))
        self.assertEqual(self.inj._find_spare()[0], code)
        return code, sym

    def read_edges(self):
        self.xd.sync()
        self.app.sync()
        edges = []
        while self.app.pending_events():
            ev = self.app.next_event()
            if ev.type == X.MappingNotify:
                self.app.refresh_keyboard_mapping(ev)
            elif ev.type in (X.KeyPress, X.KeyRelease):
                sym = self.app.keycode_to_keysym(ev.detail, 1 if ev.state & X.ShiftMask else 0)
                edges.append((ev.type, ev.detail, sym, ev.state))
        return edges

    def hold_then_paste(self, glyph='漢', cached=False):
        code, sym = self.native_row(glyph)
        if cached:
            self.inj._spare = self.inj._find_spare()
        self.assertTrue(self.inj.chord(glyph, xinject.MOD_ALT, 1))
        initial = self.read_edges()
        self.assertTrue(any(e[:3] == (X.KeyPress, code, sym) for e in initial))
        self.assertIn(code, self.held())
        self.inj.queue_input('paste', '界')
        self.inj.dispatch_pending()
        edges = self.read_edges()
        self.assertEqual(self.xd.get_keyboard_mapping(code, 1)[0][0], sym)
        self.assertIn(code, self.held())
        self.assertFalse(any(e[0] == X.KeyRelease and e[1] == code for e in edges))
        pasted = [e for e in edges if e[0] == X.KeyPress]
        self.assertEqual([e[2] for e in pasted], [self.inj.keysym_for('界')])
        self.assertTrue(pasted[0][3] & X.Mod1Mask)
        return code, sym, initial, edges

    def test_initial_free_rows_preserve_native_unicode_and_latin1_until_real_release(self):
        for glyph in ('漢', 'ä'):
            with self.subTest(glyph=glyph):
                self.inj.release_all()
                self.inj = xinject.Injector(self.xd, 640, 480)
                self.addCleanup(self.inj.release_all)
                code, sym, initial, paste = self.hold_then_paste(glyph)
                self.assertTrue(self.inj.chord(glyph, 0, 3))
                release = self.read_edges()
                self.assertEqual([e[:3] for e in release if e[1] == code],
                                 [(X.KeyRelease, code, sym)])
                self.assertEqual(self.held(), [])
                self.assertFalse(self.inj._mod_holds)
                print(json.dumps({'glyph': glyph, 'native_code': code, 'initial': initial,
                                  'paste': paste, 'real_release': release}), flush=True)

    def test_cached_initial_free_list_rechecks_current_native_holds(self):
        code, sym, _, _ = self.hold_then_paste(cached=True)
        self.assertNotIn(code, self.inj._scratch.values())
        self.assertEqual(self.xd.get_keyboard_mapping(code, 1)[0][0], sym)

    def test_all_available_rows_held_refuses_binding_without_remap_or_release(self):
        code, sym = self.native_row()
        self.inj._spare = [code]
        self.inj.chord('漢', xinject.MOD_ALT, 1)
        self.read_edges()
        self.assertEqual(self.inj._bind_scratch(self.inj.keysym_for('界')), 0)
        self.assertEqual(self.read_edges(), [])
        self.assertIn(code, self.held())
        self.assertEqual(self.xd.get_keyboard_mapping(code, 1)[0][0], sym)
        self.inj.release_all()
        self.read_edges()
        self.assertEqual(self.held(), [])
        self.assertEqual(self.inj._bind_scratch(self.inj.keysym_for('界')), code)

    def test_focus_out_legitimately_releases_held_native_glyph(self):
        code, sym, _, _ = self.hold_then_paste()
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        pane = dispatch.NativeInput.pane(self, reader)
        pane.on_focus({'in': False})
        self.assertEqual([e[:3] for e in self.read_edges() if e[1] == code],
                         [(X.KeyRelease, code, sym)])
        self.assertEqual(self.held(), [])
        self.assertFalse(self.inj._input)

    def test_actual_pane_close_legitimately_releases_held_native_glyph(self):
        code, sym, _, _ = self.hold_then_paste()
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        pane = dispatch.NativeInput.pane(self, reader)
        pane.tick_capture = lambda *a: None
        writer.send(json.dumps([{'kind': 'key', 'key': 'q', 'mods': 5, 'event': 1}]).encode())
        pane.run()
        self.assertEqual([e[:3] for e in self.read_edges() if e[1] == code],
                         [(X.KeyRelease, code, sym)])
        self.assertEqual(self.held(), [])
        self.assertFalse(self.inj._input)


def load_tests(loader, tests, pattern):
    return fixture.x11_sandbox.sandbox_load_tests(loader, tests, __name__)
