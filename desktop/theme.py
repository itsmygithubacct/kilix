"""kilix desktop — the Windows-95 look: palette, metrics, fonts, bevels.

Everything visual that is shared between modules lives here so the look can
be tuned in one place. Colors are the classic 16-color Windows 95 system
palette; edges are the two-pixel BDR_RAISED / BDR_SUNKEN bevels that give
that UI its depth. Text is rendered with PIL in binary (non-antialiased)
mode for the crisp bitmap-font feel.

Fonts: real MS fonts are never bundled (kilix is published); if you own them,
drop .ttf files into desktop/assets/fonts/ (gitignored) and they are picked
up by preference. Otherwise DejaVu Sans at 11 px is a close enough stand-in.
"""
import os

from PIL import ImageFont

# ── the Windows 95 system palette ───────────────────────────────────────────
FACE       = (192, 192, 192)   # 3D face (buttons, dialogs, menus)
LIGHT      = (255, 255, 255)   # 3D hilight (top/left of raised edges)
LTGRAY     = (223, 223, 223)   # 3D light (inner raised edge)
SHADOW     = (128, 128, 128)   # 3D shadow (bottom/right inner)
DKSHADOW   = (0, 0, 0)         # 3D dark shadow (bottom/right outer)
DESKTOP    = (0, 128, 128)     # the teal
TITLE_A    = (0, 0, 128)       # active title bar
TITLE_A_TX = (255, 255, 255)
TITLE_I    = (128, 128, 128)   # inactive title bar
TITLE_I_TX = (192, 192, 192)
WINDOW_BG  = (255, 255, 255)   # text fields, lists
TEXT       = (0, 0, 0)
DISABLED   = (128, 128, 128)
SEL_BG     = (0, 0, 128)       # selection / menu highlight
SEL_TX     = (255, 255, 255)
INFO_BG    = (255, 255, 225)   # tooltips (unused yet, reserved)

# ── metrics ─────────────────────────────────────────────────────────────────
TITLE_H    = 18                # title bar height (inside the frame)
BORDER     = 4                 # resizable window frame thickness
MENU_H     = 19                # in-window menu bar height
TASKBAR_H  = 28
SCROLL_W   = 16                # scrollbar thickness
ICON       = 32                # large icon size (desktop, file manager)
ICON_S     = 16                # small icon size (title bars, menus, lists)
CELL_W, CELL_H = 75, 66        # desktop / file-manager icon grid cell

_here = os.path.dirname(os.path.abspath(__file__))


def _find_font(names, size):
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _candidates(bold):
    out = []
    assets = os.path.join(_here, "assets", "fonts")
    if os.path.isdir(assets):   # user-supplied fonts win (never committed)
        pref = [f for f in sorted(os.listdir(assets))
                if f.lower().endswith((".ttf", ".otf"))
                and (("bold" in f.lower()) == bold)]
        out += [os.path.join(assets, f) for f in pref]
    out += ["DejaVuSans-Bold.ttf"] if bold else ["DejaVuSans.ttf"]
    out += ["/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
            % ("-Bold" if bold else "")]
    return out


FONT   = _find_font(_candidates(False), 11)
BOLD   = _find_font(_candidates(True), 11)
SMALL  = _find_font(_candidates(False), 10)


def text_w(font, s):
    try:
        return int(font.getlength(s))
    except AttributeError:               # very old PIL
        return font.getsize(s)[0]


def ellipsize(font, s, max_w):
    if text_w(font, s) <= max_w:
        return s
    while s and text_w(font, s + "…") > max_w:
        s = s[:-1]
    return s + "…"


# ── bevels ──────────────────────────────────────────────────────────────────
# All take an ImageDraw and an inclusive pixel rect (x0, y0, x1, y1).

def _edge(d, x0, y0, x1, y1, tl, br):
    d.line([(x0, y1), (x0, y0), (x1, y0)], fill=tl)          # top + left
    d.line([(x1, y0 + 1), (x1, y1), (x0 + 1, y1)], fill=br)  # right + bottom


def raised(d, x0, y0, x1, y1, fill=FACE):
    """Button/taskbar face: white TL, black BR, gray inner BR."""
    if fill:
        d.rectangle([x0, y0, x1, y1], fill=fill)
    _edge(d, x0, y0, x1, y1, LIGHT, DKSHADOW)
    _edge(d, x0 + 1, y0 + 1, x1 - 1, y1 - 1, fill or FACE, SHADOW)


def raised_thin(d, x0, y0, x1, y1, fill=FACE):
    """One-pixel raised edge (taskbar top, toolbar buttons at rest)."""
    if fill:
        d.rectangle([x0, y0, x1, y1], fill=fill)
    _edge(d, x0, y0, x1, y1, LIGHT, SHADOW)


def pressed(d, x0, y0, x1, y1, fill=FACE):
    """A pushed-in button."""
    if fill:
        d.rectangle([x0, y0, x1, y1], fill=fill)
    _edge(d, x0, y0, x1, y1, DKSHADOW, LIGHT)
    _edge(d, x0 + 1, y0 + 1, x1 - 1, y1 - 1, SHADOW, fill or FACE)


def sunken(d, x0, y0, x1, y1, fill=WINDOW_BG):
    """Text fields, list boxes, the taskbar clock well."""
    if fill:
        d.rectangle([x0, y0, x1, y1], fill=fill)
    _edge(d, x0, y0, x1, y1, SHADOW, LIGHT)
    _edge(d, x0 + 1, y0 + 1, x1 - 1, y1 - 1, DKSHADOW, FACE)


def frame(d, x0, y0, x1, y1):
    """Window outer frame (the 2px 3D edge of the 4px border)."""
    _edge(d, x0, y0, x1, y1, LTGRAY, DKSHADOW)
    _edge(d, x0 + 1, y0 + 1, x1 - 1, y1 - 1, LIGHT, SHADOW)


def groove(d, x0, y0, x1, y1):
    """Etched-in group box / separator edge."""
    _edge(d, x0, y0, x1, y1, SHADOW, LIGHT)
    _edge(d, x0 + 1, y0 + 1, x1 - 1, y1 - 1, LIGHT, SHADOW)


def hsep(d, x0, x1, y):
    """Menu separator."""
    d.line([(x0, y), (x1, y)], fill=SHADOW)
    d.line([(x0, y + 1), (x1, y + 1)], fill=LIGHT)


def focus_rect(d, x0, y0, x1, y1, on=TEXT, off=FACE):
    """Dotted keyboard-focus marquee."""
    for x in range(x0, x1 + 1):
        d.point((x, y0), fill=on if (x + y0) % 2 == 0 else off)
        d.point((x, y1), fill=on if (x + y1) % 2 == 0 else off)
    for y in range(y0, y1 + 1):
        d.point((x0, y), fill=on if (x0 + y) % 2 == 0 else off)
        d.point((x1, y), fill=on if (x1 + y) % 2 == 0 else off)
