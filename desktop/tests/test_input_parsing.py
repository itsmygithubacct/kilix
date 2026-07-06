"""WHEEL/W04 regression (inverted wheel_repro): after the CSI_RE '-' fix,
negative SGR-pixel coords (pointer in pane padding, or motion through it) parse
as clean mouse events at every chunk split instead of leaking as paste text.
Also locks in F00 lock-modifier text and F47 split-UTF-8 reassembly.

Pre-fix these all FAILED: negative bursts produced {'kind':'paste'} events (and
end-to-end typed '[<64;-2;300M...' into Notepad); '\x1b[97;129u' produced
text=''; a split 'é' produced two U+FFFD."""
import harness as H
import main as desk_main
import apps


def evs_of(data, **kw):
    return H.term_feed(data, **kw)


# ── negative-coordinate wheel/motion now parse as mouse, never paste ────────
neg_x = b"\x1b[<64;-2;300M" * 6          # wheel, pointer 2px into left padding
neg_y = b"\x1b[<65;500;-3M" * 6          # wheel, pointer above the pane
mot   = b"\x1b[<35;-1;200M" * 6          # W04: motion through the padding, no scroll
for label, burst, b in (("neg-x wheel", neg_x, 64),
                        ("neg-y wheel", neg_y, 65),
                        ("neg-x motion", mot, 35)):
    evs = evs_of(burst)
    assert len(evs) == 6, (label, evs)
    assert all(e["kind"] == "mouse" and e["b"] == b for e in evs), (label, evs)
    assert not any(e["kind"] == "paste" for e in evs), (label, evs)
    assert evs[0]["x"] < 0 or evs[0]["y"] < 0, (label, evs)  # signed coord intact

# the specific negative values round-trip
e = evs_of(b"\x1b[<64;-2;300M")[0]
assert (e["x"], e["y"]) == (-2, 300), e

# ── every chunk split of the negative burst stays mouse (was: paste at all) ──
for i in range(len(neg_x) + 1):
    evs = evs_of(neg_x, chunks=[i])
    assert not any(e["kind"] == "paste" for e in evs), (i, evs)
# byte-by-byte too
evs = evs_of(neg_x, chunks=range(len(neg_x)))
assert len(evs) == 6 and all(e["kind"] == "mouse" for e in evs), evs

# ── positive burst control: still clean mouse at every split (unchanged) ─────
good = (b"\x1b[<35;600;300M\x1b[<64;600;300M\x1b[<64;600;300M"
        b"\x1b[<65;601;302M\x1b[<68;601;302M\x1b[<0;601;302M\x1b[<0;601;302m")
for i in range(len(good) + 1):
    evs = evs_of(good, chunks=[i])
    assert len(evs) == 7 and all(e["kind"] == "mouse" for e in evs), (i, evs)
evs = evs_of(good, chunks=range(len(good)))
assert len(evs) == 7 and all(e["kind"] == "mouse" for e in evs), evs

# ── a single negative event no longer STALLS the events queued behind it ─────
mix = b"\x1b[<64;500;300M" * 3 + b"\x1b[<64;-1;300M" + b"\x1b[<64;500;300M" * 3
t, w = H.make_term()
import os
os.write(w, mix)
evs = t.read_input()
assert len(evs) == 7 and all(e["kind"] == "mouse" for e in evs), evs
assert t.inbuf == b"", t.inbuf
os.close(t.fd); os.close(w)

# ── F00: NumLock/CapsLock LED must not blank printable text ──────────────────
assert evs_of(b"\x1b[97u")[0]["text"] == "a"                 # plain
assert evs_of(b"\x1b[97;129u")[0]["text"] == "a"             # NumLock LED on
assert evs_of(b"\x1b[97;65u")[0]["text"] == "A"              # CapsLock -> upper
assert evs_of(b"\x1b[97;5u")[0]["text"] == ""                # Ctrl+a stays a shortcut
k = evs_of(b"\x1b[115;5u")[0]                                # Ctrl+s
assert k["key"] == "s" and k["text"] == ""                   # routing key unchanged

# ── F47: a multibyte char split across read()s is reassembled, not mangled ──
snow = "☃".encode()                                          # 3 bytes e2 98 83
for i in range(1, len(snow)):
    assert evs_of(snow, chunks=[i]) == [{"kind": "paste", "text": "☃"}], i
assert evs_of("é".encode(), chunks=[1]) == [{"kind": "paste", "text": "é"}]

# ── END-TO-END: a padding wheel burst no longer types into Notepad ──────────
d = desk_main.Desk(term=None, size=(1024, 768))
apps.open(d, "notepad", None)
np = d.wm.windows[-1]
before = np.ta.text()
for raw in evs_of(b"\x1b[<64;-2;300M" * 8):
    if raw["kind"] == "mouse":
        ev = d._norm_mouse(raw)
        if ev:
            d.dispatch_mouse(ev)
    elif raw["kind"] == "paste":
        d.dispatch_paste(raw["text"])
assert np.ta.text() == before == "", np.ta.text()

print("ok")
