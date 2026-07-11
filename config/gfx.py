"""kilix — direct (t=d) kitty graphics transmission for streamed/remote sessions.

The fast LOCAL path used by apprun.py / browse.py writes each raw-RGB frame to a
/dev/shm file and sends the terminal only that PATH via the graphics protocol's
temporary-file mode (t=t). That path is meaningless to a kitty running on another
machine, so a streamed/attached session would show a blank pane.

When a session is streamed (KILIX_STREAM=1, exported by `kilix serve`), frames
must instead be carried INLINE in the escape stream. `blit_direct` zlib-compresses
the frame, base64-encodes it, and emits it as a chunked  t=d,o=z  kitty graphics
APC — the only transmission medium that survives a network hop. When inside tmux
(`$TMUX` set), each APC is wrapped in the tmux DCS passthrough envelope so it
survives `allow-passthrough on`.

This module is intentionally dependency-free (stdlib only) and has no side effects
beyond `term.write`, so `build_direct` can be unit-tested without a terminal.
"""
import base64
import zlib

# kitty requires direct-transmission data to be chunked into pieces no larger
# than 4096 bytes; 4096 % 4 == 0 so every base64 slice is independently valid
# (kitty concatenates the slices before decoding).
CHUNK = 4096


def _tmux_wrap(apc: str) -> str:
    # tmux DCS passthrough:  ESC P tmux ; <payload, every ESC doubled> ESC \
    # Only the APC (which itself contains ESC) needs wrapping; plain CSI cursor
    # moves are understood by tmux directly and are emitted unwrapped.
    return "\x1bPtmux;" + apc.replace("\x1b", "\x1b\x1b") + "\x1b\\"


def build_direct(rgb: bytes, w: int, h: int, cols: int, rows: int, img_id: int,
                 off_row: int = 1, off_col: int = 1, in_tmux: bool = False) -> str:
    """Build the escape sequence that inline-transmits+displays one RGB frame.

    rgb        raw 24-bit RGB, exactly w*h*3 bytes (f=24)
    w, h       source pixel dimensions (kitty checks inflated len == w*h*3)
    cols, rows target placement size in cells (GPU letterbox/scale, like t=t)
    img_id     stable per-producer image id (i=) — distinct producers reaching
               one client must not collide; caller derives from KITTY_WINDOW_ID
    off_row/off_col  1-based cursor cell the placement is anchored at
    in_tmux    wrap each APC for tmux passthrough
    """
    if not rgb:
        return ""
    # level 1: ~2-3x faster than the default (6) for ~5% larger output — a good
    # trade on this per-frame, Python-driven path where CPU is the bottleneck.
    comp = zlib.compress(rgb, 1)
    payload = base64.b64encode(comp)
    chunks = [payload[i:i + CHUNK] for i in range(0, len(payload), CHUNK)]

    out = [f"\x1b[{off_row};{off_col}H"]           # home to placement origin
    n = len(chunks)
    for idx, ch in enumerate(chunks):
        more = 1 if idx < n - 1 else 0
        if idx == 0:
            # a=T transmit+display, t=d inline, o=z zlib, f=24 RGB, z=-1 below
            # text, q=2 suppress ALL responses (mandatory under multi-client
            # attach — no N-terminal response storm), C=1 keep cursor put.
            ctrl = (f"a=T,i={img_id},p=1,z=-1,t=d,f=24,o=z,"
                    f"s={w},v={h},c={cols},r={rows},q=2,C=1,m={more}")
        else:
            ctrl = f"m={more}"
        apc = f"\x1b_G{ctrl};{ch.decode('ascii')}\x1b\\"
        out.append(_tmux_wrap(apc) if in_tmux else apc)
    return "".join(out)


def blit_direct(term, rgb: bytes, w: int, h: int, cols: int, rows: int,
                img_id: int, off_row: int = 1, off_col: int = 1,
                in_tmux: bool = False) -> int:
    """Emit one inline (t=d) frame to `term`. Returns bytes written (wire size)."""
    esc = build_direct(rgb, w, h, cols, rows, img_id, off_row, off_col, in_tmux)
    if esc:
        term.write(esc)
    return len(esc)


# ── damage (tiled/partial) updates ───────────────────────────────────────────
# Instead of retransmitting a whole frame when a few pixels change, callers can
# diff consecutive frames into a changed row band and edit just that rectangle
# of the already-displayed image via the kitty ANIMATION protocol: a=f with
# r=1 edits the root frame (the base image data) in place, and kitty re-uploads
# and repaints immediately when the edited frame is the current one — which for
# a still image it always is. One a=f edit costs the terminal a full-frame
# recompose regardless of rect size (only the WIRE scales with the rect), so
# damage is coalesced into a single band per frame, never many small tiles.
# a=f cannot grow an image: after a resize the caller must send a full a=T
# frame first and only then resume band edits.

