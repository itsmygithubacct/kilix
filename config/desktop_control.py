#!/usr/bin/env python3
"""Delegate desktop actions to the selected shared Kilix desktop executor."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


HELP = """usage: kilix desktop-action [--display DISPLAY] [--json] [--yes|--parse-only] COMMAND ...

Commands: window list|focus|move|resize|minimize|maximize|restore|close,
          pointer move|click|double-click|drag, keyboard type|press,
          scroll up|down|left|right, wait MILLISECONDS, batch FILE, schema.
Use COMMAND --help for arguments. Preview is the default; --yes executes.
Targets are active, title:EXACT TITLE, or id:DECIMAL ID. Typing never adds Enter.

Examples:
  kilix desktop-action window list
  kilix desktop-action --yes window focus --target 'title:Notes'
  kilix desktop-action --yes keyboard type --text 'literal text'
  kilix desktop-action --yes keyboard press Return
  kilix desktop-action --yes pointer move --x 300 --y 200
  kilix desktop-action --yes batch actions.json

Requires kilix-desktop or kilix-needle on PATH. KILIX_DESKTOP_CLI selects an
executable; KILIX_DESKTOP_MODULE_ROOT selects its source directory. An explicit
selection is never replaced with another implementation. No automatic install.
Natural language: kilix-needle desktop --parser grammar|needle|hybrid REQUEST.
"""


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args in (["--help"], ["-h"], ["help"]):
        print(HELP, end="")
        return 0
    cli = os.environ.get("KILIX_DESKTOP_CLI")
    root = os.environ.get("KILIX_DESKTOP_MODULE_ROOT")
    if cli is not None:
        executable = shutil.which(cli)
        if executable:
            os.execv(executable, [executable, *args])
    elif root is not None:
        module = Path(root).expanduser().resolve() / "desktop_control.py"
        if module.is_file():
            os.execv(sys.executable, [sys.executable, "-B", str(module), *args])
    else:
        executable = shutil.which("kilix-desktop")
        if executable:
            os.execv(executable, [executable, *args])
        executable = shutil.which("kilix-needle")
        if executable:
            os.execv(executable, [executable, "desktop-action", *args])
    print("kilix desktop-action: selected desktop executor is unavailable; install kilix-needle or set KILIX_DESKTOP_MODULE_ROOT", file=sys.stderr)
    return 7


if __name__ == "__main__":
    raise SystemExit(main())
