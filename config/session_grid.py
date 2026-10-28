"""Coding-session grids: tabs in the engine's sessions layout, twelve panes each.

Every launch of a coding session into "a new tab" comes here, whoever starts
it: ``kilix session``, ``kilix agent-control new-tab``, the structured
``agent.launch`` action, ``kilix new-tab codex`` and ``kilix session grid
add``. A launch joins the newest grid of the same name with room in the
caller's OS window, or opens a new grid tab. The engine's ``sessions`` layout
keeps the panes in two rows of ceil(n/2) columns and re-lays them out when one
closes, so nothing here has to rebalance.

A grid is a tab whose layout is ``sessions`` and which holds at least one pane
carrying this grid's group marker. Manual panes in that tab take capacity too;
a tab whose layout a user changed is no longer a grid. Placement is serialised
per engine socket, so launches from different run stores cannot overfill a tab.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat

import agent_control as control

CAPACITY = 12
LAYOUT = "sessions"
DEFAULT_GRID = "agent-sessions"
GROUP_VAR = "kilix_session_group"
NAME_VAR = "kilix_session_grid"
REQUIRED_OPTIONS = ("--keep-focus", "--source-window", "--next-to", "--var", "--location")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def shape(n):
    """Panes per column, left to right: one pane alone, else two rows."""
    if n <= 0:
        return []
    if n == 1:
        return [1]
    return [2] * (n // 2) + [1] * (n % 2)


def slot(index, n):
    """(column, row) of the index-th pane of n: columns fill top to bottom."""
    if not 0 <= index < n:
        raise ValueError("pane index outside the grid")
    return (0, 0) if n == 1 else (index // 2, index % 2)


def group(name):
    return hashlib.sha256(("kilix-session-grid\0" + name).encode("utf-8")).hexdigest()


def enabled():
    """The coding_session_grid setting: on unless the user turned it off."""
    from kilix_sdk import settings
    return settings.coding_session_grid()


def is_agent_command(argv):
    """True when argv starts a known coding client (codex, claude, grok, ...)."""
    return bool(argv) and Path(str(argv[0])).name in control.PROCESS_AGENTS


@contextmanager
def placement_lock(client):
    """One placement at a time per engine socket, whatever the run store."""
    command = list(getattr(client, "command", []))
    socket = command[command.index("--to") + 1] if "--to" in command[:-1] else ""
    from kilix_sdk.paths import session_dir
    folder = Path(session_dir()) / "session-grid"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise control.ControlError("session grid lock directory must be private and owned")
    name = hashlib.sha256(socket.encode("utf-8")).hexdigest()[:32] + ".lock"
    fd = os.open(folder / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise control.ControlError("session grid lock must be a private, owned file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def tabs_of(panes):
    tabs = {}
    for pane in panes:
        tabs.setdefault(pane["tab_id"], []).append(pane)
    return tabs


def occupancy(members):
    """Window groups in a tab: an overlay shares its parent's slot."""
    counts = [p.get("tab_window_groups") for p in members if p.get("tab_window_groups")]
    return counts[0] if counts else len(members)


def is_grid(members, marker=None):
    """A tab still in the sessions layout with a grid marker (this one, if given)."""
    if not members or any(p.get("layout") != LAYOUT for p in members):
        return False
    markers = {(p.get("user_vars") or {}).get(GROUP_VAR) for p in members}
    return marker in markers if marker else bool(markers - {None})


def grids(panes, name=None):
    """Every grid tab, newest first, as {tab_id, os_window_id, name, count, panes}."""
    marker = group(name) if name else None
    found = []
    for tab_id, members in tabs_of(panes).items():
        if not is_grid(members, marker):
            continue
        names = [(p.get("user_vars") or {}).get(NAME_VAR) for p in members]
        count = occupancy(members)
        found.append({"tab_id": tab_id, "os_window_id": members[0]["os_window_id"],
                      "tab_title": members[0].get("tab_title", ""),
                      "name": next((n for n in names if n), None), "count": count, "capacity": CAPACITY,
                      "panes": [{"pane_id": p["id"], "title": p.get("title", ""),
                                 "slot": list(slot(i, len(members))) if len(members) == count else None}
                                for i, p in enumerate(members)]})
    return sorted(found, key=lambda g: g["tab_id"], reverse=True)


