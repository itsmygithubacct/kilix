"""Terminal input and raw-mode helpers for Kilix-hosted applications."""

import browse as _browse

from . import keys as _keys

CSI_RE = _browse.CSI_RE
SPECIAL_CSI = _browse.SPECIAL_CSI
SPECIAL_TILDE = _browse.SPECIAL_TILDE
SPECIAL_U = _browse.SPECIAL_U
Term = _browse.Term
cdp_mods = _browse.cdp_mods
KEYPAD = _keys.KEYPAD
translate_key_event = _keys.translate_key_event

__all__ = [
    "CSI_RE",
    "KEYPAD",
    "SPECIAL_CSI",
    "SPECIAL_TILDE",
    "SPECIAL_U",
    "Term",
    "cdp_mods",
    "translate_key_event",
]
