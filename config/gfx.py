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
