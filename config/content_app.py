#!/usr/bin/env python3
"""Install and launch catalog applications on Kilix presentation surfaces.

The content catalog owns identity, source, build, executable, launch mode, and
preferred geometry. Desktops choose only a presentation surface:

``run``
    Replace the current process. A Kilix tab/pane host uses this verb after it
    has created the pane.
``window``
    Give terminal applications a PTY in an X terminal window. Native X
    applications are executed directly on the current display.

Keeping installation here means IceWM, Kilix 95, Cap, Land, TUI, and the CLI
all resolve the same immutable catalog entry instead of growing provider-local
pins and build rules.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from kilix_sdk import content
from kilix_sdk import paths

_INSTALLABLE_SOURCES = frozenset(("git", "archive"))


def apps_root() -> str:
    """Directory shared by every catalog application launch surface."""
    return os.path.join(paths.data_dir(), "desktop-apps")


def _report(content_id: str, message: str) -> None:
    print(f"kilix app {content_id}: {message}", file=sys.stderr)


def application_spec(content_id: str):
    spec = content.default_catalog().require(content_id)
    if spec.kind != "app":
        raise content.CatalogError(
            f"{content_id} is {spec.kind!r} content, not an application"
        )
    return spec


def _enabled(value: str) -> bool:
    return value.strip().casefold() in {"1", "yes", "true", "on"}


def _auto_install_enabled(content_id: str) -> bool:
    value = os.environ.get("KILIX_APP_AUTO_INSTALL")
    if value is None and content_id == "kilix-pdf-conversion":
        value = os.environ.get("KILIX_PDF_AUTO_INSTALL")
    return _enabled("1" if value is None else value)


def ensure_application(spec, *, install: bool | None = None) -> str:
    if spec.source_type not in _INSTALLABLE_SOURCES:
        raise content.InstallError(
            f"{spec.content_id} uses a non-installable {spec.source_type} source"
        )
    installer = content.Installer(apps_root())
    ready = installer.ready(spec)
    if ready:
        return ready
    allowed = _auto_install_enabled(spec.content_id) if install is None else install
    if not allowed:
        compatibility = (
            " (or KILIX_PDF_AUTO_INSTALL=1)"
            if spec.content_id == "kilix-pdf-conversion"
            else ""
        )
        raise content.InstallError(
            f"not installed under {apps_root()}; set "
            f"KILIX_APP_AUTO_INSTALL=1{compatibility} to build it"
        )
    if spec.dependency_hint:
        _report(spec.content_id, spec.dependency_hint)
    return installer.ensure(
        spec, lambda message: _report(spec.content_id, message)
    )


def _xterm() -> str:
    configured = os.environ.get("KILIX_XTERM", "xterm")
    if os.path.isabs(configured):
        if os.path.isfile(configured) and os.access(configured, os.X_OK):
            return configured
        raise RuntimeError(f"configured X terminal is not executable: {configured}")
    if os.sep in configured:
        raise RuntimeError("KILIX_XTERM must be an absolute path or command name")
    executable = shutil.which(configured)
    if not executable:
        raise RuntimeError(
            "xterm is required to render terminal applications in a desktop window"
        )
    return executable


def window_argv(spec, executable: str, arguments: list[str]) -> list[str]:
    """Return the native window command for an already installed app."""
    if spec.launch_mode == "terminal":
        return [_xterm(), "-T", spec.label, "-e", executable, *arguments]
    return [executable, *arguments]


def _exec(argv: list[str], *, surface: str, content_id: str) -> None:
    environment = dict(os.environ)
    environment["KILIX_APP_ID"] = content_id
    environment["KILIX_APP_SURFACE"] = surface
    os.execvpe(argv[0], argv, environment)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kilix app",
        description="install or launch a pinned catalog application",
    )
    parser.add_argument("action", choices=("run", "window", "install", "ref"))
    parser.add_argument("content_id", help="application ID from the Kilix catalog")
    parser.add_argument(
        "arguments",
        nargs=argparse.REMAINDER,
        help="arguments passed to the application after an optional --",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    forwarded = list(args.arguments)
    if forwarded[:1] == ["--"]:
        forwarded.pop(0)
    if args.action in {"install", "ref"} and forwarded:
        build_parser().error(f"{args.action} does not accept application arguments")

    try:
        spec = application_spec(args.content_id)
        if args.action == "ref":
            if not spec.ref:
                raise content.CatalogError(
                    f"{spec.content_id} has no immutable catalog ref"
                )
            print(spec.ref)
            return 0
        executable = ensure_application(spec)
        if args.action == "install":
            print(executable)
            return 0
        if args.action == "window":
            if not os.environ.get("DISPLAY"):
                raise RuntimeError("a DISPLAY is required for the window surface")
            command = window_argv(spec, executable, forwarded)
            _exec(command, surface="window", content_id=spec.content_id)
        _exec(
            [executable, *forwarded],
            surface="current",
            content_id=spec.content_id,
        )
    except (content.CatalogError, content.InstallError, OSError, RuntimeError) as error:
        _report(args.content_id, str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
