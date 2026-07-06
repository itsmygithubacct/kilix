"""kilix 95 — UI sound engine + scheme model.

Original synthesized Win95-style cues; NO external assets. Short mono .wav
files generated in pure Python (wave + math + struct), cached under
~/.local/share/kilix/sounds and regenerated when missing. A *scheme* maps each
system event to a sound file (any format) or silence, defaulting to the
built-in cue; the active scheme persists to sounds/scheme.json, named schemes
to sounds/schemes/<name>.json. Playback is fire-and-forget through a detached
CLI player (WAV via paplay/aplay, other formats via ffplay/mpv/cvlc), so it
never blocks the event loop and never raises when no player exists.
"""
import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import threading
import wave

RATE = 44100
_PLAYERS = ("paplay", "aplay", "ffplay", "play")     # WAV players
_MEDIA_PLAYERS = ("ffplay", "mpv", "cvlc")           # non-WAV (kilix-amp formats)
_AUDIO_EXT = (".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a",
              ".aac", ".aiff", ".aif", ".aifc", ".wma")

# equal-temperament reference pitches (Hz)
C5, D5, E5, F5, G5, A5, B5 = 523.25, 587.33, 659.25, 698.46, 784.0, 880.0, 987.77
C6, E6, G6 = 1046.5, 1318.5, 1568.0
C4, E4, G4 = 261.63, 329.63, 392.0


def _data_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "kilix", "sounds")


# ── synthesis primitives ────────────────────────────────────────────────────
def _osc(freq, i, kind):
    ph = 2 * math.pi * freq * (i / RATE)
    if kind == "square":
        return 1.0 if math.sin(ph) >= 0 else -1.0
    if kind == "tri":
        return (2.0 / math.pi) * math.asin(math.sin(ph))
    return math.sin(ph)


def _env(i, n, a, r):
    at = max(1, int(a * RATE))
    rt = max(1, int(r * RATE))
    if i < at:
        return i / at
    if i > n - rt:
        return max(0.0, (n - i) / rt)
    return 1.0


def _note(buf, freq, start, dur, amp=0.3, kind="sine", a=0.008, r=0.12):
    n = int(dur * RATE)
    s = int(start * RATE)
    for i in range(n):
        j = s + i
        if 0 <= j < len(buf):
            buf[j] += amp * _env(i, n, a, r) * _osc(freq, i, kind)


def _glide(buf, f0, f1, start, dur, amp=0.25):
    n = int(dur * RATE)
    s = int(start * RATE)
    ph = 0.0
    for i in range(n):
        f = f0 + (f1 - f0) * (i / n)
        ph += 2 * math.pi * f / RATE
        j = s + i
        if 0 <= j < len(buf):
            buf[j] += amp * _env(i, n, 0.004, 0.03) * math.sin(ph)


def _blank(dur):
    return [0.0] * int(dur * RATE)


# ── the sounds ───────────────────────────────────────────────────────────────
def _startup():
    b = _blank(1.15)
    for f, t in ((C5, 0.0), (E5, 0.11), (G5, 0.22)):     # ascending arpeggio
        _note(b, f, t, 0.20, amp=0.26, kind="tri")
    for f in (C5, E5, G5, C6):                            # crowning major chord
        _note(b, f, 0.34, 0.72, amp=0.17, kind="tri", a=0.01, r=0.4)
    return b


def _shutdown():
    b = _blank(1.05)
    for f, t in ((C6, 0.0), (G5, 0.14), (E5, 0.28)):     # descending
        _note(b, f, t, 0.20, amp=0.24, kind="tri")
    for f in (C4, E4, G4):                                # low resolving chord
        _note(b, f, 0.40, 0.6, amp=0.16, kind="tri", a=0.01, r=0.35)
    return b


def _error():
    b = _blank(0.5)                                       # low double buzz
    _note(b, 130.81, 0.0, 0.16, amp=0.3, kind="square", r=0.03)
    _note(b, 130.81, 0.21, 0.24, amp=0.3, kind="square", r=0.05)
    return b


def _exclamation():
    b = _blank(0.4)                                       # two-tone alert
    _note(b, E5, 0.0, 0.13, amp=0.26, kind="tri")
    _note(b, A5, 0.13, 0.2, amp=0.26, kind="tri")
    return b


def _asterisk():
    b = _blank(0.45)                                      # soft ding + octave
    _note(b, A5, 0.0, 0.42, amp=0.24, kind="sine", a=0.005, r=0.36)
    _note(b, A5 * 2, 0.0, 0.3, amp=0.08, kind="sine", a=0.005, r=0.28)
    return b


def _question():
    b = _blank(0.4)                                       # rising two-tone
    _note(b, D5, 0.0, 0.12, amp=0.24, kind="tri")
    _note(b, G5, 0.12, 0.22, amp=0.24, kind="tri")
    return b


def _minimize():
    b = _blank(0.14)
    _glide(b, A5, D5, 0.0, 0.12, amp=0.22)               # quick descending blip
    return b


def _maximize():
    b = _blank(0.14)
    _glide(b, D5, A5, 0.0, 0.12, amp=0.22)               # quick ascending blip
    return b


