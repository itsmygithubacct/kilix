#!/usr/bin/env python3
"""Prepare the exact Kilix Amp build this Kilix checkout pins.

Kilix Amp is catalog content, not a component with a ref of its own: its
commit lives in the pinned content catalog beside the games, and the shared
installer clones, verifies and builds it into Kilix's own data directory. So
this drives that installer rather than repeating a pin here, which is why it
is Python where the component installers next to it are shell.

The Media Player has always installed on first use from the desktop. This
gives the same install a name a caller can reach: `kilix amp` for a person,
`kilix amp --install-only` for a provisioner that wants it present before
anyone asks.

There is no force flag because there is nothing for one to do. The shared
installer treats a checkout whose origin or commit does not match the catalog
as not installed, so moving the catalog pin already rebuilds on the next call.
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

CONTENT_ID = "kilix-amp"


def apps_root() -> str:
    """Where Kilix 95 keeps installed catalog apps, so both paths agree."""
    return os.path.join(paths.data_dir(), "desktop-apps")


def report(message: str) -> None:
    """Progress goes to stderr; stdout carries the path and nothing else."""
    print(f"kilix amp: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install-kilix-amp.py",
        description="Install the pinned Kilix Amp and print its executable.")
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

    auto = os.environ.get("KILIX_AMP_AUTO_INSTALL", "1")
    if auto.lower() not in ("1", "yes", "true", "on"):
        report(f"not installed under {apps_root()}; "
               "set KILIX_AMP_AUTO_INSTALL=1 to build it")
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
