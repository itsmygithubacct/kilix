"""Kitty keyboard-protocol functional keys shared by Kilix input paths.

With the disambiguate and all-keys flags, kitty reports the numeric keypad
as functional codes in the private-use range (57399-57427). It does not
fold them into digits or arrows. The private X server's NumLock stays off,
so the digit and the arrow are two levels of one key. Callers that inject
or forward a key use this table. The terminal parser keeps the raw code
text-less so a private-use character cannot leak into a page.
"""

from typing import NamedTuple


class KeypadKey(NamedTuple):
    """One keypad key: the X keysym name, and the DOM key Chromium expects."""

    x_name: str
    key: str
    code: str
    vk: int
    text: str


def _digits():
    out = {}
    for i in range(10):
        out[57399 + i] = KeypadKey(
            f"KP_{i}", str(i), f"Numpad{i}", 96 + i, str(i))
    return out


# Kitty functional numbers are the ones in kitty's key_encoding map.
# X names are XK_KP_* without the XK_ prefix. vk is the Windows virtual
# key Chromium's CDP path uses for that DOM code. Navigation entries are
# the NumLock-off identity of the same physical key, so they stay text-less.
KEYPAD = {
    **_digits(),
    57409: KeypadKey("KP_Decimal", ".", "NumpadDecimal", 110, "."),
    57410: KeypadKey("KP_Divide", "/", "NumpadDivide", 111, "/"),
    57411: KeypadKey("KP_Multiply", "*", "NumpadMultiply", 106, "*"),
    57412: KeypadKey("KP_Subtract", "-", "NumpadSubtract", 109, "-"),
    57413: KeypadKey("KP_Add", "+", "NumpadAdd", 107, "+"),
    57414: KeypadKey("KP_Enter", "Enter", "NumpadEnter", 13, "\r"),
    57415: KeypadKey("KP_Equal", "=", "NumpadEqual", 187, "="),
    57416: KeypadKey("KP_Separator", ",", "NumpadComma", 108, ","),
    57417: KeypadKey("KP_Left", "ArrowLeft", "Numpad4", 37, ""),
    57418: KeypadKey("KP_Right", "ArrowRight", "Numpad6", 39, ""),
    57419: KeypadKey("KP_Up", "ArrowUp", "Numpad8", 38, ""),
    57420: KeypadKey("KP_Down", "ArrowDown", "Numpad2", 40, ""),
    57421: KeypadKey("KP_Page_Up", "PageUp", "Numpad9", 33, ""),
    57422: KeypadKey("KP_Page_Down", "PageDown", "Numpad3", 34, ""),
    57423: KeypadKey("KP_Home", "Home", "Numpad7", 36, ""),
    57424: KeypadKey("KP_End", "End", "Numpad1", 35, ""),
    57425: KeypadKey("KP_Insert", "Insert", "Numpad0", 45, ""),
    57426: KeypadKey("KP_Delete", "Delete", "NumpadDecimal", 46, ""),
    57427: KeypadKey("KP_Begin", "Clear", "Numpad5", 12, ""),
}


def x_keysym_name(number):
    """X keysym name for a kitty functional number, or None."""
    spec = KEYPAD.get(number)
    return spec.x_name if spec else None


def translate_key_event(ev):
    """Copy *ev* with a keypad functional code rewritten as a real key.

    A non-keypad event is returned unchanged, so callers can keep the
    parser's text-less private-use character until this point. The raw
    code is never forwarded as text.
    """
    if not isinstance(ev, dict):
        return ev
    key = ev.get("key")
    if not isinstance(key, str) or len(key) != 1:
        return ev
    spec = KEYPAD.get(ord(key))
    if spec is None:
        return ev
    out = dict(ev)
    out["key"] = spec.key
    out["code"] = spec.code
    out["vk"] = spec.vk
    out["text"] = spec.text
    return out