def _restore():
    b = _blank(0.12)
    _note(b, E5, 0.0, 0.09, amp=0.2, kind="tri", r=0.05)
    return b


def _open():
    b = _blank(0.1)
    _glide(b, E5, A5, 0.0, 0.07, amp=0.16)               # soft click up
    return b


def _close():
    b = _blank(0.1)
    _glide(b, A5, E5, 0.0, 0.07, amp=0.16)               # soft click down
    return b


def _recycle_empty():
    dur = 0.5                                             # filtered-noise whoosh
    n = int(dur * RATE)
    b = [0.0] * n
    rnd = random.Random(95)
    prev = 0.0
    for i in range(n):
        p = i / n
        cut = 0.02 + 0.25 * math.sin(math.pi * p)        # sweeping one-pole LP
        prev += cut * (rnd.uniform(-1.0, 1.0) - prev)
        b[i] = 0.55 * math.sin(math.pi * p) * prev        # bell-shaped envelope
    return b


_GEN = {
    "startup": _startup, "shutdown": _shutdown, "error": _error,
    "exclamation": _exclamation, "asterisk": _asterisk, "question": _question,
    "minimize": _minimize, "maximize": _maximize, "restore": _restore,
    "open": _open, "close": _close, "recycle_empty": _recycle_empty,
}


def names():
    return list(_GEN)


# ── cache (generate .wav files, regenerate if missing) ───────────────────────
def path_for(name):
    return os.path.join(_data_dir(), name + ".wav")


def _valid(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() > 0
    except Exception:
        return False


def _write(path, samples):
    peak = max((abs(s) for s in samples), default=0.0)
    scale = (0.89 / peak) if peak > 0 else 1.0           # normalize loudness
    frames = bytearray()
    for s in samples:
        v = int(max(-1.0, min(1.0, s * scale)) * 32767)
        frames += struct.pack("<h", v)
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    os.replace(tmp, path)                                # atomic swap


def ensure(name):
    """Return the cached wav path for name, generating it if missing/invalid;
    None if name is unknown or generation fails."""
    if name not in _GEN:
        return None
    p = path_for(name)
    if _valid(p):
        return p
    try:
        os.makedirs(_data_dir(), exist_ok=True)
        _write(p, _GEN[name]())
    except OSError:
        return None
    return p


def ensure_all():
    """Generate every sound; return {name: path or None}."""
    return {n: ensure(n) for n in _GEN}


_warmed = False


def warm():
    """Pre-synthesize every wav off-thread so no first-play() ever blocks the
    loop (one-time; the files persist). No-op after the first call."""
    global _warmed
    if _warmed:
        return
    _warmed = True
    threading.Thread(target=ensure_all, daemon=True).start()


# ── event registry & scheme model ────────────────────────────────────────────
_EVENTS = [
    ("startup", "Startup"), ("shutdown", "Shutdown"),
    ("error", "Critical Stop"), ("exclamation", "Exclamation"),
    ("asterisk", "Default Beep"), ("question", "Question"),
    ("minimize", "Minimize"), ("maximize", "Maximize"),
    ("restore", "Restore"), ("open", "Open program"),
    ("close", "Close program"), ("recycle_empty", "Empty Recycle Bin"),
]
_EVENT_IDS = {eid for eid, _ in _EVENTS}

DEFAULT_SCHEME = "kilix 95"                              # built-in cue per event
NO_SOUNDS = "No Sounds"                                  # all silent

_active = None                                           # dict: event_id -> path|None (overrides only)
_active_name = DEFAULT_SCHEME


def events(generate=True):
    """[(event_id, human label, default built-in wav path)] in display order.
    generate=False lists cue paths without synthesizing (for UI enumeration)."""
    return [(eid, label, ensure(eid) if generate else path_for(eid))
            for eid, label in _EVENTS]


def _scheme_path():
    return os.path.join(_data_dir(), "scheme.json")


def _schemes_dir():
    return os.path.join(_data_dir(), "schemes")


def _named_path(name):
    safe = re.sub(r"[^\w .-]", "_", name).strip() or "scheme"
    return os.path.join(_schemes_dir(), safe + ".json")


def _sanitize(mapping):
    """Keep only known event ids mapped to a str path or None."""
    out = {}
    if isinstance(mapping, dict):
        for eid in _EVENT_IDS:
            if eid in mapping:
                v = mapping[eid]
                out[eid] = v if (v is None or isinstance(v, str)) else None
    return out


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)                                # atomic swap


def _ensure_loaded():
    """Load the active scheme once; tolerate a missing/corrupt file."""
    global _active, _active_name
    if _active is not None:
        return
    try:
        with open(_scheme_path()) as f:
            data = json.load(f)
        _active = _sanitize(data.get("sounds", {}))
        _active_name = data.get("name") or DEFAULT_SCHEME
    except Exception:
        _active, _active_name = {}, DEFAULT_SCHEME       # regenerate default


def _save_active():
    try:
        _write_json(_scheme_path(), {"name": _active_name, "sounds": _active})
    except OSError:
        pass


