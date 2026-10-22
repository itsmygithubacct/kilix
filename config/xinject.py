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
from collections import OrderedDict, deque
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
# Bound work between returns to the pane's input/capture event loop.
INPUT_BATCH = 16
INPUT_SLICE = .004
# Admission is separate from dispatch. Keep complete accepted pastes, with a
# finite retained UTF-8 payload budget, and reserve a separate bounded slot for
# each accepted gesture's release. A full paste queue cannot strand a key.
INPUT_ITEMS = 256
INPUT_BYTES = 1024 * 1024
INPUT_RELEASES = 256
INPUT_GESTURE_BYTES = 128


class _RebindPending(Exception):
    """A queued input must yield until its old keycode has had its grace period."""

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
        # keysym -> code, with (keysym, code) for an alternate binding while
        # the original is held. Every owned row remains in the same LRU.
        self._scratch = OrderedDict()
        self._spare = None           # spare keycodes of the private keymap
        self._input = deque()        # FIFO: pastes and subsequent gestures
        # Item identities whose presses collided with an owner before its
        # restoring release passed them. Keep that verdict until FIFO dispatch;
        # later items after the release belong to a new generation.
        self._superseded_presses = {}  # item id -> original alias row, or duplicate
        self._input_bytes = 0        # retained UTF-8 text + fixed gesture charges
        self._work_items = 0
        self._work_bytes = 0
        self._release_reservations = set()  # admitted presses awaiting release
        self._queued_releases = 0
        self._release_pending = {}   # identity -> release edges already in FIFO
        self.last_input_refusal = None
        self._input_capacity_pending = False
        self._released_codes = set()  # rows with release events not yet fenced for a rebind
        self._rebind_safe_codes = set()  # release rows covered by one completed grace barrier
        self._rebind_grace_codes = set()  # rows included in the outstanding barrier
        self._defer_rebind = False
        self._rebind_pending = None

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
            cp = sym - UNICODE_KEYSYM
            return (0xa0 <= sym <= 0xff or
                    0x100 <= cp <= 0x10ffff and not 0xd800 <= cp <= 0xdfff)
        spare = [first + i for i, row in enumerate(rows) if reusable(row)]
        return spare[::-1][:SCRATCH_KEYCODES]

    def _record_released_code(self, code):
        self._released_codes.add(code)
        self._rebind_safe_codes.discard(code)
        self._rebind_grace_codes.discard(code)

    def _bind_scratch(self, keysym, unheld=False):
        for binding, code in self._scratch.items():
            symbol = binding[0] if isinstance(binding, tuple) else binding
            if symbol == keysym and (not unheld or code not in self._keys_down):
                self._scratch.move_to_end(binding)
                return code
        if self._spare is None:
            self._spare = self._find_spare()
        used = set(self._scratch.values())
        # A reusable pre-existing Unicode/Latin-1 row can still be a native
        # key held by this injector. Recheck holds even for a cached free list.
        free = [code for code in self._spare
                if code not in used and code not in self._keys_down]
        if free:
            code = free[0]
        else:
            # Rebind the least recently used spare that is not held down.
            for old, code in self._scratch.items():
                if code not in self._keys_down:
                    break
            else:
                return 0
        needs_grace = (not free or code in self._released_codes) and code not in self._rebind_safe_codes
        if needs_grace:
            if self._defer_rebind:
                pending = self._rebind_pending
                if pending is None or pending[:2] != (keysym, code):
                    # Flush and fence the old events before starting the
                    # existing client keymap-consumption grace period. Keep
                    # the old binding until the FIFO retries this same input.
                    # Every row released before this fence shares the same
                    # 30 ms grace window; waiting once per LRU row serialized
                    # the same client-consumption interval and multiplied
                    # multilingual paste latency by the number of spares.
                    self.xd.sync()
                    self._rebind_grace_codes = set(self._released_codes)
                    self._rebind_grace_codes.add(code)
                    self._rebind_pending = (keysym, code, time.monotonic() + REBIND_DELAY)
                    raise _RebindPending
                if time.monotonic() < pending[2]:
                    raise _RebindPending
                self._released_codes.difference_update(self._rebind_grace_codes)
                self._rebind_safe_codes.update(self._rebind_grace_codes)
                self._rebind_grace_codes.clear()
                self._rebind_pending = None
            else:
                self.xd.sync()
                time.sleep(REBIND_DELAY)
                grace_codes = set(self._released_codes)
                grace_codes.add(code)
                self._released_codes.difference_update(grace_codes)
                self._rebind_safe_codes.update(grace_codes)
        self._released_codes.discard(code)
        if not free:
            # A row covered by an earlier shared barrier needs no new wait,
            # but its former LRU symbol must still leave the cache.
            del self._scratch[old]
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
        binding = keysym if keysym not in self._scratch else (keysym, code)
        self._scratch[binding] = code
        return code

    def _native_keycode_for(self, keysym, unheld=False):
        """Look up existing rows without binding or changing their ownership."""
        code = self.xd.keysym_to_keycode(keysym)
        lookup = getattr(self.xd, 'keycode_to_keysym', None)
        if lookup is None:
            return (code, 0) if not unheld or code not in self._keys_down else (0, 0)
        if code and (not unheld or code not in self._keys_down):
            try:
                for level in (0, 1):
                    if lookup(code, level) == keysym:
                        return code, level
            except Exception:
                return (0, 0) if unheld else (code, 0)
        if unheld and code:
            # The preferred row may be held while another native row already
            # produces this glyph. Consult the cached keymap before rebinding.
            first = self.xd.display.info.min_keycode
            last = self.xd.display.info.max_keycode
            for other in range(first, last + 1):
                if other not in self._keys_down:
                    for level in (0, 1):
                        if lookup(other, level) == keysym:
                            return other, level
        return 0, 0

    def _keycode_for(self, keysym, unheld=False):
        """(code, level); paste pairs require a row with no existing owner."""
        for binding, code in self._scratch.items():
            symbol = binding[0] if isinstance(binding, tuple) else binding
            if symbol == keysym and (not unheld or code not in self._keys_down):
                return self._bind_scratch(keysym, unheld), 0
        code, level = self._native_keycode_for(keysym, unheld)
        if code:
            return code, level
        # Missing or only reachable through a group/level-3 shift, or all
        # matching native/owned rows held: bind a separate safe row plainly.
        return self._bind_scratch(keysym, unheld), 0

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
            self._record_released_code(keycode)
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
            self._record_released_code(keycode)
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

    def _paste_key(self, ch):
        shift = self._modifier_codes(MOD_SHIFT)
        keysym = self.keysym_for(PASTE_KEYS.get(ch, ch))
        keycode, level = self._keycode_for(keysym, unheld=True) if keysym else (0, 0)
        if not keycode:
            return not keysym  # intentionally ignored controls, never a lost valid glyph
        if level == 1:
            self._hold_modifiers(shift)
        xtest.fake_input(self.xd, X.KeyPress, keycode)
        xtest.fake_input(self.xd, X.KeyRelease, keycode)
        self._record_released_code(keycode)
        if level == 1:
            self._release_modifiers(shift)
        return True

    def _paste_has_capacity(self, text):
        """Whole-item preflight, without X input, remapping or sleeping.

        One unheld reusable row can deliver any supported glyph sequentially.
        With no such row, every distinct glyph must have an unheld existing
        mapping. Cached lookups and a bounded set avoid per-character X calls.
        """
        if self._spare is None:
            self._spare = self._find_spare()
        if any(code not in self._keys_down for code in self._spare):
            return True
        seen = set()
        for ch in text:
            if ch in seen:
                continue
            seen.add(ch)
            keysym = self.keysym_for(PASTE_KEYS.get(ch, ch))
            if keysym and not self._native_keycode_for(keysym, unheld=True)[0]:
                return False
        return True

    def paste(self, text):
        """Synchronous typing for callers without an event loop."""
        if not self._paste_has_capacity(text):
            return self._refuse_input('key capacity unavailable')
        for ch in text:
            if not self._paste_key(ch):
                self.xd.flush()
                return self._refuse_input('key capacity unavailable')
        self.xd.flush()
        return True

    def queue_input(self, kind, *args):
        """Admit a whole item, returning bool; False sets last_input_refusal.

        Up to INPUT_ITEMS work items / INPUT_BYTES retained payload bytes plus
        INPUT_RELEASES release slots / INPUT_GESTURE_BYTES per slot. Matching
        releases consume their reserved slots even when work admission is full.
        Accepted text/work stays FIFO. Only an actual held owner's queued
        release may pass a capacity-blocked paste to restore its delivery.
        Refused items have no injected prefix and are never retained for
        automatic retry. Focus/close cancels all.
        """
        if kind not in ('paste', 'chord', 'mouse'):
            raise ValueError('unknown input kind')
        self.last_input_refusal = None
        identity = None
        release = False
        if kind == 'paste':
            if len(args) != 1 or not isinstance(args[0], str):
                return self._refuse_input('invalid input')
            payload = args[0]
            if not payload:
                return True
            # Do not encode a caller's arbitrarily large string just to refuse
            # it. With this length check the temporary encoding is <= 4 MiB.
            if len(payload) > INPUT_BYTES:
                return self._refuse_input('item too large')
            try:
                cost = len(payload.encode('utf-8'))
            except UnicodeError:
                return self._refuse_input('invalid Unicode')
            if cost > INPUT_BYTES:
                return self._refuse_input('item too large')
        elif kind == 'chord':
            if len(args) not in (3, 4):
                return self._refuse_input('invalid input')
            key, mods, etype = args[:3]
            shifted = args[3] if len(args) == 4 else None
            if (not isinstance(key, str) or not (len(key) == 1 or key in NAME_KEYSYMS)
                    or type(mods) is not int or etype not in (1, 3)
                    or (shifted is not None and
                        (not isinstance(shifted, str) or len(shifted) != 1))):
                return self._refuse_input('invalid input')
            if any(0xd800 <= ord(c) <= 0xdfff for c in key + (shifted or '')):
                return self._refuse_input('invalid Unicode')
            mods &= MOD_SHIFT | MOD_ALT | MOD_CTRL | MOD_SUPER | MOD_LOCKS
            payload = (key, mods, etype, shifted)
            identity, release = ('key', key), etype == 3
            cost = INPUT_GESTURE_BYTES
        else:
            # Snapshot only bounded protocol fields; do not retain arbitrary
            # caller dictionaries, extra strings or giant integer objects.
            if len(args) != 2:
                return self._refuse_input('invalid input')
            ev, box = args
            try:
                b, x, y, press = ev['b'], ev['x'], ev['y'], ev['press']
                if (any(type(n) is not int or abs(n) > 0x7fffffff for n in (b, x, y))
                        or not 0 <= b <= 511 or type(press) is not bool
                        or len(box) != 4
                        or any(type(n) not in (int, float) or not -0x7fffffff <= n <= 0x7fffffff
                               for n in box) or box[2] <= 0 or box[3] <= 0):
                    return self._refuse_input('invalid input')
            except (KeyError, TypeError):
                return self._refuse_input('invalid input')
            payload = ({'b': b, 'x': x, 'y': y, 'press': press}, tuple(box))
            if not b & (32 | 64 | 256):
                identity = ('button', (b & 3) + (8 if b & 128 else 1))
                release = not press
            cost = INPUT_GESTURE_BYTES
        # Direct/native holds may predate queued input. Give them the same
        # release reservation before admitting work behind them.
        live = {('key', key) for key in self._key_presses}
        live.update(('button', btn) for btn in self._btns_down)
        self._release_reservations.update(live - self._release_pending.keys())
        if release:
            if identity not in self._release_reservations:
                return self._refuse_input('no accepted press')
            if self._queued_releases >= INPUT_RELEASES:
                return self._refuse_input('release capacity full')
            self._release_reservations.remove(identity)
            self._queued_releases += 1
            self._release_pending[identity] = self._release_pending.get(identity, 0) + 1
        else:
            if self._work_items >= INPUT_ITEMS or self._work_bytes + cost > INPUT_BYTES:
                return self._refuse_input('queue full')
            if kind == 'paste' and not self._paste_has_capacity(payload):
                return self._refuse_input('key capacity unavailable')
            if (identity is not None and identity not in self._release_reservations
                    and len(self._release_reservations) + self._queued_releases >= INPUT_RELEASES):
                return self._refuse_input('release capacity full')
            if identity is not None:
                self._release_reservations.add(identity)
            self._work_items += 1
            self._work_bytes += cost
        self._input.append([kind, payload, 0, cost, identity if release else None])
        self._input_bytes += cost
        return True

    def _refuse_input(self, reason):
        self.last_input_refusal = reason
        return False

    @property
    def queued_input_bytes(self):
        """Retained payload charge, including the full partially typed string."""
        return self._input_bytes

    @property
    def input_capacity_pending(self):
        return self._input_capacity_pending

    def _finish_input(self, item=None):
        if item is None:
            item = self._input.popleft()
        else:
            self._input.remove(item)
        self._superseded_presses.pop(id(item), None)
        cost, release = item[3:]
        self._input_bytes -= cost
        if release is None:
            self._work_items -= 1
            self._work_bytes -= cost
        else:
            self._queued_releases -= 1
            count = self._release_pending[release] - 1
            if count:
                self._release_pending[release] = count
            else:
                del self._release_pending[release]

    def _capacity_release(self, keysym):
        """Only a real held owner's queued release may unblock a paste.

        Text/work stays ordered. This narrow release priority prevents the
        last-row owner's real release being stranded behind accepted text.
        """
        for item in self._input:
            identity = item[4]
            if identity is None or identity[0] != 'key':
                continue
            code = self._key_presses.get(identity[1])
            if code is None or code not in self._keys_down:
                continue
            if code in self._spare or any(self.xd.keycode_to_keysym(code, level) == keysym for level in (0, 1)):
                return item
        return None

    def _preserve_press_owners(self, release):
        """Latch duplicate/alias verdicts only within the overtaken FIFO span.

        Use the same cached row preference and shifted symbol as chord, without
        allocating a row, remapping or injecting any intervening work. A press
        after this release is untouched and can establish its own real owner.
        At most INPUT_ITEMS item identities are retained, under their existing
        work charges; completion and cancellation discard them.
        """
        key = release[4][1]
        code = self._key_presses.get(key)
        if code is None:
            return
        scratch = self._scratch.copy()
        for item in self._input:
            if item is release:
                break
            if id(item) in self._superseded_presses:
                alias = self._superseded_presses[id(item)]
                if alias is not None:
                    symbol, original = alias
                    for binding, value in scratch.items():
                        mapped_symbol = binding[0] if isinstance(binding, tuple) else binding
                        if mapped_symbol == symbol and value == original:
                            scratch.move_to_end(binding)
                            break
                continue
            if item[0] != 'chord' or item[1][2] != 1:
                continue
            prior, mods, _etype, *rest = item[1]
            if self.is_modifier(prior):
                continue
            if prior == key:
                self._superseded_presses[id(item)] = None
                continue
            if prior in self._key_presses:
                continue
            mods &= ~MOD_LOCKS
            shifted = rest[0] if rest else None
            symbol = shifted if not mods & ~MOD_SHIFT and mods & MOD_SHIFT and shifted else prior
            keysym = self.keysym_for(symbol)
            if not keysym:
                continue
            owned = next(((binding, value) for binding, value in scratch.items()
                          if (binding[0] if isinstance(binding, tuple) else binding) == keysym), None)
            if owned is not None:
                # Even a rejected alias lookup moves its row to the LRU end.
                # The next alias may therefore refer to a different owner.
                scratch.move_to_end(owned[0])
                mapped = owned[1]
            else:
                mapped = self._native_keycode_for(keysym)[0]
            if mapped == code:
                self._superseded_presses[id(item)] = (keysym, code)

    def _retired_press(self, item):
        if id(item) not in self._superseded_presses:
            return False
        alias = self._superseded_presses[id(item)]
        if alias is not None:
            keysym, code = alias
            # Preserve the original cache lookup at this item's FIFO turn if
            # that binding survived the paste. Never recreate or remap it.
            for binding, value in self._scratch.items():
                symbol = binding[0] if isinstance(binding, tuple) else binding
                if symbol == keysym and value == code:
                    self._scratch.move_to_end(binding)
                    break
        return True

    def input_timeout(self, idle=.25):
        """Select timeout: run queued work promptly without spinning on a guard."""
        if not self._input:
            return idle
        if self._input_capacity_pending:
            return min(idle, REBIND_DELAY)
        if self._rebind_pending is not None:
            return min(idle, max(0, self._rebind_pending[2] - time.monotonic()))
        return 0

    def dispatch_pending(self):
        """One bounded slice, with no sleep and no overlapping scratch remaps."""
        deadline = time.monotonic() + INPUT_SLICE
        self._defer_rebind = True
        try:
            for _ in range(INPUT_BATCH):
                if not self._input:
                    break
                item = self._input[0]
                try:
                    if item[0] == 'paste':
                        ch = item[1][item[2]]
                        if not self._paste_key(ch):
                            # Capacity can change after admission. Keep the
                            # entire string/charge and retry this same glyph;
                            # never advance past an un-emitted valid character.
                            self._input_capacity_pending = True
                            self._rebind_pending = None
                            self._rebind_grace_codes.clear()
                            release = self._capacity_release(self.keysym_for(PASTE_KEYS.get(ch, ch)))
                            if release is None:
                                break
                            self._preserve_press_owners(release)
                            self.chord(*release[1])
                            self._finish_input(release)
                            self._input_capacity_pending = False
                            if time.monotonic() >= deadline:
                                break
                            continue
                        self._input_capacity_pending = False
                        item[2] += 1
                        if item[2] == len(item[1]):
                            self._finish_input()
                    else:
                        # A queued press can fail to resolve a keysym or alias
                        # an already-held code. Its later release must not use
                        # chord's legacy fallback to release a different owner.
                        args = item[1]
                        orphan = (item[0] == 'chord' and args[2] == 3
                                  and args[0] not in self._key_presses
                                  and not self.is_modifier(args[0]))
                        if not orphan and not self._retired_press(item):
                            getattr(self, item[0])(*args)
                        self._finish_input()
                except _RebindPending:
                    break
                if time.monotonic() >= deadline:
                    break
        finally:
            self._defer_rebind = False
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
        self._input.clear()
        self._superseded_presses.clear()
        self._input_bytes = self._work_items = self._work_bytes = 0
        self._release_reservations.clear()
        self._queued_releases = 0
        self._release_pending.clear()
        self.last_input_refusal = None
        self._input_capacity_pending = False
        self._rebind_pending = None
        self._rebind_grace_codes.clear()
        self._released_codes.update(self._keys_down)
        self._rebind_safe_codes.difference_update(self._keys_down)
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