# Coarse row-scan chunk: first find the changed region in CHUNK-row steps
# (C-speed slice compares), then refine to exact rows only inside the two
# boundary chunks. Keeps the Python-loop iteration count ~h/32 + 64.
_BAND_CHUNK = 32


def diff_band(prev, cur, w: int, h: int):
    """Changed row band between two same-size w*h RGB frames.

    Returns (y0, band_h), or None when the frames are identical. Full width is
    assumed dirty (column bounds are not computed: for pane/desktop content the
    damage is usually a text row, cursor, or window band, and one a=f edit per
    frame costs the terminal a full recompose anyway — only wire size varies).
    """
    if prev is cur:
        return None
    n = w * 3 * h
    if len(prev) != n or len(cur) != n:
        return 0, h                      # size mismatch: treat as fully dirty
    # NOTE: sliced-bytes compares throughout — bytes==bytes is a C memcmp,
    # while memoryview equality falls back to per-element Python comparison
    # (measured ~10x slower on frame-sized buffers).
    p = prev if isinstance(prev, bytes) else bytes(prev)
    c = cur if isinstance(cur, bytes) else bytes(cur)
    if p == c:                           # one whole-frame memcmp: idle fast path
        return None
    stride = w * 3
    step = _BAND_CHUNK * stride

    first_chunk = -1
    for i, off in enumerate(range(0, n, step)):
        if p[off:off + step] != c[off:off + step]:
            first_chunk = i
            break
    last_chunk = first_chunk
    for i in range((n - 1) // step, first_chunk - 1, -1):
        off = i * step
        if p[off:off + step] != c[off:off + step]:
            last_chunk = i
            break

    y0 = first_chunk * _BAND_CHUNK
    for y in range(y0, min(h, y0 + _BAND_CHUNK)):
        off = y * stride
        if p[off:off + stride] != c[off:off + stride]:
            y0 = y
            break
    # bottom edge: the last differing row lives inside the last dirty chunk
    # (or, when first==last chunk, anywhere down to y0) — scan at most one
    # chunk of rows and never past y0. Fall back to y0 (a change exists there).
    y1_end = min(h, (last_chunk + 1) * _BAND_CHUNK) - 1
    y1 = y0
    for y in range(y1_end, max(y0 - 1, y1_end - _BAND_CHUNK), -1):
        off = y * stride
        if p[off:off + stride] != c[off:off + stride]:
            y1 = y
            break
    return y0, y1 - y0 + 1


def build_frame_edit(rgb: bytes, w: int, h: int, x: int, y: int,
                     img_id: int, in_tmux: bool = False) -> str:
    """Escape sequence that inline-transmits (t=d,o=z) a w*h RGB rect and
    composes it onto the displayed image at pixel (x, y) via a=f,r=1.

    The rect must lie fully inside the image (a=f cannot grow it). No cursor
    movement and no placement keys: frame edits repaint the existing placement
    in place. Every continuation chunk repeats a=f AND i=/r=1: kitty computes
    the target frame from each chunk's own keys BEFORE restoring the saved
    start command, so a continuation without r=1 silently APPENDS a stray
    animation frame instead of editing the displayed root frame (verified
    against this fork's graphics.c; kitty's own test suite repeats the keys).
    """
    if not rgb:
        return ""
    comp = zlib.compress(rgb, 1)
    payload = base64.b64encode(comp)
    chunks = [payload[i:i + CHUNK] for i in range(0, len(payload), CHUNK)]
    out = []
    n = len(chunks)
    for idx, ch in enumerate(chunks):
        more = 1 if idx < n - 1 else 0
        if idx == 0:
            ctrl = (f"a=f,i={img_id},r=1,x={x},y={y},t=d,f=24,o=z,"
                    f"s={w},v={h},q=2,m={more}")
        else:
            ctrl = f"a=f,i={img_id},r=1,q=2,m={more}"
        apc = f"\x1b_G{ctrl};{ch.decode('ascii')}\x1b\\"
        out.append(_tmux_wrap(apc) if in_tmux else apc)
    return "".join(out)


def blit_frame_edit(term, rgb: bytes, w: int, h: int, x: int, y: int,
                    img_id: int, in_tmux: bool = False) -> int:
    """Emit one inline (t=d) partial-frame edit. Returns bytes written."""
    esc = build_frame_edit(rgb, w, h, x, y, img_id, in_tmux)
    if esc:
        term.write(esc)
    return len(esc)


def build_frame_edit_file(path: str, w: int, h: int, x: int, y: int,
                          img_id: int) -> str:
    """Escape sequence that composes a w*h RGB rect read from `path` (t=t)
    onto the displayed image at pixel (x, y) via a=f,r=1 — the fast LOCAL
    partial-update path. kitty deletes the file after reading when its name
    contains 'tty-graphics-protocol' (the caller should name it so)."""
    payload = base64.b64encode(path.encode()).decode()
    return (f"\x1b_Ga=f,i={img_id},r=1,x={x},y={y},t=t,f=24,"
            f"s={w},v={h},q=2;{payload}\x1b\\")
