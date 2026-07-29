#!/usr/bin/env python3
"""Live kilix/kitty remote-control helpers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any


KITTEN = os.environ.get("KILIX_KITTEN", "kitten")
RC_PASSWORD_FILE = os.environ.get("KILIX_RC_PASSWORD_FILE", "")


def fail(command: str, message: str, code: int = 1) -> int:
    print(f"kilix {command}: {message}", file=sys.stderr)
    return code


def run_kitten(
    args: list[str], *, authenticated: bool = False, via_tty: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [KITTEN, "@"]
    if authenticated and RC_PASSWORD_FILE:
        command.extend(["--password-file", RC_PASSWORD_FILE])
    env = None
    if via_tty:
        env = os.environ.copy()
        env.pop("KITTY_LISTEN_ON", None)
    return subprocess.run(
        [*command, *args],
        check=False,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def load_state(command: str) -> list[dict[str, Any]]:
    proc = run_kitten(["ls"], authenticated=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"kitten exited {proc.returncode}"
        raise RuntimeError(f"could not query live kilix tabs via KITTY_LISTEN_ON={os.environ.get('KITTY_LISTEN_ON', '')}: {detail}")
    try:
        state = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"could not parse kitty state: {exc}") from exc
    if not isinstance(state, list):
        raise RuntimeError("kitty returned an unexpected remote-control payload")
    return state


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("path") or value.get("cwd") or "")
    return str(value)


def focused_window(windows: list[dict[str, Any]]) -> dict[str, Any]:
    for window in windows:
        if window.get("is_focused") or window.get("is_active"):
            return window
    return windows[0] if windows else {}


def process_name(window: dict[str, Any]) -> str:
    for process in reversed(window.get("foreground_processes") or []):
        cmdline = process.get("cmdline") or []
        if cmdline:
            return os.path.basename(cmdline[0]) or cmdline[0]
    return ""


def tab_is_active(os_window: dict[str, Any], tab: dict[str, Any], windows: list[dict[str, Any]]) -> bool:
    if "is_active" in tab:
        return bool(tab.get("is_active"))
    return bool(os_window.get("is_focused")) and any(w.get("is_focused") for w in windows)


def truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


def print_table(rows: list[dict[str, str]], columns: list[tuple[str, str, int | None]]) -> None:
    widths: dict[str, int] = {}
    for key, label, limit in columns:
        value_width = max([len(row[key]) for row in rows] or [0])
        width = max(len(label), value_width)
        widths[key] = min(width, limit) if limit else width
    header = []
    for column_index, (key, label, _) in enumerate(columns):
        if column_index == len(columns) - 1:
            header.append(label)
        elif key in {"index", "tab_id", "pane_id", "os_id", "panes"}:
            header.append(f"{label:>{widths[key]}}")
        else:
            header.append(f"{label:<{widths[key]}}")
    print("  ".join(header))
    for row in rows:
        cells = []
        for column_index, (key, _, _) in enumerate(columns):
            value = truncate(row[key], widths[key])
            if column_index == len(columns) - 1:
                cells.append(value)
            elif key in {"index", "tab_id", "pane_id", "os_id", "panes"}:
                cells.append(f"{value:>{widths[key]}}")
            else:
                cells.append(f"{value:<{widths[key]}}")
        print("  ".join(cells))


def iter_tabs(state: list[dict[str, Any]]):
    for os_index, os_window in enumerate(state, 1):
        os_id = os_window.get("id") or os_window.get("os_window_id") or os_index
        for tab in os_window.get("tabs") or []:
            windows = tab.get("windows") or []
            yield os_window, str(os_id), tab, windows


def cmd_ls(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kilix ls", description="List live kilix tabs or panes")
    parser.add_argument("--panes", "-p", action="store_true", help="list individual panes/windows instead of tabs")
    ns = parser.parse_args(argv)
    try:
        state = load_state("ls")
    except RuntimeError as exc:
        return fail("ls", str(exc))

    if ns.panes:
        rows = []
        for os_window, os_id, tab, windows in iter_tabs(state):
            active_tab = tab_is_active(os_window, tab, windows)
            tab_id = str(tab.get("id") or "?")
            for window in windows:
                active_pane = active_tab and bool(window.get("is_focused") or window.get("is_active"))
                rows.append(
                    {
                        "active": "*" if active_pane else " ",
                        "index": str(len(rows) + 1),
                        "pane_id": str(window.get("id") or "?"),
                        "tab_id": tab_id,
                        "os_id": os_id,
                        "title": text(window.get("title")) or process_name(window) or "(untitled)",
                        "proc": process_name(window),
                        "cwd": text(window.get("cwd")),
                    }
                )
        if not rows:
            print("kilix ls: no panes")
            return 0
        print_table(
            rows,
            [
                ("active", "ACT", None),
                ("index", "#", None),
                ("pane_id", "PANE_ID", None),
                ("tab_id", "TAB_ID", None),
                ("os_id", "OSWIN", None),
                ("title", "TITLE", 40),
                ("proc", "PROC", 18),
                ("cwd", "CWD", None),
            ],
        )
        return 0

    rows = []
    for os_window, os_id, tab, windows in iter_tabs(state):
        window = focused_window(windows)
        title = text(tab.get("title")) or text(window.get("title")) or process_name(window) or "(untitled)"
        rows.append(
            {
                "active": "*" if tab_is_active(os_window, tab, windows) else " ",
                "index": str(len(rows) + 1),
                "tab_id": str(tab.get("id") or "?"),
                "os_id": os_id,
                "panes": str(len(windows)),
                "title": title,
                "cwd": text(window.get("cwd")),
            }
        )
    if not rows:
        print("kilix ls: no tabs")
        return 0
    print_table(
        rows,
        [
            ("active", "ACT", None),
            ("index", "#", None),
            ("tab_id", "TAB_ID", None),
            ("os_id", "OSWIN", None),
            ("panes", "PANES", None),
            ("title", "TITLE", 40),
            ("cwd", "CWD", None),
        ],
    )
    return 0


def normalize_target(raw: str) -> tuple[str | None, str]:
    if ":" not in raw:
        return None, raw
    kind, value = raw.split(":", 1)
    kind = kind.lower()
    if kind in {"pane", "window", "win"}:
        return "pane", value
    if kind in {"tab", "page", "session"}:
        return "tab", value
    return None, raw


def resolve_target(command: str, raw: str, state: list[dict[str, Any]]) -> tuple[str, str]:
    kind, target_id = normalize_target(raw)
    if not target_id:
        raise RuntimeError("missing ID")
    tab_ids = {str(tab.get("id")) for _, _, tab, _ in iter_tabs(state) if tab.get("id") is not None}
    pane_ids = {
        str(window.get("id"))
        for _, _, _, windows in iter_tabs(state)
        for window in windows
        if window.get("id") is not None
    }
    if kind == "tab":
        if target_id not in tab_ids:
            raise RuntimeError(f"no live tab with id {target_id}")
        return "tab", target_id
    if kind == "pane":
        if target_id not in pane_ids:
            raise RuntimeError(f"no live pane with id {target_id}")
        return "pane", target_id
    in_tabs = target_id in tab_ids
    in_panes = target_id in pane_ids
    if in_tabs and in_panes:
        raise RuntimeError(f"id {target_id} is ambiguous; use tab:{target_id} or pane:{target_id}")
    if in_tabs:
        return "tab", target_id
    if in_panes:
        return "pane", target_id
    raise RuntimeError(f"no live tab or pane with id {target_id}; run 'kilix ls --panes'")


def cmd_focus(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kilix focus", description="Focus a live kilix tab or pane")
    parser.add_argument("target", help="tab ID, pane ID, tab:<id>, or pane:<id>")
    ns = parser.parse_args(argv)
    try:
        state = load_state("focus")
        kind, target_id = resolve_target("focus", ns.target, state)
    except RuntimeError as exc:
        return fail("focus", str(exc))
    if kind == "tab":
        proc = run_kitten(
            ["focus-tab", "--match", f"id:{target_id}"], authenticated=True,
        )
    else:
        proc = run_kitten(
            ["focus-window", "--match", f"id:{target_id}"], authenticated=True,
        )
    if proc.returncode != 0:
        return fail("focus", proc.stderr.strip() or f"kitten exited {proc.returncode}")
    print(f"kilix focus: focused {kind} {target_id}")
    return 0


def cmd_watch(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kilix watch", description="Best-effort read-only text watch of a live pane")
    parser.add_argument("--interval", "-n", type=float, default=1.0, help="poll interval in seconds")
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    parser.add_argument("--extent", choices=["screen", "all"], default="screen", help="text extent to read")
    parser.add_argument("--plain", action="store_true", help="omit ANSI styling from the watched pane")
    parser.add_argument("pane_id", help="pane ID from 'kilix ls --panes' or pane:<id>")
    ns = parser.parse_args(argv)
    if ns.interval <= 0:
        return fail("watch", "--interval must be greater than zero", 2)
    try:
        state = load_state("watch")
        kind, pane_id = resolve_target("watch", ns.pane_id, state)
    except RuntimeError as exc:
        return fail("watch", str(exc))
    if kind != "pane":
        return fail("watch", f"{ns.pane_id} is a tab; run 'kilix ls --panes' and watch a PANE_ID", 2)
    if pane_id == os.environ.get("KITTY_WINDOW_ID"):
        return fail("watch", "refusing to watch the current pane; open another pane first", 2)

    base_args = ["get-text", "--match", f"id:{pane_id}", "--extent", ns.extent]
    if not ns.plain:
        base_args.extend(["--ansi", "--add-cursor"])
    try:
        while True:
            proc = run_kitten(base_args, authenticated=True)
            if proc.returncode != 0:
                return fail("watch", proc.stderr.strip() or f"kitten exited {proc.returncode}")
            if not ns.once:
                sys.stdout.write("\033[H\033[2J\033[3J")
            sys.stdout.write(proc.stdout)
            if proc.stdout and not proc.stdout.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
            if ns.once:
                return 0
            time.sleep(ns.interval)
    except KeyboardInterrupt:
        return 130


# Where a new pane can be put, and the one place it cannot.
#
# kitty's splits layout treats the split axis and which side the new window
# lands on as two independent choices, but `launch --location` only names three
# of the four combinations: `vsplit` is always the far side of the horizontal
# axis, `hsplit` always the far side of the vertical, and `before` flips to the
# near side of whichever axis the *layout* defaults to. Kilix enables `splits`
# first and sets no `split_axis`, so that default is horizontal and `before`
# lands on the left. There is no spelling for the near side of the vertical
# axis, so "up" has to be refused rather than quietly given as "down".
PANE_LOCATIONS = {"right": "vsplit", "down": "hsplit", "left": "before"}


def _launch(kind: str, argv: list[str], extra: list[str]) -> int:
    """Run one authenticated `launch` and report the id it hands back."""
    proc = run_kitten(["launch", *extra], authenticated=True)
    if proc.returncode != 0:
        return fail(kind, proc.stderr.strip() or f"kitten exited {proc.returncode}")
    new_id = proc.stdout.strip()
    print(f"kilix {kind}: opened {new_id}" if new_id else f"kilix {kind}: opened")
    return 0


def cmd_new_pane(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kilix new-pane",
        description="Open a pane beside this one",
        epilog="Direction is relative to the pane the command runs in, not to "
               "whichever pane currently has focus.",
    )
    parser.add_argument(
        "direction", nargs="?", default="right",
        choices=["left", "right", "up", "down"])
    parser.add_argument(
        "--cwd", default="current",
        help="directory for the new pane (default: follow this one)")
    parser.add_argument(
        "command", nargs=argparse.REMAINDER,
        help="program to run in it (default: the shell)")
    ns = parser.parse_args(argv)

    location = PANE_LOCATIONS.get(ns.direction)
    if location is None:
        return fail(
            "new-pane",
            "the terminal cannot place a pane above this one: kitty's launch "
            "has no location for it. Use 'kilix new-pane down' and then "
            "Ctrl+Alt+Up to move it", 2)

    # --self anchors the split to the pane this command was typed in. Without
    # it the split hangs off whichever pane has focus, so running this from a
    # background pane would put the new one somewhere else entirely.
    extra = ["--type=window", f"--location={location}", "--cwd", ns.cwd, "--self"]
    command = ns.command[1:] if ns.command[:1] == ["--"] else ns.command
    if command:
        extra.append("--")
        extra.extend(command)
    return _launch("new-pane", argv, extra)


def cmd_new_tab(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kilix new-tab", description="Open a new page (tab)")
    parser.add_argument(
        "--cwd", default="current",
        help="directory for the new page (default: follow this pane)")
    parser.add_argument("--title", default="", help="name the page")
    parser.add_argument(
        "command", nargs=argparse.REMAINDER,
        help="program to run in it (default: the shell)")
    ns = parser.parse_args(argv)

    extra = ["--type=tab", "--cwd", ns.cwd, "--self"]
    if ns.title:
        extra.extend(["--tab-title", ns.title])
    command = ns.command[1:] if ns.command[:1] == ["--"] else ns.command
    if command:
        extra.append("--")
        extra.extend(command)
    return _launch("new-tab", argv, extra)


def cmd_fullscreen(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kilix fullscreen",
        description="Toggle content-only fullscreen for this tab's OS window",
    )
    parser.parse_args(argv)
    proc = run_kitten([
        "resize-os-window", "--self", "--action", "toggle-fullscreen",
    ], via_tty=True)
    if proc.returncode != 0:
        return fail(
            "fullscreen",
            proc.stderr.strip() or f"kitten exited {proc.returncode}",
        )
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        return fail("", "missing command", 2)
    command, rest = argv[0], argv[1:]
    if command == "ls":
        return cmd_ls(rest)
    if command == "focus":
        return cmd_focus(rest)
    if command == "watch":
        return cmd_watch(rest)
    if command == "fullscreen":
        return cmd_fullscreen(rest)
    if command in ("new-pane", "split"):
        return cmd_new_pane(rest)
    if command in ("new-tab", "new-page"):
        return cmd_new_tab(rest)
    return fail(command, "unknown remote-control command", 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
