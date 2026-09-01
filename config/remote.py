#!/usr/bin/env python3
"""Live kilix/kitty remote-control verbs: argument parsing and formatting.

Every operation in this file goes through :mod:`kilix_sdk.panes`.  Nothing
here calls ``kitten @`` directly, which is the rule that stops a sixth
reimplementation of ``kitten @ ls`` walking appearing in this file.  If a verb
needs something the library does not expose, the library grows -- it does not
get bypassed.

The import of the library is deliberately lazy: this module is loaded by tests
and by tooling that only wants its argument parsing, and a module-level import
would make the whole file unloadable wherever ``kilix_sdk.panes`` is not yet on
the path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any


def fail(command: str, message: str, code: int = 1) -> int:
    print(f"kilix {command}: {message}", file=sys.stderr)
    return code


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


def _rows_for_panes(workspace) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for tab in workspace.tabs():
        for pane in tab.panes:
            rows.append({
                "active": "*" if (tab.is_active and pane.is_focused) else " ",
                "index": str(len(rows) + 1),
                "pane_id": str(pane.id),
                "tab_id": str(tab.id),
                "os_id": str(tab.os_window_id),
                "title": pane.title or pane.process or "(untitled)",
                "proc": pane.process,
                "cwd": pane.cwd,
            })
    return rows


def _rows_for_tabs(workspace) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for tab in workspace.tabs():
        focused = next((p for p in tab.panes if p.is_focused), None)
        if focused is None:
            focused = tab.panes[0] if tab.panes else None
        title = tab.title or (focused.title if focused else "") \
            or (focused.process if focused else "") or "(untitled)"
        rows.append({
            "active": "*" if tab.is_active else " ",
            "index": str(len(rows) + 1),
            "tab_id": str(tab.id),
            "os_id": str(tab.os_window_id),
            "panes": str(len(tab.panes)),
            "title": title,
            "cwd": focused.cwd if focused else "",
        })
    return rows


PANE_COLUMNS = [
    ("active", "ACT", None),
    ("index", "#", None),
    ("pane_id", "PANE_ID", None),
    ("tab_id", "TAB_ID", None),
    ("os_id", "OSWIN", None),
    ("title", "TITLE", 40),
    ("proc", "PROC", 18),
    ("cwd", "CWD", None),
]

TAB_COLUMNS = [
    ("active", "ACT", None),
    ("index", "#", None),
    ("tab_id", "TAB_ID", None),
    ("os_id", "OSWIN", None),
    ("panes", "PANES", None),
    ("title", "TITLE", 40),
    ("cwd", "CWD", None),
]


def cmd_ls(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kilix ls", description="List live kilix tabs or panes")
    parser.add_argument("--panes", "-p", action="store_true", help="list individual panes/windows instead of tabs")
    ns = parser.parse_args(argv)
    panes = load_panes("ls")
    try:
        workspace = panes.snapshot()
    except panes.PaneError as exc:
        return fail("ls", str(exc))

    rows = _rows_for_panes(workspace) if ns.panes else _rows_for_tabs(workspace)
    if not rows:
        print("kilix ls: no panes" if ns.panes else "kilix ls: no tabs")
        return 0
    print_table(rows, PANE_COLUMNS if ns.panes else TAB_COLUMNS)
    return 0


def cmd_focus(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kilix focus", description="Focus a live kilix tab or pane")
    parser.add_argument("target", help="tab ID, pane ID, tab:<id>, or pane:<id>")
    ns = parser.parse_args(argv)
    panes = load_panes("focus")
    try:
        kind, target_id = panes.resolve_target(ns.target, panes.load_state())
        panes.focus(f"{kind}:{target_id}")
    except panes.PaneError as exc:
        return fail("focus", str(exc))
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
    panes = load_panes("watch")
    try:
        kind, pane_id = panes.resolve_target(ns.pane_id, panes.load_state())
    except panes.PaneError as exc:
        return fail("watch", str(exc))
    if kind != "pane":
        return fail("watch", f"{ns.pane_id} is a tab; run 'kilix ls --panes' and watch a PANE_ID", 2)
    if pane_id == os.environ.get("KITTY_WINDOW_ID"):
        return fail("watch", "refusing to watch the current pane; open another pane first", 2)

    try:
        while True:
            try:
                # read_raw, not read: the blank tail of a screen is part of the
                # frame, and stripping it makes the display jump each poll.
                frame = panes.read_raw(f"pane:{pane_id}", extent=ns.extent,
                                       ansi=not ns.plain, cursor=not ns.plain)
            except panes.PaneError as exc:
                return fail("watch", str(exc))
            if not ns.once:
                sys.stdout.write("\033[H\033[2J\033[3J")
            sys.stdout.write(frame)
            if frame and not frame.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
            if ns.once:
                return 0
            time.sleep(ns.interval)
    except KeyboardInterrupt:
        return 130


# The direction words this CLI offers, and the fold from the words humans
# reach for onto the canonical four.  This is argument parsing, which is what
# remote.py keeps: the word -> `launch --location` MAPPING, the engine-age
# guard and the argv all live in kilix_sdk.panes, and are not restated here.
# test_panes_split pins that these words and the library's keys agree, so the
# two cannot drift apart.
PANE_DIRECTIONS = ("right", "left", "up", "down", "above", "below")
PANE_DIRECTION_SYNONYMS = {"above": "up", "below": "down"}
CANONICAL_DIRECTIONS = ("right", "left", "up", "down")


def normalize_direction(direction: str) -> str:
    """Fold above/below onto the canonical four."""
    return PANE_DIRECTION_SYNONYMS.get(direction, direction)


def _engine_advice(command: str, exc) -> int:
    """Render the library's EnginePredatesLocation as operator advice."""
    other = "right" if exc.direction == "left" else "down"
    return fail(
        command,
        f"this terminal is running an engine that predates '{exc.direction}' "
        "panes and would put the pane on the wrong side. Restart kilix to "
        f"pick up the current build, or use 'kilix {command} {other}' for now",
        2)


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

    panes = load_panes("new-pane")
    command = ns.command[1:] if ns.command[:1] == ["--"] else ns.command
    try:
        new_id = panes.split(ns.direction, cwd=ns.cwd, command=tuple(command))
    except panes.EnginePredatesLocation as exc:
        return _engine_advice("new-pane", exc)
    except panes.PaneError as exc:
        return fail("new-pane", str(exc))
    print(f"kilix new-pane: opened {new_id}")
    return 0


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

    panes = load_panes("new-tab")
    command = ns.command[1:] if ns.command[:1] == ["--"] else ns.command
    try:
        new_id = panes.new_tab(cwd=ns.cwd, title=ns.title,
                               command=tuple(command))
    except panes.PaneError as exc:
        return fail("new-tab", str(exc))
    print(f"kilix new-tab: opened {new_id}")
    return 0


