"""kilix desktop — the icon set, drawn in code.

No Microsoft artwork is bundled (kilix is published); every icon is original
pixel art in the Windows 95 16-color idiom. Icons are authored on a 16×16
grid and rendered at an integer scale (16 px for menus/title bars, 32 px for
the desktop and file manager) so they stay crisp and chunky at both sizes.

    icons.get("folder", 32)      -> cached RGBA Image
    icons.paint(img, "folder", x, y, 32[, shortcut=True])
"""
from PIL import Image, ImageDraw

# the 16-color palette (single-char keys used by the drawing code)
K = (0, 0, 0)
W = (255, 255, 255)
G = (128, 128, 128)
S = (192, 192, 192)
R = (255, 0, 0)
DR = (128, 0, 0)
Y = (255, 255, 0)
DY = (128, 128, 0)
B = (0, 0, 255)
DB = (0, 0, 128)
C = (0, 255, 255)
T = (0, 128, 128)
N = (0, 255, 0)
DN = (0, 128, 0)


class P:
    """A 16×16 logical canvas rendered at integer scale u (1 → 16px, 2 → 32px).
    Coordinates are inclusive, in 16-space; everything lands on whole device
    pixels so the art stays sharp at every size."""

    def __init__(self, u):
        self.u = u
        self.img = Image.new("RGBA", (16 * u, 16 * u), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def rect(self, x0, y0, x1, y1, fill=None, outline=None):
        u = self.u
        self.d.rectangle([x0 * u, y0 * u, (x1 + 1) * u - 1, (y1 + 1) * u - 1],
                         fill=fill, outline=outline, width=u)

    def px(self, x, y, c):
        self.rect(x, y, x, y, fill=c)

    def hline(self, x0, x1, y, c):
        self.rect(x0, y, x1, y, fill=c)

    def vline(self, x, y0, y1, c):
        self.rect(x, y0, x, y1, fill=c)

    def poly(self, pts, fill=None, outline=None):
        u = self.u
        # +0.5u centers vertices on logical pixels; good enough for our shapes
        sp = [((x + 0.5) * u, (y + 0.5) * u) for x, y in pts]
        self.d.polygon(sp, fill=fill, outline=outline, width=u)


# ── the icons (each draws into a P) ─────────────────────────────────────────

def _computer(p):
    p.rect(1, 1, 13, 10, fill=S, outline=K)        # monitor shell
    p.rect(3, 3, 11, 8, fill=T, outline=K)         # screen
    p.hline(4, 6, 4, C)                            # glint
    p.rect(6, 11, 8, 11, fill=S)                   # neck
    p.rect(2, 12, 12, 14, fill=S, outline=K)       # base
    p.hline(4, 8, 13, G)                           # drive slot
    p.px(11, 13, DN)                               # power led


def _display(p):
    _computer(p)
    p.rect(5, 4, 9, 7, fill=S, outline=K)          # a little window on screen
    p.hline(5, 9, 4, DB)


def _drive(p):
    p.rect(1, 5, 14, 11, fill=S, outline=K)
    p.hline(2, 13, 6, W)
    p.hline(3, 9, 9, G)                            # slot
    p.px(12, 9, DN)                                # led


def _folder(p, open_=False):
    p.rect(1, 3, 6, 4, fill=Y, outline=K)          # tab
    p.rect(1, 4, 14, 13, fill=Y, outline=K)        # body
    p.hline(2, 13, 5, W)                           # top hilight
    if open_:
        p.poly([(3, 7), (15, 7), (12, 13), (0, 13)], fill=Y, outline=K)
        p.hline(4, 13, 8, W)


def _folder_open(p):
    _folder(p, open_=True)


def _doc(p, deco=None):
    p.rect(3, 1, 12, 14, fill=W, outline=K)
    p.poly([(9, 1), (12, 4), (9, 4)], fill=S, outline=K)   # dog-ear
    if deco == "text":
        for y in (6, 8, 10, 12):
            p.hline(5, 10, y, B)
    elif deco == "image":
        p.rect(5, 5, 10, 11, fill=C, outline=K)
        p.poly([(5, 11), (7, 8), (10, 11)], fill=DN)       # hill
        p.px(9, 6, Y)                                      # sun


def _doc_text(p):
    _doc(p, "text")


def _doc_image(p):
    _doc(p, "image")


def _exe(p):
    p.rect(1, 2, 14, 13, fill=S, outline=K)
    p.rect(2, 3, 13, 4, fill=DB)                   # title bar
    p.px(13, 3, R)
    p.rect(4, 7, 8, 11, fill=W, outline=K)         # content + play arrow
    p.poly([(10, 7), (13, 9), (10, 11)], fill=DN, outline=K)


def _terminal(p):
    p.rect(1, 2, 14, 13, fill=S, outline=K)
    p.rect(2, 3, 13, 12, fill=K)
    p.hline(3, 4, 4, W)                            # C:\>
    p.px(6, 4, W)
    p.rect(8, 4, 9, 4, fill=S)                     # block cursor
    p.hline(3, 7, 6, G)                            # dim scrollback
    p.hline(3, 5, 8, G)


def _settings(p):
    p.rect(1, 2, 14, 13, fill=S, outline=K)
    p.hline(2, 13, 3, W)
    for i, (x, ky) in enumerate(((4, 6), (8, 10), (12, 8))):
        p.vline(x, 4, 12, G)                       # slider tracks
        p.rect(x - 1, ky - 1, x + 1, ky + 1, fill=S, outline=K)  # knobs
        p.px(x - 1, ky - 1, W)


def _notepad(p):
    p.rect(3, 2, 12, 14, fill=W, outline=K)
    for x in (5, 8, 11):                           # spiral binding
        p.px(x, 1, G)
        p.px(x, 2, K)
    for y in (5, 7, 9, 11):
        p.hline(5, 10, y, B)
    p.hline(5, 13, 13, R)                          # margin flourish


def _browser(p):
    p.rect(4, 1, 11, 1, fill=DB)                   # a chunky globe
    p.rect(2, 3, 13, 3, fill=DB)
    p.rect(1, 4, 14, 11, fill=DB)
    p.rect(2, 12, 13, 12, fill=DB)
    p.rect(4, 13, 11, 14, fill=DB)
    p.rect(3, 2, 12, 2, fill=DB)
    p.poly([(4, 3), (7, 3), (6, 6), (3, 6)], fill=DN)      # landmasses
    p.poly([(9, 5), (12, 4), (12, 8), (10, 9)], fill=DN)
    p.poly([(5, 8), (8, 9), (6, 12)], fill=DN)
    p.hline(1, 14, 7, C)                           # equator
    p.vline(7, 1, 14, C)                           # meridian


def _run(p):
    p.rect(1, 2, 14, 13, fill=S, outline=K)
    p.rect(2, 3, 13, 4, fill=DB)
    p.rect(3, 7, 10, 9, fill=W, outline=K)         # the Run field
    p.rect(4, 8, 6, 8, fill=DB)
    p.poly([(11, 10), (14, 12), (11, 14)], fill=DN, outline=K)


def _shutdown(p):
    p.rect(4, 2, 11, 3, fill=S, outline=K)
    p.rect(2, 4, 13, 13, fill=S, outline=K)        # the big red switch
    p.rect(4, 6, 11, 11, fill=R, outline=K)
    p.vline(7, 7, 9, W)
    p.px(8, 7, W)


def _flame(p):
    """kilix's flame — used for the Start button and About boxes."""
    p.poly([(7, 0), (11, 4), (13, 8), (12, 12), (9, 14), (5, 14), (2, 12),
            (2, 8), (4, 4)], fill=R, outline=DR)
    p.poly([(7, 4), (10, 8), (10, 11), (8, 13), (6, 13), (4, 11), (5, 7)],
           fill=Y)
    p.poly([(7, 8), (9, 11), (7, 13), (5, 11)], fill=W)


def _home(p):
    p.poly([(7, 1), (14, 8), (1, 8)], fill=DR, outline=K)  # roof
    p.rect(3, 8, 12, 14, fill=S, outline=K)
    p.rect(6, 10, 9, 14, fill=DY, outline=K)       # door
    p.rect(10, 10, 11, 11, fill=C, outline=K)      # window
    p.rect(11, 2, 12, 5, fill=DR, outline=K)       # chimney


def _question(p):
    p.rect(3, 1, 12, 2, fill=DB)
    p.rect(2, 3, 6, 5, fill=DB)
    p.rect(9, 3, 13, 6, fill=DB)
    p.rect(7, 7, 11, 8, fill=DB)
    p.rect(6, 9, 9, 10, fill=DB)
    p.rect(6, 13, 9, 14, fill=DB)


def _info(p):
    p.rect(4, 1, 11, 1, fill=W, outline=None)
    p.rect(2, 2, 13, 12, fill=W)
    p.rect(1, 4, 14, 10, fill=W)
    p.rect(4, 13, 11, 13, fill=W)
    p.rect(5, 14, 7, 14, fill=W)                   # speech tail
    p.rect(7, 3, 8, 4, fill=B)                     # the i
    p.rect(6, 6, 8, 6, fill=B)
    p.rect(7, 7, 8, 10, fill=B)
    p.rect(6, 11, 9, 11, fill=B)


def _warn(p):
    p.poly([(7, 0), (8, 0), (15, 14), (0, 14)], fill=Y, outline=K)
    p.rect(7, 4, 8, 9, fill=K)
    p.rect(7, 11, 8, 12, fill=K)


def _error(p):
    p.rect(4, 1, 11, 1, fill=R)
    p.rect(2, 2, 13, 3, fill=R)
    p.rect(1, 4, 14, 11, fill=R)
    p.rect(2, 12, 13, 13, fill=R)
    p.rect(4, 14, 11, 14, fill=R)
    for i in range(5):                             # the X
        p.rect(5 + i, 5 + i, 6 + i, 6 + i, fill=W)
        p.rect(9 - i, 5 + i, 10 - i, 6 + i, fill=W)


def _arrow(p, dx):
    if dx < 0:
        p.poly([(10, 2), (10, 13), (3, 7)], fill=K)
        p.rect(9, 6, 13, 9, fill=K)
    else:
        p.poly([(5, 2), (5, 13), (12, 7)], fill=K)
        p.rect(2, 6, 6, 9, fill=K)


def _arrow_left(p):
    _arrow(p, -1)


def _arrow_right(p):
    _arrow(p, +1)


def _games(p):
    p.rect(1, 5, 14, 10, fill=S, outline=K)        # a gamepad
    p.rect(0, 6, 0, 9, fill=S)
    p.rect(15, 6, 15, 9, fill=S)
    p.rect(3, 7, 6, 8, fill=K)                     # d-pad
    p.rect(4, 6, 5, 9, fill=K)
    p.px(11, 6, R)                                 # buttons
    p.px(12, 7, Y)
    p.px(10, 7, B)
    p.px(11, 8, DN)
    p.hline(2, 13, 5, W)


def _doom(p):
    # a cacodemon-ish grinning red sphere (all in-house pixel art)
    p.rect(4, 1, 11, 1, fill=DR)
    p.rect(2, 2, 13, 3, fill=R)
    p.rect(1, 4, 14, 11, fill=R)
    p.rect(2, 12, 13, 13, fill=R)
    p.rect(4, 14, 11, 14, fill=DR)
    p.rect(5, 3, 10, 3, fill=DR)                   # brow
    p.rect(5, 4, 10, 6, fill=W)                    # eye
    p.rect(7, 4, 8, 6, fill=DN)                    # green iris
    p.rect(3, 9, 12, 11, fill=DR)                  # mouth
    for x in (4, 6, 8, 10, 12):
        p.px(x, 9, W)                              # fangs
    for x in (5, 7, 9, 11):
        p.px(x, 11, W)


def _arrow_up(p):
    p.poly([(2, 9), (13, 9), (7, 2)], fill=Y, outline=K)   # explorer "up" folder
    p.rect(5, 9, 9, 13, fill=Y)
    p.vline(5, 9, 13, K)
    p.vline(9, 9, 13, K)
    p.hline(5, 9, 13, K)


ICONS = {
    "computer": _computer, "display": _display, "drive": _drive,
    "folder": _folder, "folder_open": _folder_open,
    "doc": _doc, "doc_text": _doc_text, "doc_image": _doc_image,
    "exe": _exe, "terminal": _terminal, "settings": _settings,
    "notepad": _notepad, "browser": _browser, "run": _run,
    "shutdown": _shutdown, "flame": _flame, "home": _home,
    "games": _games, "doom": _doom,
    "question": _question, "info": _info, "warn": _warn, "error": _error,
    "back": _arrow_left, "forward": _arrow_right, "up": _arrow_up,
}

_cache = {}


def get(name, size=32, shortcut=False):
    """Cached RGBA icon. Unknown names fall back to the blank document."""
    key = (name, size, shortcut)
    if key not in _cache:
        u = max(1, size // 16)
        p = P(u)
        ICONS.get(name, _doc)(p)
        img = p.img
        if img.size != (size, size):
            img = img.resize((size, size), Image.NEAREST)
        if shortcut:                              # little corner link-arrow
            o = P(u)
            o.rect(0, 9, 6, 15, fill=W, outline=K)
            o.poly([(2, 13), (5, 13), (5, 10)], fill=K)
            o.px(4, 12, K)
            img = img.copy()
            img.alpha_composite(o.img.resize((size, size), Image.NEAREST)
                                if o.img.size != (size, size) else o.img)
        _cache[key] = img
    return _cache[key]


def paint(dst, name, x, y, size=32, shortcut=False):
    """Paste icon `name` onto RGB image `dst` at (x, y)."""
    ic = get(name, size, shortcut)
    dst.paste(ic, (int(x), int(y)), ic)


def for_path(path, is_dir=False):
    """Pick an icon name for a filesystem path."""
    if is_dir:
        return "folder"
    low = path.lower()
    if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                     ".ico", ".ppm", ".tiff")):
        return "doc_image"
    if low.endswith((".txt", ".md", ".rst", ".log", ".conf", ".cfg", ".ini",
                     ".json", ".yaml", ".yml", ".toml", ".py", ".sh", ".c",
                     ".h", ".go", ".rs", ".js", ".ts", ".html", ".css")):
        return "doc_text"
    if low.endswith(".desktop"):
        return "exe"
    return "doc"
