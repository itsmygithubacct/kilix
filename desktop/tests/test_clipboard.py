"""Unified clipboard: Desk hub fan-out + SelectionBridge branch logic.

The hub is exercised on a real (headless) Desk. The bridge's X handlers are
driven with object.__new__ and a fake Xlib display/window — no server — so
every copy/paste/serve branch and the loop-guards run without an Xvfb. The
live ICCCM handshake against a real selection still needs VM validation.
"""
import harness as H                 # noqa: sets sys.path for the imports below
import clipboard
from Xlib import X, Xatom
from Xlib.ext import xfixes


class Rec:
    """Namespace that records every method call as (name, args) on .calls."""
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.calls = []

    def _mk(self, name):
        def f(*a, **k):
            self.calls.append((name, a, k))
            return self.__dict__.get("_ret_" + name)
        return f

    def __getattr__(self, name):        # only for undefined attrs
        return self._mk(name)

    def did(self, name):
        return [c for c in self.calls if c[0] == name]


class Ev:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ── Desk hub: fan-out, source-skip, OSC-52 gate ─────────────────────────────
d = H.make_desk()
got_a, got_b = [], []
sink_a = lambda t: got_a.append(t)
sink_b = lambda t: got_b.append(t)
d.add_clip_sink(sink_a)
d.add_clip_sink(sink_b)

d.set_clipboard("hello")
assert d.clipboard == "hello"
assert got_a == ["hello"] and got_b == ["hello"], (got_a, got_b)

# a copy that came from sink_a must not echo back into sink_a
d.set_clipboard("world", source=sink_a)
assert got_a == ["hello"], got_a
assert got_b == ["hello", "world"], got_b

d.remove_clip_sink(sink_b)
d.set_clipboard("again")
assert got_b == ["hello", "world"], "removed sink must stop receiving"

# OSC 52 mirrors to the terminal only when no host bridge owns the host CLIPBOARD
term = Rec()
d.term = term
d.clip_host = None
d.set_clipboard("osc")
assert term.did("write"), "with no host bridge, set_clipboard must emit OSC 52"
term.calls.clear()
d.clip_host = object()               # a host bridge is active
d.set_clipboard("noosc")
assert not term.did("write"), "host bridge active: OSC 52 must be suppressed"


# ── SelectionBridge: build a bare bridge over a fake display/window ──────────
def bare_bridge():
    b = object.__new__(clipboard.SelectionBridge)
    b.desk = Rec()
    b.d = Rec()
    b.win = Rec(id=4242)
    b._text = None
    b._ok = True
    b.A_CLIPBOARD = 1
    b.A_UTF8 = 2
    b.A_TARGETS = 3
    b.A_TEXT = 4
    b.A_STRING = Xatom.STRING
    b.A_PROP = 9
    return b


# push(): take ownership + dedup + None-guard
b = bare_bridge()
b.push("abc")
assert b._text == "abc"
assert len(b.win.did("set_selection_owner")) == 1
b.push("abc")                        # identical: no re-assert
assert len(b.win.did("set_selection_owner")) == 1, "dedup must skip re-owning"
b.push(None)
assert len(b.win.did("set_selection_owner")) == 1

# XFixes owner-change: our own push is ignored; a foreign owner triggers a read
b = bare_bridge()
mine = object.__new__(xfixes.SetSelectionOwnerNotify)
mine.owner, mine.selection = b.win.id, b.A_CLIPBOARD
b._handle(mine)
assert not b.win.did("convert_selection"), "own ownership change must be ignored"

foreign = object.__new__(xfixes.SetSelectionOwnerNotify)
foreign.owner, foreign.selection = 777, b.A_CLIPBOARD
b._handle(foreign)
assert b.win.did("convert_selection"), "a foreign copy must start a read"

# read reply: property → decoded text published to the hub with source=push
b = bare_bridge()
b.win._ret_get_full_property = Rec(value=b"pasted text")
reply = Ev(type=X.SelectionNotify, property=b.A_PROP, target=b.A_UTF8)
b._handle(reply)
assert b._text == "pasted text"
setc = b.desk.did("set_clipboard")
assert setc and setc[0][1][0] == "pasted text", setc
assert setc[0][2].get("source") == b.push, "must tag itself as the source"
assert b.win.did("delete_property"), "property must be consumed after reading"

# a read that matches what we already have must not re-publish (loop guard)
b = bare_bridge()
b._text = "same"
b.win._ret_get_full_property = Rec(value=b"same")
b._handle(Ev(type=X.SelectionNotify, property=b.A_PROP, target=b.A_UTF8))
assert not b.desk.did("set_clipboard"), "unchanged read must not re-publish"

# UTF8 refused (property==0) → fall back to STRING once
b = bare_bridge()
b._handle(Ev(type=X.SelectionNotify, property=0, target=b.A_UTF8))
conv = b.win.did("convert_selection")
assert conv and conv[0][1][1] == b.A_STRING, "must retry with STRING target"

# SelectionRequest for TARGETS → advertise the text targets, positive notify
b = bare_bridge()
b._text = "payload"
req = Rec(id=55)
b._handle(Ev(type=X.SelectionRequest, requestor=req, target=b.A_TARGETS,
             property=b.A_PROP, selection=b.A_CLIPBOARD, time=X.CurrentTime))
cp = req.did("change_property")
assert cp and cp[0][1][1] == Xatom.ATOM, "TARGETS reply is a list of ATOMs"
assert b.A_UTF8 in cp[0][1][3], "TARGETS must advertise UTF8_STRING"
se = req.did("send_event")
assert se and se[0][1][0].property == b.A_PROP, "served: notify carries the prop"

# SelectionRequest for UTF8 → hand over the bytes
b = bare_bridge()
b._text = "payload"
req = Rec(id=55)
b._handle(Ev(type=X.SelectionRequest, requestor=req, target=b.A_UTF8,
             property=b.A_PROP, selection=b.A_CLIPBOARD, time=X.CurrentTime))
cp = req.did("change_property")
assert cp and cp[0][1][3] == b"payload", cp

# SelectionClear: we lost ownership, forget what we served
b = bare_bridge()
b._text = "held"
b._handle(Ev(type=X.SelectionClear))
assert b._text is None, "SelectionClear must drop our served text"

# _decode tolerates bytes and int arrays
assert clipboard._decode(b"hi") == "hi"
assert clipboard._decode([104, 105]) == "hi"
assert clipboard._decode(b"\xff\xfe") == "��"   # never raises

print("test_clipboard OK")