def cmd_fullscreen(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kilix fullscreen",
        description="Toggle content-only fullscreen for this tab's OS window",
    )
    parser.parse_args(argv)
    panes = load_panes("fullscreen")
    try:
        panes.fullscreen()
    except panes.PaneError as exc:
        return fail("fullscreen", str(exc))
    return 0


# --- pane / tab verbs -------------------------------------------------------


def load_panes(command: str):
    """Import kilix_sdk.panes, or raise SystemExit with a usable message."""
    try:
        from kilix_sdk import panes  # noqa: PLC0415  (deliberately lazy)
    except ImportError as exc:
        raise SystemExit(fail(
            command,
            f"the pane library is unavailable ({exc}). It ships as "
            "config/kilix_sdk/panes.py; check KILIX_HOME and PYTHONPATH", 2))
    return panes


def pane_as_dict(pane: Any) -> dict[str, Any]:
    """A pane rendered for --json, tolerant of fields the SDK may not carry."""
    out: dict[str, Any] = {}
    for field in ("id", "tab_id", "os_window_id", "title", "cwd", "pid",
                  "process", "is_focused", "is_self", "columns", "lines"):
        value = getattr(pane, field, None)
        if value is not None:
            out[field] = value
    cmdline = getattr(pane, "cmdline", None)
    if cmdline is not None:
        out["cmdline"] = list(cmdline)
    return out

SHELL_METACHARACTERS = set("&|;<>()$`") | {"\n"}


def looks_like_shell_string(arguments: list[str]) -> bool:
    """One argument carrying shell syntax means the user wrote a shell line.

    `tab new "cd ~/src && codex --yolo"` only means anything to a shell. Two or
    more arguments are an argv vector and are exec'd directly.
    """
    if len(arguments) != 1:
        return False
    return any(character in SHELL_METACHARACTERS for character in arguments[0])


def partition_double_dash(argv: list[str]) -> tuple[list[str], list[str], bool]:
    """Split argv at the first standalone `--`.

    Done before argparse sees anything. argparse.REMAINDER would swallow any
    flag written after the positional -- `pane right --hold` would silently not
    hold -- which is exactly the class of bug the design warns about.

    `--` always forces argv, which is the escape hatch for a literal filename
    containing a `$`.
    """
    if "--" not in argv:
        return argv, [], False
    index = argv.index("--")
    return argv[:index], argv[index + 1:], True