def place(client, *, source, argv, cwd, title, name=DEFAULT_GRID, new=False, into=None,
          hold=True, extra_vars=(), extra_env=(), retarget=None):
    """Launch argv as a pane in a session grid without taking focus.

    ``source`` is the verified pane the launch is anchored to; its OS window
    holds the grid. ``into`` names a pane whose grid tab must take the new
    pane; ``new`` always opens a new grid tab. ``retarget(identity)`` lets a
    guarded client move its expected target to the pane the launch is
    anchored on before the launch is sent. ``extra_vars`` and ``extra_env``
    are (name, value) pairs for the pane's user variables and environment.
    """
    if not argv:
        raise control.ControlError("a grid pane needs a command")
    if new and into:
        raise control.ControlError("choose --new or --into, not both")
    name = control.plain_text(name, "grid name", 200)
    title = control.plain_text(title, "title", 200)
    marker = group(name)
    for key, value in (*extra_vars, *extra_env):
        if not NAME.fullmatch(key) or key in (GROUP_VAR, NAME_VAR):
            raise control.ControlError(f"invalid or reserved variable name: {key!r}")
        control.plain_text(value, key, 4096)
    with placement_lock(client):
        help_text = client.run(["launch", "--help"])
        if any(option not in help_text for option in REQUIRED_OPTIONS):
            raise control.ControlError("running engine lacks required focus-preserving grid options")
        panes = client.snapshot()
        tabs = tabs_of(panes)
        window = source["os_window_id"]
        if into is not None:
            chosen = next((p for p in panes if p["id"] == into), None)
            if chosen is None:
                raise control.ControlError("--into pane is absent; take a new snapshot")
            members = tabs[chosen["tab_id"]]
            if not is_grid(members, marker):
                raise control.ControlError(f"--into pane is not in a '{name}' session grid")
            if occupancy(members) >= CAPACITY:
                raise control.ControlError(f"that grid already holds {CAPACITY} panes")
        elif new:
            members = None
        else:
            # D4: the newest grid with room. Tab ids only grow.
            eligible = [m for m in tabs.values() if m[0]["os_window_id"] == window
                        and is_grid(m, marker) and occupancy(m) < CAPACITY]
            members = max(eligible, key=lambda m: m[0]["tab_id"]) if eligible else None
        # --into may name a grid in another OS window; the pane goes there.
        window = members[0]["os_window_id"] if members else window
        anchor = members[-1] if members else source
        anchor_broker = (anchor.get("env") or {}).get("KITTY_PTY_BROKER_SESSION", "") or anchor.get("broker", "")
        if not control.BROKER.fullmatch(anchor_broker or ""):
            raise control.ControlError("grid anchor lacks a broker identity")
        identity = {"pane_id": anchor["id"] if "id" in anchor else anchor["pane_id"], "broker": anchor_broker}
        if retarget is not None:
            retarget(identity)
        launch = ["launch", "--match", f"window_id:{identity['pane_id']}",
                  "--next-to", f"id:{identity['pane_id']}", "--source-window", f"id:{identity['pane_id']}",
                  "--keep-focus", "--cwd", str(cwd), "--title", title,
                  "--var", f"{GROUP_VAR}={marker}", "--var", f"{NAME_VAR}={name}"]
        if hold:
            launch.append("--hold")
        for key, value in extra_vars:
            launch += ["--var", f"{key}={value}"]
        for key, value in extra_env:
            launch += ["--env", f"{key}={value}"]
        if members:
            launch += ["--type", "window", "--location", "last"]
        else:
            others = sum(1 for m in tabs.values() if m[0]["os_window_id"] == window and is_grid(m, marker))
            launch += ["--type", "tab", "--tab-title", name if not others else f"{name} {others + 1}"]
        launch += ["--", *[str(a) for a in argv]]
        before = {p["id"] for p in panes}
        current = client.resolve(identity["pane_id"], identity["broker"])
        if members and (current["tab_id"] != members[0]["tab_id"] or current.get("layout") != LAYOUT):
            raise control.ControlError("grid changed while placing; inspect before launching")
        raw = client.run(launch).strip()
        if not raw.isdecimal() or int(raw) in before or int(raw) <= 0:
            raise control.ControlError("launch returned no new pane ID; inspect state, do not blindly relaunch")
        created = client.resolve(int(raw))
        if (created["os_window_id"] != window
                or (members and created["tab_id"] != members[0]["tab_id"])
                or (not members and created["tab_id"] in tabs)):
            raise control.ControlError(f"created pane {raw} in unexpected geometry; retained for inspection")
        layout = LAYOUT
        fallback = None
        if not members:
            try:
                client.run(["goto-layout", "--match", f"window_id:{raw}", LAYOUT])
            except control.ControlError as exc:
                # An engine without the sessions layout: the pane stays in a
                # plain tab, which later launches will not treat as a grid.
                layout, fallback = created.get("layout", ""), f"engine lacks the {LAYOUT} layout: {exc}"
        snapshot = client.snapshot()
        created = next((p for p in snapshot if p["id"] == created["id"]), created)
        after = tabs_of(snapshot).get(created["tab_id"], [])
        count = occupancy(after) if after else 1
        ids = [p["id"] for p in after]
        index = ids.index(created["id"]) if created["id"] in ids else count - 1
        return {"pane": control.describe(created), "grid": name, "group": marker,
                "tab_id": created["tab_id"], "new_tab": not members, "layout": layout,
                "fallback": fallback, "count": count, "capacity": CAPACITY,
                "slot": list(slot(index, count)) if index < count else None, "argv": launch}


def open_tab(command, *, cwd, title, shell_string=""):
    """``kilix new-tab``/``tab new``: place a coding client in the default grid.

    A command whose program is a coding client (codex, claude, grok, ...)
    joins the newest session grid with room, unless the coding_session_grid
    setting is off or this shell is not in a Kilix pane; then None, and the
    caller opens a plain tab. A shell line counts when any of its words is such
    a program. The pane closes with its command, like a plain tab.
    """
    import shlex
    if shell_string:
        try:
            words = shlex.split(shell_string)
        except ValueError:
            return None
        programs = [Path(word).name for word in words if is_agent_command([word])]
        if not programs:
            return None
        argv, name = [os.environ.get("SHELL") or "/bin/sh", "-lc", shell_string], programs[0]
    elif is_agent_command(command):
        argv, name = list(command), Path(command[0]).name
    else:
        return None
    if not enabled():
        return None
    try:
        client = control.Client()
        if not client.caller:
            return None
        source = control.describe(client.resolve(client.caller))
    except (control.ControlError, OSError, ValueError):
        return None
    if not control.BROKER.fullmatch(source["broker"]):
        return None
    directory = os.getcwd() if cwd in ("", "current") else str(Path(cwd).expanduser().resolve(strict=True))
    return place(client, source=source, argv=argv, cwd=directory, title=title or name, hold=False)
