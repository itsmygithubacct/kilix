"""kilix 95 — UI sound engine: synthesis, caching, detached playback."""
import os
import tempfile
import time
import wave

# isolate the cache from the real ~/.local/share so tests don't pollute it
_cache = tempfile.mkdtemp(prefix="kilix95-snd-")
os.environ["XDG_DATA_HOME"] = _cache

import harness as H
import sounds


# ── every synthesized wav generates and is a valid readable wave ────────────
made = sounds.ensure_all()
assert set(made) == set(sounds.names())
for name, path in made.items():
    assert path and os.path.isfile(path), name
    assert path == os.path.join(_cache, "kilix", "sounds", name + ".wav")
    with wave.open(path, "rb") as w:                     # readable + non-empty
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == sounds.RATE
        assert w.getnframes() > 0
assert {"startup", "shutdown", "error", "exclamation", "asterisk",
        "question", "minimize", "maximize", "restore",
        "recycle_empty"} <= set(sounds.names())

# regenerates when the cached file is missing
os.remove(made["startup"])
assert not os.path.exists(made["startup"])
assert sounds.ensure("startup") == made["startup"]
assert os.path.isfile(made["startup"])
assert sounds.ensure("nope") is None                     # unknown name


# ── player selection picks a plausible command (or None) ────────────────────
p = sounds.player()
assert p is None or os.path.basename(p) in sounds._PLAYERS
for exe in ("/usr/bin/paplay", "/usr/bin/aplay", "/usr/bin/ffplay",
            "/usr/bin/play"):
    argv = sounds._argv(exe, "/x.wav", 80)
    assert argv[0] == exe and "/x.wav" in argv         # command + file present


# ── play() honors mute / level / KILIX_NO_SOUND and never raises ────────────
os.environ["KILIX_NO_SOUND"] = "1"
t0 = time.time()
assert sounds.play("startup") is False                   # disabled by env
assert time.time() - t0 < 0.5                            # returned immediately
os.environ.pop("KILIX_NO_SOUND", None)
assert sounds.play("startup", volume=0) is False         # zero level
assert sounds.play("startup", muted=True) is False       # muted
assert sounds.play("no_such_sound", volume=90) is False  # unknown, no raise


# ── Desk.play_sound: no-op headless (term=None), never raises ───────────────
d = H.make_desk()
assert d.term is None
t0 = time.time()
d.play_sound("startup")                                  # must not spawn/raise
d.play_sound("recycle_empty")
assert time.time() - t0 < 0.5

# the whole desktop still builds and paints with the sound engine wired in
import apps
apps.open(d, "notepad", None)                            # WM.add "open" cue
win = H.find_window(d, "Notepad")
d.wm.minimize(win)                                       # "minimize" cue
d.wm.toggle_maximize(win)                                # "maximize" cue
d.wm.toggle_maximize(win)                                # "restore" cue
import wm
wm.msgbox(d, "Test", "boom", icon="error")               # dialog cue
d.render()

# ── warm() fills the cache off-thread (one-time) without blocking ────────────
os.remove(sounds.path_for("close"))
assert not os.path.exists(sounds.path_for("close"))
sounds._warmed = False
t0 = time.time()
sounds.warm()
assert time.time() - t0 < 0.1                    # returns immediately (off-thread)
deadline = time.time() + 10
while not os.path.exists(sounds.path_for("close")) and time.time() < deadline:
    time.sleep(0.02)
assert os.path.isfile(sounds.path_for("close"))  # background thread regenerated it
sounds.warm()                                    # idempotent: no-op after the first

print("ok")
