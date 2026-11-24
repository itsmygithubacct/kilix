#!/usr/bin/env python3
"""Locate the packaged Media Player or prepare a catalog-selected source build.

Explicit embedding roots and JSON catalog queries retain the pinned source
selection. A packaged CLI can launch the system player without a checkout.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

HOST_ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HOST_ROOT / "config"))

from kilix_sdk import content as kilix_content  # noqa: E402
from kilix_sdk._content_runtime import apps_root, normalized_root, packaged_amp  # noqa: E402

CONTENT_ID = "kilix-amp"


class _ReadOnlyInstaller(kilix_content.Installer):
    """Keep the existing readiness implementation without creating its root.

    Installer.__init__ calls _ensure_root; overriding only this construction
    hook leaves all source/binary checks with Content, including future fields.
    This class is used only for ready(), never ensure().
    """

    def _ensure_root(self):
        if not stat.S_ISDIR(os.stat(self.root, follow_symlinks=False).st_mode):
            raise kilix_content.InstallError("content root must be a real directory")


def selection(spec, root, executable):
    return {"id": spec.content_id, "root": root, "executable": executable,
            "ref": spec.ref, "build": list(spec.build)}


def report(message: str) -> None:
    """Progress goes to stderr; stdout carries the path and nothing else."""
    print(f"kilix amp: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install-kilix-amp.py",
        description="Install the pinned Kilix Amp and print its executable.")
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--print-ref", action="store_true",
        help="print the pinned catalog commit and change nothing")
    output.add_argument(
        "--print-root", action="store_true",
        help="print the normalized catalog root and change nothing")
    output.add_argument(
        "--resolve", action="store_true",
        help="print read-only JSON selection; never create, install or build")
    output.add_argument(
        "--json", action="store_true",
        help="prepare the selected app and return its executable/root as JSON")
    parser.add_argument(
        "--content-root", type=normalized_root,
        help="explicit embedding root (ordinary host commands use host storage)")
    args = parser.parse_args(argv)
    root = apps_root() if args.content_root is None else args.content_root

    if args.print_root:
        print(root)
        return 0

    if not (args.content_root or args.print_ref or args.resolve or args.json):
        try:
            executable = packaged_amp(HOST_ROOT)
        except FileNotFoundError as error:
            report(str(error))
            return 1
        if executable:
            print(executable)
            return 0

    try:
        spec = kilix_content.default_catalog().require(CONTENT_ID)
    except kilix_content.CatalogError as error:
        report(str(error))
        return 1

    if args.print_ref:
        print(spec.ref)
        return 0

    if args.resolve:
        try:
            ready = _ReadOnlyInstaller(
                root, env=dict(os.environ, GIT_ALLOW_PROTOCOL="file")).ready(spec)
        except FileNotFoundError:
            ready = None
        except (OSError, kilix_content.InstallError) as error:
            report(str(error))
            return 1
        print(json.dumps(selection(spec, root, ready)))
        return 0

    def selected(executable):
        print(json.dumps(selection(spec, root, executable)) if args.json else executable)

    installer = kilix_content.Installer(root)
    ready = installer.ready(spec)
    if ready:
        selected(ready)
        return 0

    auto = os.environ.get("KILIX_AMP_AUTO_INSTALL", "1")
    if auto.lower() not in ("1", "yes", "true", "on"):
        report(f"not installed under {root}; "
               "set KILIX_AMP_AUTO_INSTALL=1 to build it")
        return 1

    if spec.dependency_hint:
        report(spec.dependency_hint)
    try:
        executable = installer.ensure(spec, report)
    except kilix_content.InstallError as error:
        report(str(error))
        return 1
    selected(executable)
    return 0


if __name__ == "__main__":
    if "--resolve" in sys.argv[1:] or "--json" in sys.argv[1:]:
        # These embedding calls are cancellable owned processes. Content build
        # children create their own sessions, so a process-group kill alone
        # cannot discharge the caller's cleanup obligation.
        sys.path.insert(0, str(HOST_ROOT / "scripts"))
        from _amp_process import run_owned
        raise SystemExit(run_owned(main, timeout=5 if "--resolve" in sys.argv[1:] else 900))
    raise SystemExit(main())
