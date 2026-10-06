"""kilix — X11 input injection (keyboard/mouse) via XTest.

Factored out of apprun.AppPane so it can be reused by:
  - `kilix run` (Phase 2): inject the local pane's kitty-kbd + SGR-pixel events
    into the app's private X server (Xvfb or, in --serve mode, Xvnc).
  - `kilix share` (Phase 3, share.py): inject a remote viewer's events
    into the headless Xvfb the whole kilix runs on.

Injects only into the private display it is handed — never the real one. Tracks
which keys/buttons are currently held and can release them all on disconnect, so
a viewer that drops mid-drag or mid-keypress never leaves a stuck modifier or
button down on the shared display.
"""
from collections import OrderedDict
import time

from Xlib import X, XK
from Xlib.ext import xtest

# X11 maps any Unicode code point above Latin-1 to keysym 0x01000000 | cp.
UNICODE_KEYSYM = 0x01000000
# Spare keycodes kept bound to keysyms the private keymap lacks (a default
# Xvfb keymap has 19 unused keycodes).
SCRATCH_KEYCODES = 32
# Before rebinding a spare keycode, give clients time to read the key events
# that used its previous keysym: they resolve keycodes with their current map.
REBIND_DELAY = .03

# kitty functional keycodes for modifier keys -> X keysym names
MOD_KEYSYMS = {57441: "Shift_L", 57442: "Control_L", 57443: "Alt_L",
               57444: "Super_L", 57447: "Shift_R", 57448: "Control_R",
               57449: "Alt_R", 57450: "Super_R"}

NAME_KEYSYMS = {"Enter": "Return", "Escape": "Escape",
                "Backspace": "BackSpace", "Tab": "Tab",
                "ArrowUp": "Up", "ArrowDown": "Down",
                "ArrowLeft": "Left", "ArrowRight": "Right",
                "Home": "Home", "End": "End", "PageUp": "Prior",
                "PageDown": "Next", "Insert": "Insert", "Delete": "Delete",
                **{f"F{i}": f"F{i}" for i in range(1, 13)}}

# Control characters a paste can carry that have a key of their own.
PASTE_KEYS = {"\n": "Enter", "\t": "Tab"}

# kitty keyboard-protocol modifier bits, after the protocol's +1 offset is
# removed (apprun does that before calling in). Order is the press order.
MOD_SHIFT, MOD_ALT, MOD_CTRL, MOD_SUPER = 1, 2, 4, 8
MOD_BITS = ((MOD_SHIFT, "Shift_L"), (MOD_ALT, "Alt_L"),
            (MOD_CTRL, "Control_L"), (MOD_SUPER, "Super_L"))
# The keyboard protocol also reports the lock states in the modifier field
# (CapsLock 64, NumLock 128). They are not chord modifiers: with one set, a
# Shift-only key would no longer count as Shift-only.
MOD_LOCKS = 64 | 128
MOD_KEY_BITS = {57441: MOD_SHIFT, 57442: MOD_CTRL, 57443: MOD_ALT,
                57444: MOD_SUPER, 57447: MOD_SHIFT, 57448: MOD_CTRL,
                57449: MOD_ALT, 57450: MOD_SUPER}