def emit_new_id(command: str, new_id: Any, porcelain: bool) -> int:
    """Creating verbs print only the id under --porcelain, prose otherwise."""
    if porcelain:
        print(new_id)
    else:
        print(f"kilix {command}: opened {new_id}")
    return 0


def emit_listing(workspace: Any, as_json: bool, as_tree: bool) -> int:
    if as_json:
        print(json.dumps([pane_as_dict(pane) for pane in workspace.panes()],
                         indent=2, sort_keys=True))
        return 0
    print(workspace.tree())
    return 0


def cmd_pane(argv: list[str]) -> int:
    subcommands = ("quad", "list", "close", "focus", "read", "send")
    head = argv[0] if argv else "right"
    if head in subcommands:
        return _pane_subcommand(head, argv[1:])
    if head.startswith("-") and head not in ("-h", "--help"):
        head, argv = "right", ["right", *argv]
    return _pane_split(argv)


def _pane_split(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="kilix pane",
        description="Open a pane beside this one",
        epilog="Direction is relative to the pane the command runs in, not to "
               "whichever pane currently has focus. 'above' and 'below' are "
               "accepted as synonyms for 'up' and 'down'.")
    parser.add_argument("direction", nargs="?", default="right",
                        choices=list(PANE_DIRECTIONS))
    parser.add_argument("--cwd", default="current",
                        help="directory for the new pane (default: follow this one)")
    parser.add_argument("--title", default="", help="name the pane")
    parser.add_argument("--anchor", type=int, default=None,
                        help="split off this pane id instead of this one")
    parser.add_argument("--hold", action="store_true",
                        help="keep the pane open after the command exits")
    parser.add_argument("--porcelain", action="store_true",
                        help="print only the new pane id")
    parser.epilog += " A command goes after --."
    head, command, _ = partition_double_dash(argv)
    ns = parser.parse_args(head)

    panes = load_panes("pane")
    try:
        new_id = panes.split(normalize_direction(ns.direction),
                             anchor=ns.anchor, cwd=ns.cwd,
                             command=tuple(command), title=ns.title,
                             hold=ns.hold)
    except panes.EnginePredatesLocation as exc:
        return _engine_advice("pane", exc)
    except panes.PaneError as exc:
        return fail("pane", str(exc))
    return emit_new_id("pane", new_id, ns.porcelain)


def _pane_subcommand(name: str, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"kilix pane {name}")
    if name == "quad":
        parser.description = "Four panes where this one is"
        parser.add_argument("--anchor", type=int, default=None)
        parser.add_argument("--porcelain", action="store_true",
                            help="print only the new pane ids, one per line")
        ns = parser.parse_args(argv)
        panes = load_panes("pane quad")
        try:
            created = panes.quad(anchor=ns.anchor)
        except Exception as exc:
            return fail("pane quad", str(exc), 2)
        if ns.porcelain:
            for pane_id in created:
                print(pane_id)
        else:
            print("kilix pane quad: opened " + " ".join(str(i) for i in created))
        return 0

    if name == "list":
        parser.description = "What panes exist"
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--tree", action="store_true", dest="as_tree")
        ns = parser.parse_args(argv)
        panes = load_panes("pane list")
        return emit_listing(panes.snapshot(), ns.as_json, ns.as_tree)

    if name == "close":
        parser.description = "Close a pane (default: this one)"
        parser.add_argument("target", nargs="?", default=None)
        parser.add_argument("--force", action="store_true")
        ns = parser.parse_args(argv)
        panes = load_panes("pane close")
        target = ns.target
        if target is None:
            me = panes.snapshot().me()
            if me is None:
                return fail("pane close", "cannot tell which pane this is; "
                                          "name one explicitly", 2)
            target = me.id
        panes.close(target, force=ns.force)
        return 0

    if name == "focus":
        parser.description = "Focus a pane"
        parser.add_argument("target")
        ns = parser.parse_args(argv)
        load_panes("pane focus").focus(ns.target)
        return 0

    if name == "read":
        parser.description = "Print a pane's contents"
        parser.add_argument("target")
        parser.add_argument("--extent", default="screen",
                            choices=["screen", "all"])
        ns = parser.parse_args(argv)
        sys.stdout.write(load_panes("pane read").read(ns.target, extent=ns.extent))
        return 0

    if name == "send":
        parser.description = "Type text into a pane"
        parser.add_argument("target")
        parser.add_argument("text")
        parser.add_argument("--submit", action="store_true",
                            help="press Enter afterwards")
        ns = parser.parse_args(argv)
        load_panes("pane send").send(ns.target, ns.text, submit=ns.submit)
        return 0

    return fail("pane", f"unknown pane command '{name}'", 2)

