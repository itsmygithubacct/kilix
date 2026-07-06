"""browse.Term.read_input regressions: F14 (non-CSI escape sequences must be
consumed, never leaked as paste), W03 (SGR-pixel coords are 0-based), plus
focused F00/F47 checks and the F00 keypad follow-up (functional-PUA keys must
never carry text into kilix browse). All assertions FAIL on the pre-fix
parser."""
import os
import harness as H


def feed(data, **kw):
    return H.term_feed(data, **kw)


# ── F14: DCS/OSC/APC/PM/SOS/SS3 are consumed whole, no paste leak ────────────
non_csi = [
    b'\x1bP@kitty-cmd{"ok":true}\x1b\\',      # DCS + ST (was: paste body + '\\')
    b"\x1b]52;c;Zm9v\x07",                     # OSC + BEL
    b"\x1b]0;window title\x1b\\",              # OSC + ST
    b"\x1b_Gf=100,a=T;AAAA\x1b\\",             # APC (kitty graphics)
    b"\x1b^private-message\x1b\\",             # PM
    b"\x1bXstatus-string\x1b\\",               # SOS
    b"\x1bOP",                                  # SS3 (F1 via tmux) (was: paste 'OP')
]
for seq in non_csi:
    evs = feed(seq)
    assert evs == [], (seq, evs)               # fully consumed, nothing emitted

# split at every boundary: still never a paste, never a mangled fragment
for seq in non_csi:
    for i in range(len(seq) + 1):
        evs = feed(seq, chunks=[i])
        assert not any(e["kind"] == "paste" for e in evs), (seq, i, evs)

# an incomplete DCS stays buffered (no leak) until its terminator arrives
t, w = H.make_term()
os.write(w, b'\x1bP@kitty-cmd{"ok"')
assert t.read_input() == []                    # buffered, not leaked
assert t.inbuf.startswith(b"\x1bP")
os.write(w, b':true}\x1b\\\x1b[<0;5;5M')       # finish DCS + a real mouse event
evs = t.read_input()
assert evs == [{"kind": "mouse", "b": 0, "x": 5, "y": 5, "press": True}], evs
assert t.inbuf == b""
os.close(t.fd); os.close(w)

# a valid CSI event immediately after a DCS in one buffer still parses
evs = feed(b'\x1bP1$r0m\x1b\\\x1b[97u')
assert evs == [{"kind": "key", "key": "a", "code": "", "vk": 65,
                "mods": 1, "text": "a"}], evs

# ── >64-byte unparseable CSI resyncs to the next ESC (no fragment as paste) ──
junk = b"\x1b[" + b"9" * 70 + b"\x1b[<0;5;5M"
evs = feed(junk)                               # pre-fix: leaked '[999...' as paste
assert evs == [{"kind": "mouse", "b": 0, "x": 5, "y": 5, "press": True}], evs

# ── W03: SGR-pixel coordinates are 0-based (kitty round(global_x), no +1) ────
assert feed(b"\x1b[<0;0;0M")[0]["x"] == 0                    # was -1
assert feed(b"\x1b[<0;0;0M")[0]["y"] == 0                    # was -1
m = feed(b"\x1b[<64;512;300M")[0]
assert (m["x"], m["y"]) == (512, 300), m                     # was (511, 299)

# ── F00: lock-modifier text, incl. CapsLock case and CapsLock+Shift cancel ──
assert feed(b"\x1b[97;129u")[0]["text"] == "a"              # NumLock LED on
assert feed(b"\x1b[97;65u")[0]["text"] == "A"               # CapsLock: a -> A
assert feed(b"\x1b[97:65;66u")[0]["text"] == "a"            # CapsLock+Shift -> a
assert feed(b"\x1b[49;129u")[0]["text"] == "1"              # NumLock + digit
# routing key is preserved (never case-flipped) so shortcuts keep matching
assert feed(b"\x1b[97;65u")[0]["key"] == "a"

# ── F47: incomplete UTF-8 lead is held; malformed byte is not held forever ──
t, w = H.make_term()
os.write(w, b"\xc3")                            # lone lead byte of 'é'
assert t.read_input() == []                     # held back, not a replacement char
assert t.inbuf == b"\xc3"
os.write(w, b"\xa9")                            # completes 'é'
assert t.read_input() == [{"kind": "paste", "text": "é"}]
os.close(t.fd); os.close(w)
# an invalid lead (0xff) is NOT held (would stall) — emitted via replace
assert feed(b"\xff") == [{"kind": "paste", "text": "�"}]

# ── keypad/functional-PUA keys must stay text-less. F00 masks the lock LEDs
#    out of the text guard, but `key >= 32` still admitted the 57344+
#    functional block, so a keypad key under a NumLock/CapsLock LED (the case
#    F00 turned from inert into a leak) yielded a PUA glyph as text. Inert on
#    the desktop (_norm_key remaps/drops the PUA) but kilix browse forwards the
#    text verbatim into the page / URL bar. ──────────────────────────────────
for kp in (57399, 57405, 57408, 57409, 57414, 57417, 57424, 57426):
    for mods in (1, 65, 129):                      # none, CapsLock, NumLock LED
        assert feed(b"\x1b[%d;%du" % (kp, mods))[0]["text"] == "", (kp, mods)
assert feed(b"\x1b[233;129u")[0]["text"] == "é"    # NumLock + é: real text kept
assert feed(b"\x1b[8364;1u")[0]["text"] == "€"     # non-PUA above ASCII still text

# the browse surface itself: a NumLock keypad press must reach neither Chrome
# (as keyDown text) nor the URL bar — drive the real handlers with a stub CDP.
import browse
sent = []
pg = object.__new__(browse.Browse)
pg.cdp = type("C", (), {"send": lambda self, *a, **k: sent.append(a)})()
pg.sess = None
kp_ev = feed(b"\x1b[57405;129u")[0]                # KP under NumLock, as parsed
pg.url_edit = None
pg.on_key(kp_ev)                                   # focused web input
assert sent and all("text" not in payload for _, payload in sent), sent
pg.url_edit = ""
pg.url_edit_key(kp_ev)                             # URL bar
assert pg.url_edit == "", repr(pg.url_edit)

print("ok")
