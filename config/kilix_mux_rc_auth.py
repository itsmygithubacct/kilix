"""Authorize only pane-scoped byte input for ``kilix remote``."""

from __future__ import annotations

import base64
import binascii
import re


_PANE = re.compile(
    r"env:KITTY_PTY_BROKER_SESSION=([0-9a-f]{16,64})\Z")


def is_cmd_allowed(pcmd, window, from_socket, extra_data):
    del window, from_socket, extra_data
    if pcmd.get("cmd") != "send-text":
        return None
    payload = pcmd.get("payload")
    if not isinstance(payload, dict):
        return False
    if not _PANE.fullmatch(str(payload.get("match") or "")):
        return False
    if payload.get("match_tab") or payload.get("all") \
            or payload.get("exclude_active") or payload.get("session_id"):
        return False
    if payload.get("bracketed_paste") not in (None, "", "disable"):
        return False
    data = payload.get("data")
    if not isinstance(data, str) or not data.startswith("base64:"):
        return False
    encoded = data[7:]
    if len(encoded) > 1400:
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return False
    return len(decoded) <= 1024
