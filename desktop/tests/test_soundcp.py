"""kilix 95 — Sounds control panel (apps/soundcp.py)."""
import os
import tempfile

# isolate the sound cache + scheme store from the real ~/.local/share
_cache = tempfile.mkdtemp(prefix="kilix95-soundcp-")
os.environ["XDG_DATA_HOME"] = _cache
os.environ["KILIX_NO_SOUND"] = "1"                       # no players spawn

import harness as H
import icons
import sounds
import widgets as W
from apps import soundcp

sounds.reset()                                           # start on the default


def _submit_inputbox(box, text):
    """Type `text` into a wm.inputbox and confirm via its OK button."""
    fld = next(w for w in box.widgets if isinstance(w, W.TextField))
    fld.set(text)
    ok = next(w for w in box.widgets
              if isinstance(w, W.Button) and w.text == "OK")
    ok.cb()

# ── opens as a singleton; registers its own icon ────────────────────────────
d = H.make_desk()
win = soundcp.open(d)
assert H.find_window(d, "SoundCP") is win
assert soundcp.open(d) is win                            # singleton: same window
assert "soundcp" in icons.ICONS
icons.get("soundcp", 16); icons.get("soundcp", 32)       # renders at both sizes
assert win.events_lb.items                               # events listed
assert win.cur is not None                               # first event selected

# ── select an event, assign a sound via set-sound path, Apply ───────────────
eids = [e[0] for e in sounds.events()]
i = eids.index("error")
win.events_lb.sel = i
win._select_event(win.events_lb.items[i])
assert win.cur == "error"
win._set_sound_path("/clips/boom.mp3")                   # bind a (non-WAV) path
assert win.work["error"] == "/clips/boom.mp3"
assert sounds.current_scheme().get("error") is None      # not yet applied
win._apply()
assert sounds.current_scheme().get("error") == "/clips/boom.mp3"

# the assigned event shows the speaker glyph; a silenced one does not
win._select_event(win.events_lb.items[eids.index("startup")])
win.snd_dd.index = len(win.sound_paths)                  # the "(None)" row
win._sound_changed()
assert win.work["startup"] is None
marks = {it[2]: it[0] for it in win.events_lb.items}
assert marks["error"] == "soundcp" and marks["startup"] is None

# ── picking a scheme is DEFERRED: it edits the working copy, not the global
#    scheme, until Apply — so browsing then Cancel/X backs out cleanly ─────────
sounds.reset()
before = sounds.current_scheme()
win._scheme_changed(sounds.NO_SOUNDS)
assert all(it[0] is None for it in win.events_lb.items)  # working view silenced
assert sounds.current_scheme() == before                 # global NOT touched yet
win._apply()                                             # commit the picked scheme
assert sounds.play("error", volume=90) is False
assert sounds.play("startup", volume=90) is False

# ── back to the default scheme restores the built-in cues (after Apply) ──────
win._scheme_changed(sounds.DEFAULT_SCHEME)
win._apply()
assert sounds.current_scheme() == {}
assert all(it[0] == "soundcp" for it in win.events_lb.items)

# ── Save As commits the working edits into a named scheme ────────────────────
win._select_event(win.events_lb.items[eids.index("close")])
win._set_sound_path("/clips/thud.ogg")
# drive the Save As inputbox (modal UI) through its OK button
win._save_as()
_submit_inputbox(d.wm.modal_top(), "My Scheme")
assert "My Scheme" in sounds.scheme_names()
sounds.load_scheme("My Scheme")
assert sounds.current_scheme().get("close") == "/clips/thud.ogg"

# ── Save As rejects the reserved built-in names (would be unloadable) ────────
before_names = set(sounds.scheme_names())
win._save_as()
_submit_inputbox(d.wm.modal_top(), sounds.DEFAULT_SCHEME)
assert set(sounds.scheme_names()) == before_names        # nothing new written
d.wm.modal_top().close()                                 # dismiss the warning

# ── Preview never raises headless (KILIX_NO_SOUND makes it a no-op) ──────────
win._select_event(win.events_lb.items[eids.index("error")])
win._preview()                                           # must not raise
win.snd_dd.index = len(win.sound_paths)                  # "(None)": nothing to play
win._preview()

# ── the panel paints without error ──────────────────────────────────────────
d.render()

print("ok")
