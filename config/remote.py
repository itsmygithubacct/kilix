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


# Where a new pane goes.
#
# The splits layout always supported all four placements — the axis and the
# side it lands on are independent — but upstream kitty only named three of
# them, and the fourth was reachable only by splitting the other way and then
# using `move_window` to swap, which is a keybinding action and so was not
# available to remote control at all. The fork adds `vsplit-before` and
# `hsplit-before` for the two near-side placements, which is what lets every
# direction be asked for directly and meant the chrome's own left/up buttons
# could drop their swap workaround too.
PANE_LOCATIONS = {
    "right": "vsplit",
    "left": "vsplit-before",
    "down": "hsplit",
    "up": "hsplit-before",
}

# "above"/"below" are the words humans reach for; "up"/"down" are the keys the
# engine locations are mapped from. Accept all four and normalise to the two
# that already exist, so PANE_LOCATIONS stays the single source of direction.
PANE_DIRECTION_SYNONYMS = {"above": "up", "below": "down"}
PANE_DIRECTIONS = tuple(PANE_LOCATIONS) + tuple(PANE_DIRECTION_SYNONYMS)



def normalize_direction(direction: str) -> str:
    """Fold above/below onto the existing PANE_LOCATIONS keys."""
    return PANE_DIRECTION_SYNONYMS.get(direction, direction)

# The near-side placements only exist in the fork, and an engine older than
# this checkout does not merely reject them -- it ignores the location it does
# not recognise and puts the pane wherever its default would go, which is the
# opposite side of the screen from the one that was asked for. A running kilix
# keeps its own build generation alive across a rebuild on purpose, so this is
# the ordinary state between rebuilding and restarting rather than an exotic
# one, and it is worth one readlink to avoid acting silently wrong.
FORK_ONLY_LOCATIONS = {"vsplit-before", "hsplit-before"}


def engine_predates(location: str) -> bool:
    """True when the live engine is older than the build that knows `location`."""
    if location not in FORK_ONLY_LOCATIONS:
        return False
    pid = os.environ.get("KITTY_PID", "")
    build_directory = os.environ.get("KILIX_BUILD_DIRECTORY", "")
    if not pid or not build_directory:
        return False        # cannot tell; do not block on a guess
    try:
        running = os.path.realpath(f"/proc/{int(pid)}/exe")
    except (ValueError, OSError):
        return False
    current = os.path.realpath(
        os.path.join(build_directory, "current", "src", "kitty", "launcher", "kitty"))
    if not running or not os.path.exists(current):
        return False
    if running == current:
        return False
    # Different generations: only a problem if the running one is too old to
    # have the location at all.
    source = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(running)))), "src", "kitty", "layout", "splits.py")
    try:
        with open(source, encoding="utf-8") as handle:
            return location not in handle.read()
    except OSError:
        return False


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

    location = PANE_LOCATIONS[ns.direction]
    if engine_predates(location):
        return fail(
            "new-pane",
            f"this terminal is running an engine that predates '{ns.direction}' "
            "panes and would put the pane on the wrong side. Restart kilix to "
            "pick up the current build, or use 'kilix new-pane "
            f"{'right' if ns.direction == 'left' else 'down'}' for now", 2)

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


# --- pane / tab verbs -------------------------------------------------------
#
# These front ends hold argument parsing and formatting only. Every operation
# goes through kilix_sdk.panes; nothing here calls `kitten @` directly, which
# is the rule that stops a sixth reimplementation of `kitten @ ls` walking
# appearing in this file.
#
# The import is deliberately lazy. remote.py is loaded by tests and by the
# older verbs that do not need the SDK, and a module-level import would make
# the whole file unloadable wherever kilix_sdk.panes is not on the path.


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

    direction = normalize_direction(ns.direction)
    location = PANE_LOCATIONS[direction]
    if engine_predates(location):
        return fail(
            "pane",
            f"this terminal is running an engine that predates '{direction}' "
            "panes and would put the pane on the wrong side. Restart kilix to "
            "pick up the current build, or use 'kilix pane "
            f"{'right' if direction == 'left' else 'down'}' for now", 2)

    panes = load_panes("pane")
    new_id = panes.split(direction, anchor=ns.anchor, cwd=ns.cwd,
                         command=tuple(command), title=ns.title, hold=ns.hold)
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
