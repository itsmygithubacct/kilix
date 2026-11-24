#!/usr/bin/env python3
"""kilix content: maintenance of installed content trees.

  kilix content set-aside PATH   move an installed tree aside (kept, not deleted)
                                 so the same install can run again

Replacing an installed tree needs an atomic directory exchange, which FreeBSD
does not have; there a reinstall or repair stops and names this command.
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _content_api():
    # The SDK selects the pinned host package before kilix_content is imported.
    from kilix_sdk import content  # noqa: F401
    import kilix_content

    return kilix_content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kilix content",
        description="Maintain installed content trees (apps, games, models).")
    commands = parser.add_subparsers(dest="command", required=True)
    aside = commands.add_parser(
        "set-aside",
        help="move an installed tree aside so the same install can run again",
        description="Move the installed tree at PATH to a hidden sibling, under its "
                    "install lock. Nothing is deleted; the new location is printed.")
    aside.add_argument("path", help="the installed tree, as an install error names it")
    arguments = parser.parse_args(argv)

    api = _content_api()
    set_aside = getattr(api, "set_aside", None)
    if set_aside is None:
        print("kilix content: the pinned Content component has no set-aside; update it",
              file=sys.stderr)
        return 1
    try:
        moved = set_aside(arguments.path)
    except api.InstallError as error:
        print(f"kilix content: {error}", file=sys.stderr)
        return 1
    print(f"set aside: {os.path.abspath(arguments.path)} -> {moved}")
    print("Run the same install again. Once the new tree works, delete the old one:")
    print(f"  rm -rf -- {shlex.quote(moved)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
