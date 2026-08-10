#!/usr/bin/env python3
"""Prepare the exact PDF Conversion build this Kilix checkout pins.

PDF Conversion is catalog content, so its immutable source commit and build
command live in the pinned content catalog.  This small bridge keeps the CLI,
desktop catalog and provisioner on that one installer and one private data
directory instead of introducing a second pin or source cache.

`kilix pdf` installs on first use and opens the guided terminal interface.
`kilix pdf --install-only` lets an image provisioner prepare the same runtime
without launching it.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

HOST_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOST_ROOT / "config"))

from kilix_sdk import content as kilix_content  # noqa: E402
from kilix_sdk import paths  # noqa: E402

CONTENT_ID = "kilix-pdf-conversion"


def apps_root() -> str:
    """Use the catalog app directory shared by Kilix's launch surfaces."""
    return os.path.join(paths.data_dir(), "desktop-apps")


def report(message: str) -> None:
    """Keep stdout machine-readable: it carries only the executable path."""
    print(f"kilix pdf: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install-kilix-pdf.py",
        description="Install pinned PDF Conversion and print its executable.")
    parser.add_argument(
        "--print-ref", action="store_true",
        help="print the pinned catalog commit and change nothing")
    args = parser.parse_args(argv)

    try:
        spec = kilix_content.default_catalog().require(CONTENT_ID)
    except kilix_content.CatalogError as error:
        report(str(error))
        return 1

    if args.print_ref:
        print(spec.ref)
        return 0

    installer = kilix_content.Installer(apps_root())
    ready = installer.ready(spec)
    if ready:
        print(ready)
        return 0

    auto = os.environ.get("KILIX_PDF_AUTO_INSTALL", "1")
    if auto.lower() not in ("1", "yes", "true", "on"):
        report(f"not installed under {apps_root()}; "
               "set KILIX_PDF_AUTO_INSTALL=1 to build it")
        return 1

    if spec.dependency_hint:
        report(spec.dependency_hint)
    try:
        executable = installer.ensure(spec, report)
    except kilix_content.InstallError as error:
        report(str(error))
        return 1
    print(executable)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