class Injector:
    def __init__(self, xd, app_w, app_h):
        self.xd = xd
        self.app_w, self.app_h = app_w, app_h
        self._keys_down = set()      # keycodes currently pressed
        self._btns_down = set()      # X button numbers currently pressed
        self._mod_holds = {}         # modifier keycode -> keyboard/mouse owners
        self._chord_mods = {}        # key keycode -> modifier keycodes pressed for it
        self._mouse_mods = []        # one modifier owner for the pointer gesture
        self._mouse_mask = 0
        self._key_presses = {}       # pane key identity -> keycode pressed for it
        self._scratch = OrderedDict()  # keysym -> spare keycode bound to it (LRU)
        self._spare = None           # spare keycodes of the private keymap

    # The private Xvfb starts with its default (US) keymap and never learns the
    # physical layout. A German pane sends a, o, u umlauts and sharp s as their
    # own code points; a Cyrillic or Greek one sends letters above Latin-1.
    # Looking those keysyms up in the private keymap found no keycode and the
    # key was silently dropped, and a shifted glyph was injected as Shift plus
    # the base key, which is the US glyph (Shift+7 gave '&' instead of '/').
    # Each character is injected as the keysym it names: an existing keycode
    # at the level that produces it, or a spare keycode bound to it, the same
    # technique xdotool uses.
    def _find_spare(self):
        try:
            first = self.xd.display.info.min_keycode
            last = self.xd.display.info.max_keycode
            rows = self.xd.get_keyboard_mapping(first, last - first + 1)
        except Exception:
            return []
        def reusable(row):
            # Unused, or a scratch binding an earlier injector left behind: one
            # keysym repeated that a default US keymap never contains.
            syms = {sym for sym in row if sym}
            if not syms:
                return True
            sym = syms.pop() if len(syms) == 1 else 0
            return 0xa0 <= sym <= 0xff or sym >= UNICODE_KEYSYM
        spare = [first + i for i, row in enumerate(rows) if reusable(row)]
        return spare[::-1][:SCRATCH_KEYCODES]

    def _bind_scratch(self, keysym):
        if keysym in self._scratch:
            self._scratch.move_to_end(keysym)
            return self._scratch[keysym]
        if self._spare is None:
            self._spare = self._find_spare()
        used = set(self._scratch.values())
        free = [code for code in self._spare if code not in used]
        if free:
            code = free[0]
        else:
            # Rebind the least recently used spare that is not held down.
            for old, code in self._scratch.items():
                if code not in self._keys_down:
                    del self._scratch[old]
                    break
            else:
                return 0
            self.xd.sync()
            time.sleep(REBIND_DELAY)
        try:
            self.xd.change_keyboard_mapping(code, [(keysym, keysym)])
            self.xd.sync()
        except Exception:
            return 0
        # This connection never reads its MappingNotify; refresh the cached
        # keymap so later lookups do not see the keycode's old keysym.
        update = getattr(self.xd, "_update_keymap", None)
        if update is not None:
            try:
                update(code, 1)
            except Exception:
                pass
        self._scratch[keysym] = code
        return code

    def _keycode_for(self, keysym):
        """(keycode, level) that produces *keysym*; level 1 needs Shift."""
        if keysym in self._scratch:
            return self._bind_scratch(keysym), 0
        code = self.xd.keysym_to_keycode(keysym)
        if code:
            lookup = getattr(self.xd, "keycode_to_keysym", None)
            if lookup is None:
                return code, 0
            try:
                for level in (0, 1):
                    if lookup(code, level) == keysym:
                        return code, level
            except Exception:
                return code, 0
            # Only reachable through a group or level-3 shift: bind it plainly.
        return self._bind_scratch(keysym), 0

    @staticmethod
    def _is_glyph(key):
        return len(key) == 1 and not 57344 <= ord(key) <= 63743 and ord(key) >= 32

    def _modifier_codes(self, mods):
        codes = []
        for bit, name in MOD_BITS:
            if mods & bit:
                code = self.xd.keysym_to_keycode(XK.string_to_keysym(name))
                if code:
                    codes.append(code)
        return codes

    def _hold_modifiers(self, codes):
        for code in codes:
            if self._mod_holds.get(code, 0) == 0:
                xtest.fake_input(self.xd, X.KeyPress, code)
                self._keys_down.add(code)
            self._mod_holds[code] = self._mod_holds.get(code, 0) + 1

    def _release_modifiers(self, codes):
        for code in reversed(codes):
            held = self._mod_holds.get(code, 0)
            if held <= 1:
                self._mod_holds.pop(code, None)
                if code in self._keys_down:
                    xtest.fake_input(self.xd, X.KeyRelease, code)
                    self._keys_down.discard(code)
            else:
                self._mod_holds[code] = held - 1

    def _set_mouse_modifiers(self, mods):
        codes = self._modifier_codes(mods)
        self._release_modifiers([c for c in self._mouse_mods if c not in codes])
        self._hold_modifiers([c for c in codes if c not in self._mouse_mods])
        self._mouse_mods = codes
        self._mouse_mask = mods

    def is_modifier(self, key):
        """True for a bare modifier key event (Shift/Ctrl/Alt/Super alone)."""
        return len(key) == 1 and ord(key) in MOD_KEYSYMS

    def chord(self, key, mods, etype, shifted=None):
        """Inject *key* with the *mods* bitmask held only for this event.

        The pane's modifier keys are never injected without a gesture. A bare Alt
        press forwarded into the private display, whose matching release then
        went to a different pane -- because the chord that followed it was a
        kitty binding that moved focus -- left Mod1 latched in the X server,
        and every later key reached the app as an Alt chord. Modifiers are
        pressed around the key that needs them and released with it, so there
        is no press that can outlive its release. Overlapping chords share a
        modifier by count, so releasing one key does not drop a modifier that
        another held key still needs.

        *shifted* is the glyph the pane's own layout produced with Shift (the
        kitty protocol's alternate key); a Shift-only chord injects that glyph.

        etype: 1 = press, 3 = release. Returns True if a key was injected.
        """
        if etype not in (1, 3):
            return False
        mods &= ~MOD_LOCKS
        if self.is_modifier(key):
            # A drag can change between copy/move/selection modes while the
            # pointer is stationary. Bare modifiers may update that existing
            # gesture, but may never start a hold that focus loss can strand.
            if self._btns_down and etype in (1, 3):
                bit = MOD_KEY_BITS[ord(key)]
                mask = self._mouse_mask | bit if etype == 1 else self._mouse_mask & ~bit
                self._set_mouse_modifiers(mask)
                self.xd.flush()
            return False
        if etype == 1:
            # Repeated/duplicate presses do not acquire another owner. Xvfb
            # autorepeats held keys; overwriting this key's first ownership
            # would strand a modifier when its single release arrives.
            if key in self._key_presses:
                return False
            symbol = key
            only_shift = not mods & ~MOD_SHIFT
            if only_shift and mods & MOD_SHIFT and shifted:
                symbol = shifted
            keysym = self.keysym_for(symbol)
            if not keysym:
                return False
            keycode, level = self._keycode_for(keysym)
            if not keycode:
                return False
            if only_shift and self._is_glyph(symbol) and (shifted or not mods):
                # Produce exactly this glyph: Shift only where the private
                # keymap needs it, whatever the pane's layout needed.
                mods = MOD_SHIFT if level == 1 else 0
            if keycode in self._chord_mods:
                return False
            modcodes = self._modifier_codes(mods)
            self._hold_modifiers(modcodes)
            xtest.fake_input(self.xd, X.KeyPress, keycode)
            self._keys_down.add(keycode)
            self._chord_mods[keycode] = modcodes
            self._key_presses[key] = keycode
        else:
            # Release the keycode and the modifiers this key was PRESSED with,
            # not the ones the release event reports. An operator who lets go
            # of Alt (or Shift) before the key produces a release with mods=0;
            # computing from that would leave the Alt pressed at key-down held
            # for ever -- the same latch this method exists to remove.
            keycode = self._key_presses.pop(key, None)
            if keycode is None:
                keysym = self.keysym_for(key)
                keycode = self.xd.keysym_to_keycode(keysym) if keysym else 0
                if not keycode:
                    return False
            modcodes = self._chord_mods.pop(keycode, [])
            xtest.fake_input(self.xd, X.KeyRelease, keycode)
            self._keys_down.discard(keycode)
            self._release_modifiers(modcodes)
        self.xd.flush()
        return True

    def keysym_for(self, key):
        if len(key) == 1:
            o = ord(key)
            if o in MOD_KEYSYMS:
                return XK.string_to_keysym(MOD_KEYSYMS[o])
            if 57344 <= o <= 63743:      # other functional keys: unmapped
                return 0
            if o < 0x20 or 0x7f <= o <= 0x9f:
                return 0                 # control characters are not keys
            if o < 256:                  # latin-1 keysyms == codepoints
                return o
            if o > 0x10FFFF or 0xD800 <= o <= 0xDFFF:
                return 0
            return UNICODE_KEYSYM | o
        name = NAME_KEYSYMS.get(key)
        return XK.string_to_keysym(name) if name else 0

    def key(self, key, etype):
        """etype: 1 = press, 3 = release. Returns True if a key was injected."""
        keysym = self.keysym_for(key)
        if not keysym:
            return False
        keycode, _level = self._keycode_for(keysym)
        if not keycode:
            return False
        if etype == 1:
            xtest.fake_input(self.xd, X.KeyPress, keycode)
            self._keys_down.add(keycode)
        else:
            xtest.fake_input(self.xd, X.KeyRelease, keycode)
            self._keys_down.discard(keycode)
        self.xd.flush()
        return True

    def key_named(self, xname, etype):
        """Press/release by X keysym NAME (e.g. 'Return', 'Control_L', 'Up').
        Used by kilix share to inject a browser viewer's named keys."""
        keysym = XK.string_to_keysym(xname)
        if not keysym:
            return False
        keycode = self.xd.keysym_to_keycode(keysym)
        if not keycode:
            return False
        if etype == 1:
            xtest.fake_input(self.xd, X.KeyPress, keycode)
            self._keys_down.add(keycode)
        else:
            xtest.fake_input(self.xd, X.KeyRelease, keycode)
            self._keys_down.discard(keycode)
        self.xd.flush()
        return True

    def move_click(self, x, y, button=0, press=None):
        """Absolute pointer move, and optional button/wheel, in display pixels.
        Used by kilix share (whole-screen coords, no letterbox mapping)."""
        x = max(0, min(self.app_w - 1, int(x)))
        y = max(0, min(self.app_h - 1, int(y)))
        xtest.fake_input(self.xd, X.MotionNotify, x=x, y=y)
        # A wheel notch arrives with no press flag and is one click; anything
        # carrying a flag is a real button being held, whatever its number.
        # Keyed on the number alone, a viewer's back/forward button -- which
        # kilix share's client reports as 4 and 5 -- was injected as a wheel
        # notch on the press AND again on the release, and never tracked, so
        # release_all could not free it.
        if button in (4, 5) and press is None:
            xtest.fake_input(self.xd, X.ButtonPress, button)
            xtest.fake_input(self.xd, X.ButtonRelease, button)
        elif button and press is not None:
            if press:
                xtest.fake_input(self.xd, X.ButtonPress, button)
                self._btns_down.add(button)
            else:
                xtest.fake_input(self.xd, X.ButtonRelease, button)
                self._btns_down.discard(button)
        self.xd.flush()

    def paste(self, text):
        """Type *text*: each character as its own keysym, Shift where needed."""
        shift = self._modifier_codes(MOD_SHIFT)
        for ch in text:
            keysym = self.keysym_for(PASTE_KEYS.get(ch, ch))
            keycode, level = self._keycode_for(keysym) if keysym else (0, 0)
            if not keycode:
                continue
            if level == 1:
                self._hold_modifiers(shift)
            xtest.fake_input(self.xd, X.KeyPress, keycode)
            xtest.fake_input(self.xd, X.KeyRelease, keycode)
            if level == 1:
                self._release_modifiers(shift)
        self.xd.flush()

    def mouse(self, ev, box):
        """Map a pane-pixel mouse event through `box` (x,y,w,h — the on-screen
        image rect) into app pixels, then inject motion/buttons/wheel.

        SGR carries its own Shift/Alt/Ctrl bits. Keep them while a button is
        held, update them on each event, and release this pointer's modifier
        ownership when the gesture ends. Hover and wheel holds last one event.
        Keyboard chords retain their own independent modifier ownership.
        """
        bx, by, bw, bh = box
        ax = min(self.app_w - 1, max(0, round((ev["x"] - bx) * self.app_w / bw)))
        ay = min(self.app_h - 1, max(0, round((ev["y"] - by) * self.app_h / bh)))
        b = ev["b"]
        if b & 256:                     # kitty's leave indicator, not a click
            return
        mods = ((MOD_SHIFT if b & 4 else 0) | (MOD_ALT if b & 8 else 0)
                | (MOD_CTRL if b & 16 else 0))
        self._set_mouse_modifiers(mods)
        if b & 64:                       # scroll -> X buttons 4-7
            # (b & 3) + 4, not "4 or 5": the engine encodes a scroll button as
            # (button - 4) | 64, so 66/67 are the HORIZONTAL wheel (X 6/7).
            # Collapsing them to 5 scrolled the app down for both directions.
            btn = (b & 3) + 4
            xtest.fake_input(self.xd, X.MotionNotify, x=ax, y=ay)
            xtest.fake_input(self.xd, X.ButtonPress, btn)
            xtest.fake_input(self.xd, X.ButtonRelease, btn)
        elif b & 32:                     # motion (with or without drag)
            xtest.fake_input(self.xd, X.MotionNotify, x=ax, y=ay)
        else:
            # 0/1/2 -> left/middle/right, and bit 7 -> the side buttons the
            # engine encodes as (button - 8) | 128, i.e. X buttons 8-11.
            # Without that arm a side button decoded as (b & 3) + 1: a thumb
            # click became a left/middle/RIGHT click, and pressed mid-drag its
            # release ended the real drag of the button it aliased onto and
            # dropped the modifier that gesture still owned.
            btn = (b & 3) + (8 if b & 128 else 1)
            xtest.fake_input(self.xd, X.MotionNotify, x=ax, y=ay)
            if ev["press"]:
                xtest.fake_input(self.xd, X.ButtonPress, btn)
                self._btns_down.add(btn)
            else:
                xtest.fake_input(self.xd, X.ButtonRelease, btn)
                self._btns_down.discard(btn)
        if not self._btns_down:
            self._set_mouse_modifiers(0)
        self.xd.flush()

    def release_all(self):
        """Release every key/button we still hold — call on client disconnect,
        on pane focus-out, and on every exit path
        or shutdown so nothing stays stuck down on the shared display."""
        for btn in list(self._btns_down):
            try:
                xtest.fake_input(self.xd, X.ButtonRelease, btn)
            except Exception:
                pass
        for keycode in list(self._keys_down):
            try:
                xtest.fake_input(self.xd, X.KeyRelease, keycode)
            except Exception:
                pass
        self._keys_down.clear()
        self._btns_down.clear()
        try:
            self.xd.flush()
        except Exception:
            pass
        self._mod_holds.clear()
        self._chord_mods.clear()
        self._key_presses.clear()
        self._mouse_mods.clear()
        self._mouse_mask = 0
