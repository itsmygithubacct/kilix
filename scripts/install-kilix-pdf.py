#!/usr/bin/env python3
"""Compatibility wrapper for Kilix's shared catalog application installer.

PDF Conversion is catalog content, so its immutable source commit and build
command live in the pinned content catalog.  This small bridge keeps the CLI,
desktop catalog and provisioner on that one installer and one private data
directory instead of introducing a second pin or source cache.

`kilix pdf` installs on first use and opens the guided terminal interface.
`kilix pdf --install-only` lets an image provisioner prepare the same runtime
without launching it.
"""
from __future__ import annotations

from pathlib import Path
import sys

HOST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST_ROOT / "config"))

import content_app  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    action = "ref" if arguments == ["--print-ref"] else "install"
    if arguments and action == "install":
        print("usage: install-kilix-pdf.py [--print-ref]", file=sys.stderr)
        return 2
    return content_app.main([action, "kilix-pdf-conversion"])


if __name__ == "__main__":
    raise SystemExit(main())
