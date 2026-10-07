#!/usr/bin/env python3
"""Helpers behind `kilix pty`: pane lookup and the attach check.

The launcher does the broker calls; this module only reads what they return.
It never talks to a broker.

    kilix_pty.py pane PANE_ID
    kilix_pty.py attached                      (broker status JSON on stdin)
"""
from __future__ import annotations

import json
import os
import re
import sys

SESSION_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def fail(message: str, code: int = 1) -> int:
    print(f"kilix pty: {message}", file=sys.stderr)
    return code


def cmd_pane(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].isdecimal():
        return fail("usage: kilix pty pane PANE_ID", 2)
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    try:
        from kilix_sdk import panes
        workspace = panes.snapshot(timeout=5.0)
    except Exception as error:  # the SDK raises its own PaneError family
        return fail(f"could not list panes: {error}")
    for pane in workspace.panes():
        if str(pane.id) == argv[0]:
            session = pane.broker_session or ""
            if not SESSION_ID.fullmatch(session) or session in {".", ".."}:
                return fail(f"pane {argv[0]} has no persistent session"
                            " (it is an overlay, or KILIX_PTY_BROKER=0)")
            print(session)
            return 0
    return fail(f"no kilix pane with id {argv[0]}")


def cmd_attached(argv: list[str]) -> int:
    """Exit 0 attached, 1 detached, 2 not a status document."""
    if argv:
        return 2
    try:
        document = json.load(sys.stdin)
        return 0 if document["attached"] is True else 1 if document["attached"] is False else 2
    except (ValueError, KeyError, TypeError):
        return 2


def main(argv: list[str]) -> int:
    if argv and argv[0] == "pane":
        return cmd_pane(argv[1:])
    if argv and argv[0] == "attached":
        return cmd_attached(argv[1:])
    return fail("usage: kilix_pty.py pane|attached ...", 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
