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
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence


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

#: ``quad`` refuses below this per-pane size rather than making four unusably
#: narrow panes the caller then has to unpick (design section 9).
QUAD_MIN_COLUMNS = 40
QUAD_MIN_LINES = 12


class PaneError(RuntimeError):
    """Any failure raised by this library."""


class AmbiguousTarget(PaneError):
    """A bare id matches both a tab and a pane."""


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
        raise NotImplementedError

    def panes(self) -> Iterator[Pane]:
        """Every pane, in engine order."""
        raise NotImplementedError

    def find(self, target: int | str) -> Pane | Tab:
        """Resolve ``pane:111``, ``tab:37`` or a bare id.

        A bare id that matches both raises :class:`AmbiguousTarget` rather
        than guessing.
        """
        raise NotImplementedError

    def find_pane(self, target: int | str) -> Pane:
        """Like :meth:`find`, but only ever a pane."""
        raise NotImplementedError

    def find_tab(self, target: int | str) -> Tab:
        """Like :meth:`find`, but only ever a tab."""
        raise NotImplementedError

    def me(self) -> Pane | None:
        """The pane this process is running in, or ``None``."""
        raise NotImplementedError

    def tree(self) -> str:
        """One ASCII tree, so the CLI, the TUI and agents render identically."""
        raise NotImplementedError


def snapshot(*, timeout: float = 2.0) -> Workspace:
    """Query the live engine once and build a :class:`Workspace`."""
    raise NotImplementedError


def split(direction: str = "right", *,
          anchor: int | None = None,
          cwd: str = "current",
          command: Sequence[str] = (),
          title: str = "",
          hold: bool = False,
          take_focus: bool = True) -> int:
    """Split a pane and return **the new pane id**.

    ``anchor`` is a pane id; ``None`` means the calling pane.  It maps onto
    ``launch --next-to=id:<n>``, which the engine already supports.
    """
    raise NotImplementedError


def quad(*, anchor: int | None = None,
         commands: Sequence[Sequence[str]] = ()) -> tuple[int, int, int]:
    """Three splits making a 2x2 grid; returns the three new pane ids.

    Transactional: on partial failure the panes already made are closed, so
    the caller is never left with panes it cannot identify.
    """
    raise NotImplementedError


def close(target: int | str, *, force: bool = False) -> None:
    """Close a pane or tab."""
    raise NotImplementedError


def focus(target: int | str) -> None:
    """Focus a pane or tab."""
    raise NotImplementedError


def move_tab(offset: int) -> int:
    """Move the active tab by ``offset``; returns the new index."""
    raise NotImplementedError


def new_tab(*, cwd: str = "current", title: str = "",
            command: Sequence[str] = (), shell_string: str = "") -> int:
    """Open a tab and return its id."""
    raise NotImplementedError


def rename_tab(target: int | str, title: str) -> None:
    """Retitle a tab."""
    raise NotImplementedError


def read(target: int | str, *, extent: str = "screen") -> str:
    """Return the text of a pane."""
    raise NotImplementedError


def send(target: int | str, text: str, *, submit: bool = False) -> None:
    """Type ``text`` into a pane, optionally pressing Enter."""
    raise NotImplementedError


# --- helpers absorbed from config/remote.py -------------------------------

def load_state(*, timeout: float = 2.0) -> list[dict[str, Any]]:
    """The raw ``kitten @ ls`` payload."""
    raise NotImplementedError


def focused_window(windows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The focused window of a tab's window list."""
    raise NotImplementedError


def process_name(window: Mapping[str, Any]) -> str:
    """Basename of the innermost foreground process."""
    raise NotImplementedError


def tab_is_active(os_window: Mapping[str, Any], tab: Mapping[str, Any],
                  windows: Sequence[Mapping[str, Any]]) -> bool:
    """Whether a tab is the active one."""
    raise NotImplementedError


def normalize_target(raw: int | str) -> tuple[str | None, str]:
    """Split ``pane:111`` into ``("pane", "111")``; bare ids give ``None``."""
    raise NotImplementedError


def resolve_target(raw: int | str,
                   state: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    """Resolve a target against live state, refusing ambiguity."""
    raise NotImplementedError


def engine_predates(location: str) -> bool:
    """True when the live engine is older than the build knowing ``location``."""
    raise NotImplementedError
