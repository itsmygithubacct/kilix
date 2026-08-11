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
from pathlib import Path
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


def _exec(
    argv: list[str],
    *,
    surface: str,
    content_id: str,
    action: str = "",
) -> None:
    environment = dict(os.environ)
    environment["KILIX_APP_ID"] = content_id
    environment["KILIX_APP_SURFACE"] = surface
    if action:
        environment["KILIX_APP_ACTION"] = action
    os.execvpe(argv[0], argv, environment)


def _application_arguments(spec, forwarded: list[str]) -> tuple[str, list[str]]:
    """Resolve a named catalog action to trusted fixed argv plus at most one input."""
    values = list(forwarded)
    if values[:1] != ["--action"]:
        if values[:1] == ["--"]:
            values.pop(0)
        return "", values
    if len(values) < 2:
        raise ValueError("--action needs an action ID")
    action_id = values[1]
    inputs = values[2:]
    if inputs[:1] == ["--"]:
        inputs.pop(0)
    action = spec.require_action(action_id)
    if len(inputs) > int(action.accepts_input):
        expected = "at most one input" if action.accepts_input else "no input"
        raise ValueError(
            f"{spec.content_id} action {action_id!r} accepts {expected}"
        )
    return action_id, [*action.argv, *inputs]


def _system_command(spec) -> list[str]:
    command = list(getattr(spec, "command", ()))
    if not command:
        command = [spec.binary or spec.content_id]
    if command[0] == "kilix":
        command[0] = str(Path(__file__).resolve().parents[1] / "kilix")
    return command


def _command_ready(command: list[str]) -> str | None:
    executable = command[0]
    if os.path.isabs(executable):
        return (
            executable
            if os.path.isfile(executable) and os.access(executable, os.X_OK)
            else None
        )
    return shutil.which(executable)


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
        action_id, application_arguments = _application_arguments(spec, forwarded)
        if spec.source_type == "system":
            command = _system_command(spec)
            ready = _command_ready(command)
            if args.action == "install":
                if ready is None:
                    raise content.InstallError(
                        f"system command {command[0]!r} is not installed"
                    )
                print(ready)
                return 0
            command[0] = ready or command[0]
            command.extend(application_arguments)
            if args.action == "window":
                if not os.environ.get("DISPLAY"):
                    raise RuntimeError("a DISPLAY is required for the window surface")
                command = window_argv(spec, command[0], command[1:])
                _exec(command, surface="window", content_id=spec.content_id,
                      action=action_id)
                return 0
            _exec(command, surface="current", content_id=spec.content_id,
                  action=action_id)
            return 0
        if spec.source_type == "custom":
            plan = content.application_plan(
                spec.content_id,
                "window" if args.action == "window" else "current",
                application_arguments,
                launcher=str(Path(__file__).resolve().parents[1] / "kilix"),
            )
            _exec(list(plan.argv), surface=plan.surface,
                  content_id=spec.content_id, action=action_id)
            return 0
        executable = ensure_application(spec)
        if args.action == "install":
            print(executable)
            return 0
        if args.action == "window":
            if not os.environ.get("DISPLAY"):
                raise RuntimeError("a DISPLAY is required for the window surface")
            command = window_argv(spec, executable, application_arguments)
            _exec(command, surface="window", content_id=spec.content_id,
                  action=action_id)
            return 0
        _exec(
            [executable, *application_arguments],
            surface="current",
            content_id=spec.content_id,
            action=action_id,
        )
    except (
        content.CatalogError,
        content.InstallError,
        OSError,
        RuntimeError,
        ValueError,
    ) as error:
        _report(args.content_id, str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
