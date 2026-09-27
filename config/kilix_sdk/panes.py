#!/usr/bin/env python3
"""Pane, tab and workspace model for Kilix, and the operations over it.

One library, five front ends: the ``kilix`` verbs, the shell functions, the
TUI tree, the GUI and any agent running Python all go through here.  **No
front end calls ``kitten @`` directly.**

Stdlib only, like ``tui_shell``, so the TUI and the installers can import it
with no dependency story.

The library holds no state.  Every call that needs the live workspace takes a
fresh :func:`snapshot`; there is deliberately no cache, because a cached
workspace is a second engine and this must not become one.

``split()`` returns the new pane id as an ``int``.  That is the point of the
module for agents: the pane you just made is addressable without scraping a
log line.  ``anchor=`` maps onto ``launch --next-to=id:<n>``, which the engine
already supports, so nothing here needs an engine change.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence


KITTEN = os.environ.get("KILIX_KITTEN", "kitten")
RC_PASSWORD_FILE = os.environ.get("KILIX_RC_PASSWORD_FILE", "")

#: Direction word -> kitty ``launch --location``.  The near-side placements
#: only exist in the fork; see :func:`engine_predates`.
PANE_LOCATIONS: Mapping[str, str] = {
    "right": "vsplit",
    "left": "vsplit-before",
    "down": "hsplit",
    "up": "hsplit-before",
    "vsplit": "vsplit",
    "hsplit": "hsplit",
}

FORK_ONLY_LOCATIONS = frozenset({"vsplit-before", "hsplit-before"})

#: "above"/"below" are the words humans reach for; "up"/"down" are the keys
#: PANE_LOCATIONS is mapped from.  Both live here so the direction vocabulary
#: has one home and a front end does not have to carry half of it.
PANE_DIRECTION_SYNONYMS: Mapping[str, str] = {"above": "up", "below": "down"}

#: The direction words a front end should offer.  Deliberately excludes the
#: raw engine names in PANE_LOCATIONS, which are an implementation detail.
PANE_DIRECTIONS: tuple[str, ...] = (
    "right", "left", "down", "up", "above", "below")


def normalize_direction(direction: str) -> str:
    """Fold ``above``/``below`` onto the PANE_LOCATIONS keys."""
    return PANE_DIRECTION_SYNONYMS.get(direction, direction)

#: ``quad`` refuses below this per-pane size rather than making four unusably
#: narrow panes the caller then has to unpick (design section 9).
QUAD_MIN_COLUMNS = 40
QUAD_MIN_LINES = 12


class PaneError(RuntimeError):
    """Any failure raised by this library."""


class AmbiguousTarget(PaneError):
    """A bare id matches both a tab and a pane."""


class EnginePredatesLocation(PaneError):
    """The running engine is too old to place a pane where it was asked to.

    Carries ``direction`` and ``location`` so a front end can render its own
    advice without re-deriving which build knows what.  A plain message would
    force every caller to parse prose to say "use right instead of left".
    """

    def __init__(self, direction: str, location: str) -> None:
        super().__init__(
            f"the running engine is older than this build and does not know "
            f"--location={location}")
        self.direction = direction
        self.location = location


class NoSuchTarget(PaneError):
    """A target names no live tab or pane."""


@dataclass(frozen=True)
class Pane:
    id: int
    tab_id: int
    os_window_id: int
    title: str
    cwd: str
    pid: int
    cmdline: tuple[str, ...]
    process: str
    is_focused: bool
    is_self: bool
    env: Mapping[str, str] = field(default_factory=dict)
    broker_session: str | None = None
    columns: int = 0
    lines: int = 0


@dataclass(frozen=True)
class Tab:
    id: int
    os_window_id: int
    title: str
    layout: str
    is_active: bool
    panes: tuple[Pane, ...] = ()


@dataclass(frozen=True)
class OSWindow:
    id: int
    is_focused: bool
    tabs: tuple[Tab, ...] = ()


@dataclass(frozen=True)
class Workspace:
    os_windows: tuple[OSWindow, ...] = ()

    def tabs(self) -> Iterator[Tab]:
        """Every tab, in engine order."""
        for os_window in self.os_windows:
            for tab in os_window.tabs:
                yield tab

    def panes(self) -> Iterator[Pane]:
        """Every pane, in engine order."""
        for tab in self.tabs():
            for pane in tab.panes:
                yield pane

    def find(self, target: int | str) -> Pane | Tab:
        """Resolve ``pane:111``, ``tab:37`` or a bare id.

        A bare id matching both a tab and a pane raises
        :class:`AmbiguousTarget` rather than guessing which was meant.
        """
        kind, value = normalize_target(target)
        tabs = {str(tab.id): tab for tab in self.tabs()}
        panes = {str(pane.id): pane for pane in self.panes()}
        if kind == "tab":
            if value not in tabs:
                raise NoSuchTarget(f"no live tab with id {value}")
            return tabs[value]
        if kind == "pane":
            if value not in panes:
                raise NoSuchTarget(f"no live pane with id {value}")
            return panes[value]
        in_tabs, in_panes = value in tabs, value in panes
        if in_tabs and in_panes:
            raise AmbiguousTarget(
                f"id {value} is ambiguous; use tab:{value} or pane:{value}")
        if in_tabs:
            return tabs[value]
        if in_panes:
            return panes[value]
        raise NoSuchTarget(
            f"no live tab or pane with id {value}; run 'kilix ls --panes'")

    def find_pane(self, target: int | str) -> Pane:
        """Like :meth:`find`, but only ever a pane.

        The review asked for this so callers stop type-switching on
        ``find()``'s return.
        """
        kind, value = normalize_target(target)
        if kind == "tab":
            raise NoSuchTarget(f"{value} names a tab, not a pane")
        for pane in self.panes():
            if str(pane.id) == value:
                return pane
        raise NoSuchTarget(f"no live pane with id {value}")

    def find_tab(self, target: int | str) -> Tab:
        """Like :meth:`find`, but only ever a tab."""
        kind, value = normalize_target(target)
        if kind == "pane":
            raise NoSuchTarget(f"{value} names a pane, not a tab")
        for tab in self.tabs():
            if str(tab.id) == value:
                return tab
        raise NoSuchTarget(f"no live tab with id {value}")

    def me(self) -> Pane | None:
        """The pane this process is running in, or ``None``.

        Prefers the engine's own ``is_self``; falls back to
        ``KITTY_WINDOW_ID`` so the answer survives a payload without it.
        """
        for pane in self.panes():
            if pane.is_self:
                return pane
        mine = os.environ.get("KITTY_WINDOW_ID", "")
        if mine:
            for pane in self.panes():
                if str(pane.id) == mine:
                    return pane
        return None

    def focused(self) -> Pane | None:
        """The one genuinely focused pane.

        ``kitten @ ls`` marks a pane ``is_focused`` *within its own tab*, so a
        workspace with twelve tabs reports twelve focused panes.  The real one
        is the focused pane of the active tab of the focused OS window.  Every
        front end needs this and none of them should re-derive it.
        """
        for os_window in self.os_windows:
            if not os_window.is_focused:
                continue
            for tab in os_window.tabs:
                if not tab.is_active:
                    continue
                for pane in tab.panes:
                    if pane.is_focused:
                        return pane
                return tab.panes[0] if tab.panes else None
        return None

    def index(self) -> dict[int, Pane]:
        """Every live pane keyed by pane id.

        The verbs address panes as ``pane:<id>``; callers that need to go the
        other way were rebuilding this dict by hand, so it lives here now.
        """
        return {pane.id: pane for pane in self.panes()}

    def tab_index(self) -> dict[int, Tab]:
        """Every live tab keyed by tab id."""
        return {tab.id: tab for tab in self.tabs()}

    def pane_for_pid(self, pid: int, *, max_hops: int = 64) -> Pane | None:
        """The pane a process is running in, or ``None``.

        ``Pane.pid`` is the process the engine launched in the pane, so
        anything started inside it -- a shell job, an agent, a nested session
        -- is a descendant of it. Walk ``/proc`` upward until a pane claims
        the ancestor. ``max_hops`` bounds the walk so a pid cycle or a re-used
        pid cannot hang the caller.

        Answers only for processes on this machine; a pid from elsewhere
        resolves to ``None`` rather than to the wrong pane.
        """
        owners = {pane.pid: pane for pane in self.panes() if pane.pid}
        current = int(pid)
        # examine the pid itself, then up to max_hops ancestors -- checking
        # after the final hop rather than before it, or a pane exactly
        # max_hops up is stepped onto and then thrown away.
        for _ in range(max_hops + 1):
            if current <= 1:
                break
            owner = owners.get(current)
            if owner is not None:
                return owner
            current = _parent_pid(current)
        return None

    def locate(self, pids: Iterable[int], *,
               max_hops: int = 64) -> dict[int, Pane | None]:
        """:meth:`pane_for_pid` for many pids at once.

        Unresolved pids are kept with a ``None`` value rather than dropped, so
        the caller can tell "not in any pane" from "never asked".
        """
        return {int(pid): self.pane_for_pid(pid, max_hops=max_hops)
                for pid in pids}

    def tree(self) -> str:
        """One ASCII tree, so the CLI, the TUI and agents render identically."""
        me = self.me()
        focused = self.focused()
        lines: list[str] = []
        for os_window in self.os_windows:
            marker = " *" if os_window.is_focused else ""
            lines.append(f"os-window {os_window.id}{marker}")
            tabs = os_window.tabs
            for tab_index, tab in enumerate(tabs):
                last_tab = tab_index == len(tabs) - 1
                stem, lead = ("`-- ", "    ") if last_tab else ("|-- ", "|   ")
                active = " *" if tab.is_active else ""
                title = f" {tab.title}" if tab.title else ""
                lines.append(
                    f"{stem}tab {tab.id}{active} [{tab.layout}]{title}")
                panes = tab.panes
                for pane_index, pane in enumerate(panes):
                    last_pane = pane_index == len(panes) - 1
                    pstem = "`-- " if last_pane else "|-- "
                    flags = ""
                    if focused is not None and pane.id == focused.id:
                        flags += " *"
                    if me is not None and pane.id == me.id:
                        flags += " (self)"
                    process = f" {pane.process}" if pane.process else ""
                    ptitle = f" {pane.title}" if pane.title else ""
                    lines.append(
                        f"{lead}{pstem}pane {pane.id}{flags}{process}{ptitle}")
        return "\n".join(lines)


# --- building the model ----------------------------------------------------

def _parent_pid(pid: int) -> int:
    """The parent of ``pid``, or ``0`` when it cannot be read.

    Field 4 of ``/proc/<pid>/stat`` is the ppid, but field 2 is the comm and
    may itself contain spaces and parentheses, so split on the LAST ``") "``
    rather than on whitespace -- splitting on the first mis-parses a process
    whose name contains one.
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as handle:
            stat = handle.read()
    except (OSError, ValueError):
        return 0
    try:
        return int(stat.rsplit(") ", 1)[1].split()[1])
    except (IndexError, ValueError):
        return 0


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("path") or value.get("cwd") or "")
    return str(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse(state: Sequence[Mapping[str, Any]]) -> Workspace:
    """Build a :class:`Workspace` from a raw ``kitten @ ls`` payload.

    Split out from :func:`snapshot` so a recorded fixture can be parsed
    without a live engine.
    """
    os_windows: list[OSWindow] = []
    for os_index, raw_os in enumerate(state, 1):
        os_id = _int(raw_os.get("id") or raw_os.get("os_window_id"), os_index)
        tabs: list[Tab] = []
        for raw_tab in raw_os.get("tabs") or []:
            tab_id = _int(raw_tab.get("id"))
            windows = raw_tab.get("windows") or []
            panes = tuple(
                _pane(raw_window, tab_id, os_id) for raw_window in windows)
            tabs.append(Tab(
                id=tab_id,
                os_window_id=os_id,
                title=_text(raw_tab.get("title")),
                layout=_text(raw_tab.get("layout")),
                is_active=tab_is_active(raw_os, raw_tab, windows),
                panes=panes,
            ))
        os_windows.append(OSWindow(
            id=os_id,
            is_focused=bool(raw_os.get("is_focused")),
            tabs=tuple(tabs),
        ))
    return Workspace(os_windows=tuple(os_windows))


def _pane(raw: Mapping[str, Any], tab_id: int, os_window_id: int) -> Pane:
    env = raw.get("env") or {}
    if not isinstance(env, dict):
        env = {}
    cmdline = tuple(str(part) for part in (raw.get("cmdline") or []))
    return Pane(
        id=_int(raw.get("id")),
        tab_id=tab_id,
        os_window_id=os_window_id,
        title=_text(raw.get("title")),
        cwd=_text(raw.get("cwd")),
        pid=_int(raw.get("pid")),
        cmdline=cmdline,
        process=process_name(raw),
        is_focused=bool(raw.get("is_focused")),
        is_self=bool(raw.get("is_self")),
        env=dict(env),
        broker_session=env.get("KITTY_PTY_BROKER_SESSION"),
        columns=_int(raw.get("columns")),
        lines=_int(raw.get("lines")),
    )


def snapshot(*, timeout: float = 2.0) -> Workspace:
    """Query the live engine once and build a :class:`Workspace`."""
    return parse(load_state(timeout=timeout))


# --- operations ------------------------------------------------------------

def _run(args: Sequence[str], *, authenticated: bool = True,
         via_tty: bool = False,
         timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
    """One ``kitten @`` invocation.  The only place in Kilix that runs one.

    ``via_tty`` drops ``KITTY_LISTEN_ON`` from the child environment, which
    makes the kitten talk to the terminal on its controlling tty instead of the
    socket.  ``resize-os-window --self`` needs that: over the socket "self" is
    the socket peer, not the OS window the operator is looking at.
    """
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
        timeout=timeout,
    )


def _check(args: Sequence[str], what: str, *, via_tty: bool = False,
           authenticated: bool = True, timeout: float = 2.0) -> str:
    proc = _run(args, authenticated=authenticated, via_tty=via_tty,
                timeout=timeout)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"kitten exited {proc.returncode}"
        raise PaneError(f"{what}: {detail}")
    return proc.stdout.strip()


def _match(target: int | str) -> tuple[str, str]:
    """Return ``(kind, id)`` for an already-resolved or explicit target."""
    kind, value = normalize_target(target)
    if kind is None:
        state = load_state()
        kind, value = resolve_target(target, state)
    return kind, value


def split_argv(direction: str = "right", *,
               anchor: int | None = None,
               cwd: str = "current",
               command: Sequence[str] = (),
               title: str = "",
               hold: bool = False,
               take_focus: bool = True) -> list[str]:
    """The exact ``kitten @`` argv :func:`split` would run.

    Exposed so the argv can be asserted in a test without a live engine.
    """
    direction = normalize_direction(direction)
    if direction not in PANE_LOCATIONS:
        raise PaneError(
            f"unknown direction {direction!r}; "
            f"expected one of {', '.join(sorted(PANE_LOCATIONS))}")
    location = PANE_LOCATIONS[direction]
    if engine_predates(location):
        raise EnginePredatesLocation(direction, location)
    argv = ["launch", "--type=window", f"--location={location}"]
    if cwd:
        argv.append(f"--cwd={cwd}")
    if anchor is not None:
        argv.append(f"--next-to=id:{int(anchor)}")
    else:
        # anchor=None is documented as "the calling pane", and only --self
        # means that.  Without it the engine hangs the split off whichever
        # pane has FOCUS, so a split asked for from a background pane lands
        # somewhere else entirely.  remote.py carried this flag before the
        # library did; it belongs here, with the rest of the placement.
        argv.append("--self")
    if title:
        argv.append(f"--title={title}")
    if hold:
        argv.append("--hold")
    if not take_focus:
        argv.append("--keep-focus")
    if command:
        argv.append("--")
        argv.extend(str(part) for part in command)
    return argv


def split(direction: str = "right", *,
          anchor: int | None = None,
          cwd: str = "current",
          command: Sequence[str] = (),
          title: str = "",
          hold: bool = False,
          take_focus: bool = True) -> int:
    """Split a pane and return **the new pane id**.

    ``anchor`` is a pane id; ``None`` means the calling pane.  It maps onto
    ``launch --next-to=id:<n>``.  Note the engine ignores ``--next-to`` over
    remote control unless the matched window is in the target tab, so
    cross-tab anchoring is silently a no-op -- that is an engine constraint,
    not one this library can paper over.
    """
    argv = split_argv(direction, anchor=anchor, cwd=cwd, command=command,
                      title=title, hold=hold, take_focus=take_focus)
    out = _check(argv, "split")
    try:
        return int(out)
    except ValueError as exc:
        raise PaneError(f"split: engine returned {out!r}, not a pane id") from exc


def quad(*, anchor: int | None = None,
         commands: Sequence[Sequence[str]] = ()) -> tuple[int, int, int]:
    """Three splits making a 2x2 grid; returns the three new pane ids.

    Transactional: if any split fails, the panes already made are closed, so
    the caller is never left holding panes it cannot identify.  Focus returns
    to the origin pane.
    """
    workspace = snapshot()
    origin = workspace.find_pane(anchor) if anchor is not None else workspace.me()
    if origin is None:
        raise PaneError("quad: cannot find the calling pane; pass anchor=")

    if origin.columns and origin.lines:
        per_columns, per_lines = origin.columns // 2, origin.lines // 2
        if per_columns < QUAD_MIN_COLUMNS or per_lines < QUAD_MIN_LINES:
            raise PaneError(
                f"quad: this pane is {origin.columns}x{origin.lines}, so each "
                f"of the four would be about {per_columns}x{per_lines}; "
                f"needs at least {QUAD_MIN_COLUMNS}x{QUAD_MIN_LINES} each")

    def command_for(index: int) -> Sequence[str]:
        return commands[index] if index < len(commands) else ()

    made: list[int] = []
    try:
        right = split("right", anchor=origin.id, take_focus=False,
                      command=command_for(0))
        made.append(right)
        below = split("down", anchor=origin.id, take_focus=False,
                      command=command_for(1))
        made.append(below)
        below_right = split("down", anchor=right, take_focus=False,
                            command=command_for(2))
        made.append(below_right)
    except PaneError:
        for pane_id in reversed(made):
            try:
                close(pane_id, force=True)
            except PaneError:
                pass
        raise
    focus(origin.id)
    return made[0], made[1], made[2]


def close(target: int | str, *, force: bool = False) -> None:
    """Close a pane or tab.

    The engine has no force flag on ``close-window``/``close-tab``, so
    ``force`` is not one: it maps to ``--ignore-no-match``, meaning "close it
    and do not complain if it has already gone".  That is the semantic a
    caller cleaning up ids it created actually wants, and it is what
    :func:`quad` uses when rolling back.
    """
    kind, value = _match(target)
    command = "close-window" if kind == "pane" else "close-tab"
    argv = [command, "--match", f"id:{value}"]
    if force:
        argv.append("--ignore-no-match")
    _check(argv, f"close {kind} {value}")


def focus(target: int | str) -> None:
    """Focus a pane or tab."""
    kind, value = _match(target)
    command = "focus-window" if kind == "pane" else "focus-tab"
    _check([command, "--match", f"id:{value}"], f"focus {kind} {value}")


def move_tab(offset: int) -> int:
    """Move the active tab by ``offset``; returns its new index, 1-based."""
    action = "move_tab_forward" if offset > 0 else "move_tab_backward"
    for _ in range(abs(int(offset))):
        _check(["action", action], "move_tab")
    workspace = snapshot()
    for os_window in workspace.os_windows:
        for index, tab in enumerate(os_window.tabs, 1):
            if tab.is_active:
                return index
    return 0


def new_tab(*, cwd: str = "current", title: str = "",
            command: Sequence[str] = (), shell_string: str = "") -> int:
    """Open a tab and return its id.

    ``command`` is argv and is passed after ``--``.  ``shell_string`` is a
    single string run through the shell.  Passing both is a caller error.
    """
    argv = new_tab_argv(cwd=cwd, title=title, command=command,
                        shell_string=shell_string)
    out = _check(argv, "new_tab")
    try:
        return int(out)
    except ValueError as exc:
        raise PaneError(f"new_tab: engine returned {out!r}, not an id") from exc


def new_tab_argv(*, cwd: str = "current", title: str = "",
                 command: Sequence[str] = (),
                 shell_string: str = "") -> list[str]:
    """The exact ``kitten @`` argv :func:`new_tab` would run.

    Symmetric with :func:`split_argv`, and for the same reason: it lets the
    argv be asserted without a live engine.
    """
    if command and shell_string:
        raise PaneError("new_tab: pass command= or shell_string=, not both")
    argv = ["launch", "--type=tab", "--self"]
    if cwd:
        argv.append(f"--cwd={cwd}")
    if title:
        # --tab-title names the tab; --title names the window inside it.  For
        # --type=tab the operator means the former.
        argv.extend(["--tab-title", title])
    if shell_string:
        argv.extend(["--", "/bin/sh", "-c", shell_string])
    elif command:
        argv.append("--")
        argv.extend(str(part) for part in command)
    return argv


def rename_tab(target: int | str, title: str) -> None:
    """Retitle a tab."""
    _, value = _match(target)
    _check(["set-tab-title", "--match", f"id:{value}", title],
           f"rename_tab {value}")


def read(target: int | str, *, extent: str = "screen",
         ansi: bool = False, cursor: bool = False) -> str:
    """Return the text of a pane.

    ``ansi`` keeps the pane's styling instead of flattening it, and ``cursor``
    adds the cursor position escape.  ``kilix watch`` renders a live pane and
    needs both; the default stays plain text so ordinary callers are not
    handed escape sequences they did not ask for.
    """
    kind, value = _match(target)
    if kind != "pane":
        raise NoSuchTarget(f"read: {value} is a tab, not a pane")
    argv = ["get-text", "--match", f"id:{value}", f"--extent={extent}"]
    if ansi:
        argv.append("--ansi")
    if cursor:
        argv.append("--add-cursor")
    return _check(argv, f"read pane {value}")


def read_raw(target: int | str, *, extent: str = "screen",
             ansi: bool = False, cursor: bool = False) -> str:
    """:func:`read` without the trailing-whitespace strip.

    ``read`` is the ergonomic one and strips, which is right for a caller that
    wants the text.  A live renderer must not strip: the blank tail of a
    screen is part of the frame, and eating it makes the display jump.
    """
    kind, value = _match(target)
    if kind != "pane":
        raise NoSuchTarget(f"read_raw: {value} is a tab, not a pane")
    argv = ["get-text", "--match", f"id:{value}", f"--extent={extent}"]
    if ansi:
        argv.append("--ansi")
    if cursor:
        argv.append("--add-cursor")
    proc = _run(argv)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"kitten exited {proc.returncode}"
        raise PaneError(f"read pane {value}: {detail}")
    return proc.stdout


def fullscreen() -> None:
    """Toggle content-only fullscreen for this pane's OS window.

    Goes over the controlling tty rather than the socket, because ``--self``
    over the socket resolves to the socket peer rather than to the window the
    operator is in front of.
    """
    _check(["resize-os-window", "--self", "--action", "toggle-fullscreen"],
           "fullscreen", via_tty=True, authenticated=False)


def send(target: int | str, text: str, *, submit: bool = False) -> None:
    """Type ``text`` into a pane, optionally pressing Enter."""
    kind, value = _match(target)
    if kind != "pane":
        raise NoSuchTarget(f"send: {value} is a tab, not a pane")
    payload = text + "\r" if submit else text
    _check(["send-text", "--match", f"id:{value}", payload],
           f"send to pane {value}")


# --- helpers absorbed from config/remote.py -------------------------------

def load_state(*, timeout: float = 2.0) -> list[dict[str, Any]]:
    """The raw ``kitten @ ls`` payload."""
    proc = _run(["ls"], timeout=timeout)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"kitten exited {proc.returncode}"
        listen = os.environ.get("KITTY_LISTEN_ON", "")
        raise PaneError(
            f"could not query live kilix tabs via KITTY_LISTEN_ON={listen}: "
            f"{detail}")
    try:
        state = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PaneError(f"could not parse kitty state: {exc}") from exc
    if not isinstance(state, list):
        raise PaneError("kitty returned an unexpected remote-control payload")
    return state


def focused_window(windows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The focused window of a tab's window list."""
    for window in windows:
        if window.get("is_focused") or window.get("is_active"):
            return window
    return windows[0] if windows else {}


def process_name(window: Mapping[str, Any]) -> str:
    """Basename of the innermost foreground process."""
    for process in reversed(window.get("foreground_processes") or []):
        cmdline = process.get("cmdline") or []
        if cmdline:
            return os.path.basename(cmdline[0]) or cmdline[0]
    return ""


def tab_is_active(os_window: Mapping[str, Any], tab: Mapping[str, Any],
                  windows: Sequence[Mapping[str, Any]]) -> bool:
    """Whether a tab is the active one."""
    if "is_active" in tab:
        return bool(tab.get("is_active"))
    return bool(os_window.get("is_focused")) and any(
        window.get("is_focused") for window in windows)


def normalize_target(raw: int | str) -> tuple[str | None, str]:
    """Split ``pane:111`` into ``("pane", "111")``; a bare id gives ``None``."""
    text = str(raw)
    if ":" not in text:
        return None, text
    kind, _, value = text.partition(":")
    kind = kind.lower()
    if kind in {"pane", "window", "win"}:
        return "pane", value
    if kind in {"tab", "page", "session"}:
        return "tab", value
    return None, text


def resolve_target(raw: int | str,
                   state: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """Resolve a target against live state, refusing ambiguity."""
    return _resolve(normalize_target(raw), parse(state))


def _resolve(pair: tuple[str | None, str], workspace: Workspace) -> tuple[str, str]:
    kind, value = pair
    if not value:
        raise NoSuchTarget("missing ID")
    found = workspace.find(f"{kind}:{value}" if kind else value)
    return ("pane" if isinstance(found, Pane) else "tab"), str(found.id)


def engine_predates(location: str) -> bool:
    """True when the live engine is older than the build knowing ``location``.

    A running kilix keeps its own build generation alive across a rebuild on
    purpose, so this is the ordinary state between rebuilding and restarting.
    An engine that does not know a location does not reject it -- it puts the
    pane on the opposite side -- which is worth one readlink to avoid.
    """
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
    current = os.path.realpath(os.path.join(
        build_directory, "current", "src", "kitty", "launcher", "kitty"))
    if not running or not os.path.exists(current):
        return False
    if running == current:
        return False
    source = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(running)))), "src", "kitty", "layout", "splits.py")
    try:
        with open(source, encoding="utf-8") as handle:
            return location not in handle.read()
    except OSError:
        return False