def cmd_tab(argv: list[str]) -> int:
    subcommands = ("new", "left", "right", "move", "list", "close",
                   "rename", "focus")
    head = argv[0] if argv else "new"
    if head in subcommands:
        return _tab_subcommand(head, argv[1:])
    if head.startswith("-") and head not in ("-h", "--help"):
        return _tab_subcommand("new", argv)
    return fail("tab", f"unknown tab command '{head}'", 2)


def _tab_subcommand(name: str, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"kilix tab {name}")

    if name == "new":
        parser.description = "Open a new page (tab)"
        parser.epilog = (
            "One argument containing shell syntax runs under $SHELL -lc; "
            "otherwise the arguments are an argv vector. Anything after -- is "
            "always argv.")
        parser.add_argument("--cwd", default="current")
        parser.add_argument("--title", default="", help="name the page")
        parser.add_argument("--porcelain", action="store_true",
                            help="print only the new tab id")
        parser.add_argument("command", nargs="*")
        head, tail, forced_argv = partition_double_dash(argv)
        ns = parser.parse_args(head)
        panes = load_panes("tab new")
        command = list(ns.command) + tail
        if not forced_argv and looks_like_shell_string(command):
            new_id = panes.new_tab(cwd=ns.cwd, title=ns.title,
                                   shell_string=command[0])
        else:
            new_id = panes.new_tab(cwd=ns.cwd, title=ns.title,
                                   command=tuple(command))
        return emit_new_id("tab", new_id, ns.porcelain)

    if name in ("left", "right"):
        # Focus, not reorder. Switching is what people do a hundred times a
        # session; reordering is rare, so switching gets the short form.
        parser.description = f"Focus the tab to the {name}"
        parser.parse_args(argv)
        load_panes(f"tab {name}").focus(f"tab:{name}")
        return 0

    if name == "move":
        parser.description = "Reorder this tab"
        parser.add_argument("direction", choices=["left", "right"])
        ns = parser.parse_args(argv)
        load_panes("tab move").move_tab(-1 if ns.direction == "left" else 1)
        return 0

    if name == "list":
        parser.description = "What tabs exist"
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--tree", action="store_true", dest="as_tree")
        ns = parser.parse_args(argv)
        panes = load_panes("tab list")
        workspace = panes.snapshot()
        if ns.as_json:
            print(json.dumps([
                {"id": tab.id, "title": tab.title, "layout": tab.layout,
                 "is_active": tab.is_active,
                 "panes": [pane_as_dict(pane) for pane in tab.panes]}
                for tab in workspace.tabs()], indent=2, sort_keys=True))
            return 0
        return emit_listing(workspace, False, ns.as_tree)

    if name == "close":
        parser.description = "Close a tab (default: this one)"
        parser.add_argument("target", nargs="?", default=None)
        ns = parser.parse_args(argv)
        panes = load_panes("tab close")
        target = ns.target
        if target is None:
            me = panes.snapshot().me()
            if me is None:
                return fail("tab close", "cannot tell which tab this is; "
                                         "name one explicitly", 2)
            target = f"tab:{me.tab_id}"
        panes.close(target)
        return 0

    if name == "rename":
        parser.description = "Rename this tab"
        parser.add_argument("title")
        parser.add_argument("--target", default=None)
        ns = parser.parse_args(argv)
        panes = load_panes("tab rename")
        target = ns.target
        if target is None:
            me = panes.snapshot().me()
            if me is None:
                return fail("tab rename", "cannot tell which tab this is; "
                                          "pass --target", 2)
            target = f"tab:{me.tab_id}"
        panes.rename_tab(target, ns.title)
        return 0

    if name == "focus":
        parser.description = "Focus a tab"
        parser.add_argument("target")
        ns = parser.parse_args(argv)
        load_panes("tab focus").focus(ns.target)
        return 0

    return fail("tab", f"unknown tab command '{name}'", 2)

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
    if command == "pane":
        return cmd_pane(rest)
    if command == "tab":
        return cmd_tab(rest)
    # Aliases, kept forever: docs/AGENTS.md has taught them and scripts exist.
    if command in ("new-pane", "split"):
        return cmd_new_pane(rest)
    if command in ("new-tab", "new-page"):
        return cmd_new_tab(rest)
    return fail(command, "unknown remote-control command", 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