def current_scheme():
    """The active scheme's overrides {event_id: path|None}; empty == default."""
    _ensure_loaded()
    return dict(_active)


def set_sound(event_id, path_or_None):
    """Bind one event to a file path (any format) or None for silence."""
    _ensure_loaded()
    if event_id in _EVENT_IDS:
        _active[event_id] = path_or_None
        _save_active()


def scheme_names():
    """Built-in schemes plus every saved named scheme."""
    out = [DEFAULT_SCHEME, NO_SOUNDS]
    try:
        for fn in sorted(os.listdir(_schemes_dir())):
            if fn.endswith(".json"):
                nm = fn[:-5]
                if nm not in out:
                    out.append(nm)
    except OSError:
        pass
    return out


def scheme_overrides(name):
    """The overrides {event_id: path|None} a scheme defines, WITHOUT making it
    active or persisting anything (for deferred/working-copy editing)."""
    if name == DEFAULT_SCHEME:
        return {}
    if name == NO_SOUNDS:
        return {eid: None for eid in _EVENT_IDS}
    try:
        with open(_named_path(name)) as f:
            return _sanitize(json.load(f).get("sounds", {}))
    except Exception:
        return {}


def load_scheme(name):
    """Make `name` the active scheme (built-in or saved); persist it. A
    missing/corrupt named scheme falls back to the default. Returns overrides."""
    global _active, _active_name
    _active = scheme_overrides(name)
    _active_name = name
    _save_active()
    return dict(_active)


def save_scheme_as(name):
    """Persist the active scheme under `name`; return its file path."""
    _ensure_loaded()
    global _active_name
    p = _named_path(name)
    try:
        _write_json(p, {"name": name, "sounds": _active})
        _active_name = name
        _save_active()
    except OSError:
        pass
    return p


def reset():
    """Restore the built-in 'kilix 95' default scheme."""
    return load_scheme(DEFAULT_SCHEME)


def library_sounds(generate=True):
    """Candidate sound files: the built-in cues plus any audio files found in
    the shared library dir and the user's ~/audio_clips. generate=False lists
    the cue paths without synthesizing them on the calling thread."""
    seen, out = set(), []
    cues = ensure_all() if generate else {n: path_for(n) for n in _GEN}
    for _, p in sorted(cues.items()):                    # synthesized cues first
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    for d in (_data_dir(), os.path.join(os.path.expanduser("~"), "audio_clips")):
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in entries:
            if fn.lower().endswith(_AUDIO_EXT):
                p = os.path.join(d, fn)
                if p not in seen and os.path.isfile(p):
                    seen.add(p)
                    out.append(p)
    return out


# ── playback (fire-and-forget, never blocks, never raises) ───────────────────
def _which(cands):
    for name in cands:
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def player():
    """Absolute path of the first available WAV player command, or None."""
    return _which(_PLAYERS)


def _player_for(path):
    """WAV → paplay/aplay/…; any other format → ffplay/mpv/cvlc (amp formats)."""
    if os.path.splitext(path)[1].lower() == ".wav":
        return _which(_PLAYERS)
    return _which(_MEDIA_PLAYERS)


def _argv(exe, path, volume):
    base = os.path.basename(exe)
    vol = max(0, min(100, int(volume)))
    if base == "ffplay":
        return [exe, "-nodisp", "-autoexit", "-loglevel", "quiet",
                "-volume", str(vol), path]
    if base == "mpv":
        return [exe, "--no-video", "--really-quiet", "--volume=%d" % vol, path]
    if base in ("cvlc", "vlc"):
        return [exe, "--intf", "dummy", "--no-video", "--play-and-exit",
                "--quiet", "--gain", "%.3f" % (vol / 100.0), path]
    if base == "paplay":
        return [exe, "--volume=%d" % int(vol * 655.36), path]
    if base == "play":                                   # sox
        return [exe, "-q", path, "vol", "%.3f" % (vol / 100.0)]
    return [exe, path]                                   # aplay: no volume flag


def _play_file(path, volume, muted):
    """Spawn a detached player for an already-resolved file. Honors mute/level
    and KILIX_NO_SOUND=1; no-op for silence (None); never raises."""
    if path is None:
        return False
    if muted or int(volume) <= 0:
        return False
    if os.environ.get("KILIX_NO_SOUND") == "1":
        return False
    if not os.path.isfile(path):
        return False
    exe = _player_for(path)
    if exe is None:
        return False
    try:
        subprocess.Popen(_argv(exe, path, volume),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        return False
    return True


def _resolve(event_id):
    """The active scheme's file for an event, or the built-in cue if unset."""
    _ensure_loaded()
    if event_id in _active:
        return _active[event_id]                         # path or None (silent)
    return ensure(event_id)                              # default built-in wav


def play(event_id, volume=100, muted=False):
    """Play the sound bound to a system event through the active scheme,
    detached. Returns True iff a player was spawned; never raises."""
    return _play_file(_resolve(event_id), volume, muted)


def preview(path, volume=100, muted=False):
    """Audition an arbitrary file the same way play() does."""
    return _play_file(path, volume, muted)
