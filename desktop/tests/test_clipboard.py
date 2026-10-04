"""Unified clipboard: Desk hub fan-out + SelectionBridge branch logic.

The hub is exercised on a real headless Desk. The shared SDK tests exercise
real authenticated X11 transfers; this provider verifies format preservation,
source suppression and the terminal fallback.
"""
import harness as H                 # noqa: sets sys.path for the imports below
import clipboard
import os
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


# Rich clipboard values retain binary data and clear stale native text.
content = clipboard.Content({'image/png': b'\x89PNG\r\n\x1a\n\0\xff'})
received = []
d.add_content_sink(received.append)
d.set_clipboard_content(content)
assert d.clipboard == ''
assert d.clipboard_content == content
assert received == [content]
# A bound callback must be recognized when supplied as the source, even if
# attribute access created a new bound-method object.
class Sink:
    def push(self, value):
        raise AssertionError('clipboard echoed to its source')
sink = Sink()
d.add_content_sink(sink.push)
d.set_clipboard_content(content, source=sink.push)
d.remove_content_sink(sink.push)
# Changing text replaces a previous file/image clipboard value.
d.set_clipboard('new text')
assert d.clipboard_content.get('image/png') is None
assert d.clipboard_content.files[1] == ()
# Explicitly clearing text also clears an OSC 52-only clipboard.
d.clip_host = None
term.calls.clear()
d.set_clipboard('')
assert term.did('write')

# Xauthority overrides are scoped to the single connection.
old = os.environ.get('XAUTHORITY')
try:
    os.environ['XAUTHORITY'] = '/tmp/host-xauth'
    with clipboard.xauthority_env('/tmp/private-xauth'):
        assert os.environ['XAUTHORITY'] == '/tmp/private-xauth'
    assert os.environ['XAUTHORITY'] == '/tmp/host-xauth'
finally:
    if old is None:
        os.environ.pop('XAUTHORITY', None)
    else:
        os.environ['XAUTHORITY'] = old
print('test_clipboard OK')
