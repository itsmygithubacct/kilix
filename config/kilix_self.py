"""Direct Ask answers about Kilix itself: tabs, panes, agents, apps, games,
models, keyboard shortcuts, the desktop and the version.

Each answer is read through the interface that already owns it: the pane
snapshot from the SDK, the content catalog and its installer's readiness
check, the Games settings, Needle's own status report, the local model
service's list, and the map lines of this checkout's kitty.conf. Nothing
here opens, closes, installs, focuses or changes anything. Agent answers
name the program only, never its arguments. A request that is not entirely
one of these questions returns 3 so Ask keeps its normal route.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

NOT_SELF = 3
WIDTH = 78
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
# Agent programs Kilix starts in panes (agent_control.AGENTS) plus the
# executable name the Qwen agent runs as.
AGENT_ALIASES = {"omp": "qwen-omp"}


class SelfError(Exception):
    """A recognized question that could not be read."""


def _agents() -> tuple[str, ...]:
    from agent_control import AGENTS
    return tuple(AGENTS)


# --- sources (each one is a patch point for tests) -------------------------

def _state() -> list:
    try:
        from kilix_sdk.panes import PaneError, load_state
        return load_state(timeout=2.0)
    except PaneError as error:
        raise SelfError("no live Kilix session to read (" + str(error)[:120] + ")") from error
    except FileNotFoundError as error:
        raise SelfError("no live Kilix session to read") from error
    except OSError as error:
        raise SelfError(str(error)) from error


def _catalog():
    from kilix_sdk import content
    return content, content.default_catalog()


def _apps_root() -> str:
    from content_app import apps_root
    return apps_root()


def _app_state(installer, spec) -> str:
    """"ready" at this Kilix's catalog version, "other" when its folder holds
    something else (an older version or an unfinished install), or ""."""
    try:
        if installer.ready(spec):
            return "ready"
    except Exception:  # an unreadable entry is not reported as installed
        pass
    try:
        destination = Path(installer.destination(spec))
        return "other" if destination.is_dir() and any(destination.iterdir()) else ""
    except Exception:
        return ""


def _installed_apps() -> list[tuple[str, str, str]]:
    content, catalog = _catalog()
    installer = content.Installer(_apps_root())
    found = []
    for spec in catalog:
        if spec.kind != "app":
            continue
        state = _app_state(installer, spec)
        if state:
            found.append((spec.content_id, spec.label or spec.content_id, state))
    return found


def _games() -> list[tuple[str, str, bool]]:
    result = subprocess.run(
        [sys.executable, "-B", str(HERE.parent / "kilix-settings"), "--print-games"],
        capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        raise SelfError("could not read the games list")
    games = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"([a-z0-9][a-z0-9._-]*)=(on|off)\t(.+)", line.strip())
        if match:
            games.append((match[1], match[3], match[2] == "on"))
    return games


def _ollama_models() -> list[str] | None:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS, timeout=1.5) as response:
            data = json.loads(response.read(1 << 20))
    except (OSError, ValueError):
        return None
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return None
    return [m["name"] for m in models if isinstance(m, dict) and isinstance(m.get("name"), str)]


def _catalog_models(ollama: list[str] | None) -> list[tuple[str, str]]:
    """Catalog models whose declared files are all present at their sizes.

    A local-model-service entry counts only when that service lists it.
    """
    content, catalog = _catalog()
    installer = content.Installer(_apps_root())
    found = []
    for asset in catalog.assets:
        try:
            where = installer.asset_destination(asset)
            present = all(
                os.path.isfile(os.path.join(where, item.path))
                and os.path.getsize(os.path.join(where, item.path)) == item.bytes
                for item in asset.files)
        except (OSError, ValueError, AttributeError):
            present = False
        if not present:
            continue
        if asset.source_mode == "registry-manifest":
            name = (asset.label or "").split()[0].rstrip(".") if asset.label else ""
            if ollama is None or name not in ollama:
                continue
        found.append((asset.asset_id, asset.label or asset.asset_id))
    return found


def _ask_status() -> dict:
    result = subprocess.run(
        [str(HERE.parent / "kilix"), "app", "run", "kilix-needle", "--", "workflows", "status", "--json"],
        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, check=False,
        env={**os.environ, "KILIX_APP_AUTO_INSTALL": "0"})
    try:
        data = json.loads(result.stdout)
    except ValueError as error:
        raise SelfError("Needle is not installed, so Ask has no command model") from error
    if not isinstance(data, dict):
        raise SelfError("Needle returned an unexpected status")
    return data


def _keymap_text() -> str:
    return (HERE / "kitty.conf").read_text(encoding="utf-8", errors="replace")


def _version() -> str:
    try:
        version = (HERE.parent / "VERSION").read_text().strip()
    except OSError:
        version = "unknown"
    try:
        build = subprocess.run(["git", "-C", str(HERE.parent), "rev-parse", "--short=8", "HEAD"],
                               capture_output=True, text=True, timeout=3, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        build = ""
    return version + (f" (build {build})" if re.fullmatch(r"[0-9a-f]{8}", build) else "")


def _desktop() -> str:
    result = subprocess.run([str(HERE.parent / "kilix"), "default-desktop", "show"],
                            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, check=False)
    return result.stdout.strip() or "auto"


# --- formatting ------------------------------------------------------------

def _wrap(items: list[str], lead: str = "  ") -> list[str]:
    lines, line = [], lead
    for item in items:
        piece = item if line == lead else ", " + item
        if len(line) + len(piece) > WIDTH and line != lead:
            lines.append(line + ",")
            line = lead + item
        else:
            line += piece
    if line != lead:
        lines.append(line)
    return lines


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


# --- tabs, panes, agents ---------------------------------------------------

def _workspace(state: list):
    from kilix_sdk.panes import parse
    return parse(state)


def _tabs(state: list) -> str:
    workspace = _workspace(state)
    focused = workspace.me() or workspace.focused()
    lines = []
    windows = workspace.os_windows
    for os_window in windows:
        if len(windows) > 1:
            lines.append(f"Window {os_window.id}" + (" (focused)" if os_window.is_focused else ""))
        for number, tab in enumerate(os_window.tabs, 1):
            mark = "*" if tab.is_active else " "
            here = " <- you are here" if focused and any(p.id == focused.id for p in tab.panes) else ""
            title = tab.title or "(untitled)"
            lines.append(f"{mark} {number:>2}. {title[:52]} ({_plural(len(tab.panes), 'pane')}){here}")
    total = sum(len(w.tabs) for w in windows)
    lines.insert(0, f"{_plural(total, 'tab')} open, * marks the active tab:")
    return "\n".join(lines)


def _counts(state: list, what: str) -> str:
    workspace = _workspace(state)
    tabs = list(workspace.tabs())
    panes = list(workspace.panes())
    windows = len(workspace.os_windows)
    where = f" in {_plural(windows, 'window')}" if windows > 1 else ""
    if what == "tabs":
        return f"{_plural(len(tabs), 'tab')} open{where}, with {_plural(len(panes), 'pane')}."
    return f"{_plural(len(panes), 'pane')} open in {_plural(len(tabs), 'tab')}{where}."


def _focused(state: list, mine: bool = False) -> str:
    workspace = _workspace(state)
    pane = (workspace.me() if mine else None) or workspace.focused()
    if pane is None:
        return "No pane has focus right now."
    if mine and workspace.me() is not None:
        tab_number = next((n for w in workspace.os_windows for n, t in enumerate(w.tabs, 1)
                           if any(p.id == pane.id for p in t.panes)), 0)
        title = f" \"{pane.title[:50]}\"" if pane.title else ""
        return f"You are in pane {pane.id}{title}, tab {tab_number}."
    tab_number, tab_title = 0, ""
    for os_window in workspace.os_windows:
        for number, tab in enumerate(os_window.tabs, 1):
            if any(p.id == pane.id for p in tab.panes):
                tab_number, tab_title = number, tab.title
    detail = f"pane {pane.id}" + (f", running {pane.process}" if pane.process else "")
    title = f" \"{pane.title[:50]}\"" if pane.title else ""
    return f"The focused pane is{title} ({detail}) in tab {tab_number}" + \
        (f", \"{tab_title[:40]}\"." if tab_title else ".")


def _agent_rows(state: list) -> list[tuple[str, int, int, str]]:
    names = set(_agents())
    rows = []
    for os_window in state:
        for number, tab in enumerate(os_window.get("tabs") or [], 1):
            for window in tab.get("windows") or []:
                seen = None
                for process in window.get("foreground_processes") or []:
                    cmdline = process.get("cmdline") if isinstance(process, dict) else None
                    if not isinstance(cmdline, list) or not cmdline:
                        continue
                    program = os.path.basename(str(cmdline[0]))
                    program = AGENT_ALIASES.get(program, program)
                    if program in names:
                        seen = program
                        break
                if seen:
                    title = str(window.get("title") or "")[:46]
                    rows.append((seen, number, int(window.get("id") or 0), title))
    return rows


def _agents_text(state: list, count_only: bool = False) -> str:
    rows = _agent_rows(state)
    if not rows:
        return "No agents are running in Kilix panes."
    kinds: dict[str, int] = {}
    for program, *_ in rows:
        kinds[program] = kinds.get(program, 0) + 1
    summary = ", ".join(f"{count} {name}" for name, count in sorted(kinds.items()))
    head = f"{_plural(len(rows), 'agent')} running in Kilix panes: {summary}."
    if count_only:
        return head
    lines = [head]
    for program, tab, pane, title in rows:
        lines.append(f"  {program:<9} tab {tab:<2} pane {pane:<4} {title}")
    return "\n".join(lines)


# --- apps, games, models ---------------------------------------------------

def _apps_text() -> str:
    found = _installed_apps()
    name = lambda cid, label: f"{label} ({cid})" if label != cid else cid  # noqa: E731
    apps = [name(cid, label) for cid, label, state in found if state == "ready"]
    other = [name(cid, label) for cid, label, state in found if state == "other"]
    lines = [f"{_plural(len(apps), 'app')} installed from the Kilix catalog:" if apps else
             "No catalog apps are installed at this Kilix's version."]
    lines += _wrap(apps)
    if other:
        lines.append(f"Present, but not at this Kilix's version (older, or not finished installing): "
                     f"{len(other)}")
        lines += _wrap(other)
        lines.append("Update one with: kilix app install ID")
    lines.append("Open one with: kilix app run ID" if apps else "Install one with: kilix app install ID")
    return "\n".join(lines)


def _app_installed(name: str, *, exact: bool = False) -> tuple[int, str]:
    """exact: match the catalog id only, not a label or the id without "kilix-"."""
    content, catalog = _catalog()
    wanted = re.sub(r"\s+", "-", name.strip().casefold())
    spec = None
    for candidate in catalog:
        if candidate.kind not in ("app", "game"):
            continue
        names = {candidate.content_id.casefold()} if exact else {
            candidate.content_id.casefold(), (candidate.label or "").casefold(),
            re.sub(r"\s+", "-", (candidate.label or "").casefold())}
        if wanted in names or not exact and f"kilix-{wanted}" in names:
            spec = candidate
            break
    if spec is None:
        return NOT_SELF, ""
    state = _app_state(content.Installer(_apps_root()), spec)
    label = spec.label or spec.content_id
    if state == "ready":
        return 0, f"Yes, {label} ({spec.content_id}) is installed."
    verb = "kilix games play" if spec.kind == "game" else "kilix app install"
    if state == "other":
        return 0, (f"{label} ({spec.content_id}) is present, but not at this Kilix's version "
                   f"(older, or not finished installing). Update it with: {verb} {spec.content_id}")
    return 0, f"No, {label} ({spec.content_id}) is not installed. Install it with: {verb} {spec.content_id}"


def app_answer(name: str, *, exact: bool = False) -> str | None:
    """The catalog's answer to "is NAME installed", or None when no catalog app
    or game is called that. For the package question in kilix_local."""
    try:
        status, text = _app_installed(name, exact=exact)
    except Exception:  # no readable catalog: the package answer stands
        return None
    return text if status == 0 else None


def _games_text() -> str:
    games = _games()
    on = [label for _, label, enabled in games if enabled]
    off = [label for _, label, enabled in games if not enabled]
    if not games:
        return "No games are listed."
    lines = [f"{_plural(len(on), 'game')} in the Games menu:"]
    lines += _wrap(on)
    if off:
        lines.append(f"Turned off in settings: {', '.join(off)}")
    lines.append("Play one with: kilix games play GAME")
    return "\n".join(lines)


def _models_text() -> str:
    ollama = _ollama_models()
    models = _catalog_models(ollama)
    lines = [f"{_plural(len(models), 'catalog model')} with every file in place:"] if models else \
        ["No catalog models are installed."]
    lines += _wrap([cid for cid, _ in models])
    if ollama is None:
        lines.append("The local model service on 127.0.0.1:11434 did not answer.")
    elif ollama:
        lines.append(f"Local model service: {', '.join(ollama)}")
    else:
        lines.append("Local model service: no models pulled.")
    lines.append("Check one model's bytes with: kilix models verify ID")
    return "\n".join(lines)


def _ask_model_text() -> str:
    data = _ask_status()
    backend = str(data.get("backend") or "")
    assets = [a for a in data.get("assets") or [] if isinstance(a, dict)]
    names = ", ".join(f"{a.get('id')} ({a.get('version')})" for a in assets if a.get("id")) or backend or "none"
    state = "ready" if data.get("ready") else str(data.get("status") or "not ready")
    lines = [f"Ask routes requests with Needle's {backend or 'default'} command model: {names}; {state}.",
             "Direct answers (time, sums, weather, this computer, Kilix itself) use no model."]
    if not data.get("ready") and data.get("detail"):
        lines.append(str(data["detail"])[:200])
    return "\n".join(lines)


# --- keyboard shortcuts ----------------------------------------------------

_ACTIONS = (
    # (words the user types, predicate on the mapped action, description)
    (r"(?:open |make |create |start |get )?(?:a )?new tab|open (?:a )?tab", r"new_tab(?:_with_cwd)?\b", "new tab"),
    (r"close (?:the |this |a )?tab", r"close_tab\b", "close tab"),
    (r"(?:go to |switch to |move to )?(?:the )?next tab", r"next_tab\b", "next tab"),
    (r"(?:go to |switch to |move to )?(?:the )?previous tab|(?:the )?last tab", r"previous_tab\b", "previous tab"),
    (r"(?:go to|switch to|jump to) (?:a )?(?:tab|tab number)(?: \d+)?", r"goto_tab\b", "go to tab N"),
    (r"(?:rename|name|title) (?:the |this |a )?tab", r"set_tab_title\b", "rename tab"),
    (r"(?:move|reorder) (?:the |this )?tab", r"move_tab_(?:forward|backward)\b", "move tab"),
    (r"(?:split|split the pane|split the screen) (?:right|vertically|side by side)|(?:a )?vertical split",
     r"launch\b.*--location=vsplit", "split right"),
    (r"(?:split|split the pane|split the screen) (?:down|horizontally|below)|(?:a )?horizontal split",
     r"launch\b.*--location=hsplit", "split down"),
    (r"(?:open |make |create )?(?:a )?(?:new )?(?:pane|split)|split(?: the)? (?:pane|screen|window)",
     r"launch\b.*--location=", "new pane"),
    (r"close (?:the |this |a )?(?:pane|split)", r"kilix_close_persistent_window\b|close_window\b", "close pane"),
    (r"(?:move|switch|go) (?:between|to (?:another|the next|a different)) panes?|(?:switch|change) panes?|next pane",
     r"neighboring_window\b|next_window\b|previous_window\b", "move between panes"),
    (r"(?:zoom|maximi[sz]e|unzoom) (?:the |this |a )?pane", r"toggle_layout stack\b", "zoom pane"),
    (r"(?:change|switch|cycle|next) (?:the )?layout", r"next_layout\b", "next layout"),
    (r"move (?:the |this |a )?pane(?: left| right| up| down)?", r"move_window(?:_forward|_backward)?\b", "move pane"),
    (r"scroll(?:back)?", r"scroll_line_(?:up|down)\b", "scroll"),
    (r"resize (?:the |this |a )?(?:pane|split|window)", r"resize_window\b|start_resizing_window\b", "resize pane"),
    (r"(?:open |make |create )?(?:a )?new (?:os )?window", r"new_os_window(?:_with_cwd)?\b", "new window"),
    (r"(?:go |toggle |enter |exit )?full ?screen", r"toggle_fullscreen\b", "fullscreen"),
    (r"(?:start )?dictat(?:e|ion)|voice typing|speak to type", r"kilix_dictate\b", "dictate"),
    (r"broadcast|type in all panes", r"kitten broadcast\b", "broadcast to panes"),
    (r"(?:open the )?(?:switcher|switch sessions?|session switcher)", r"kilix switch\b", "switcher"),
    (r"scroll (?:up|down)", r"scroll_line_(?:up|down)\b", "scroll"),
    (r"copy(?: text)?", r"copy_to_clipboard\b|copy_or_interrupt\b", "copy"),
    (r"paste(?: text)?", r"paste_from_clipboard\b", "paste"),
    (r"(?:make (?:the )?(?:text|font) )?(?:bigger|smaller|larger)|(?:increase|decrease|change) (?:the )?font size|zoom (?:in|out)",
     r"change_font_size\b", "font size"),
    (r"tmux mode|(?:the )?tmux prefix", r"--new-mode tmux\b", "tmux mode"),
)
_DESCRIBE = tuple((re.compile(pattern), name) for _, pattern, name in _ACTIONS)


def _bindings() -> list[tuple[str, str, str]]:
    """(mode, keys, action) for every map line; later lines win, as in kitty."""
    found: dict[tuple[str, str], str] = {}
    for line in _keymap_text().splitlines():
        line = line.strip()
        if not line.startswith("map "):
            continue
        parts = line.split()[1:]
        mode = ""
        if parts[:1] == ["--new-mode"]:
            keys = parts[-1]
            found[("", keys)] = "--new-mode " + " ".join(parts[1:-1])
            continue
        if parts[:1] == ["--mode"] and len(parts) >= 3:
            mode, parts = parts[1], parts[2:]
        if len(parts) < 2:
            continue
        found[(mode, parts[0])] = " ".join(parts[1:])
    return [(mode, keys, action) for (mode, keys), action in found.items()]


def _shortcut(action_text: str) -> tuple[int, str]:
    wanted = action_text.strip().casefold()
    wanted = re.sub(r"^(?:to |for |a |an |the )+", "", wanted)
    wanted = re.sub(r"(?: in kilix| in the terminal| please)$", "", wanted).strip()
    for words, predicate, name in _ACTIONS:
        if not re.fullmatch(words, wanted):
            continue
        rule = re.compile(predicate)
        bindings = _bindings()
        prefix = next((keys for mode, keys, action in bindings if action.startswith("--new-mode tmux")), "")
        direct = [keys for mode, keys, action in bindings if not mode and rule.search(action)]
        in_tmux = [keys for mode, keys, action in bindings if mode == "tmux" and rule.search(action)]
        if not direct and not in_tmux:
            # Not set in Kilix's own config: the engine's defaults and the
            # guides cover it, so the help route answers instead.
            return NOT_SELF, ""
        lines = []
        if direct:
            lines.append(f"{name.capitalize()}: {' or '.join(direct)}")
        if in_tmux and prefix:
            lines.append(("In tmux mode: " if direct else f"{name.capitalize()} in tmux mode: ")
                         + f"{prefix}, then {' or '.join(in_tmux)}")
        return 0, "\n".join(lines)
    return NOT_SELF, ""


def _shortcuts_text() -> str:
    rows = []
    for mode, keys, action in _bindings():
        if mode:
            continue
        name = next((n for rule, n in _DESCRIBE if rule.search(action)), action.split()[0])
        rows.append((name, keys))
    rows.sort()
    lines = ["Kilix keyboard shortcuts from its kitty.conf:"]
    merged: dict[str, list[str]] = {}
    for name, keys in rows:
        merged.setdefault(name, []).append(keys)
    for name, keys in merged.items():
        wrapped = _wrap(keys, " " * 25)
        lines.append(f"  {name:<22} " + wrapped[0].lstrip())
        lines += wrapped[1:]
    lines.append("Ask about one: kilix ask \"what's the shortcut for a new tab?\"")
    return "\n".join(lines)


# --- version and desktop ---------------------------------------------------

def _version_text() -> str:
    return f"Kilix {_version()}, the terminal desktop of Plebian-OS."


def _desktop_text() -> str:
    session = os.environ.get("KILIX_DESKTOP_PROVIDER", "").strip()
    saved = _desktop()
    lines = [f"This is Kilix {_version()}, the desktop shell of Plebian-OS."]
    if session and session != saved:
        lines.append(f"This session uses the {session} desktop; new sessions start with {saved}.")
    else:
        lines.append(f"Desktop: {saved}. Choices: kilix default-desktop list")
    return "\n".join(lines)


# --- matching ----------------------------------------------------------------

_KILIX = r"(?:kilix|plebian(?:[- ]?os)?)"
_PATTERNS = (
    ("tabs", r"(?:what|which) tabs (?:are|do i have)(?: open)?(?: right now)?|what tabs|(?:what|which) tabs have i got open"
             r"|list (?:all |my |the )?(?:open )?tabs|show me (?:all |my |the )(?:open )?tabs|what(?:'s| is) open in kilix"),
    ("count-tabs", r"how many tabs(?: are| do i have)?(?: open)?(?: right now)?"),
    ("count-panes", r"how many (?:panes|splits)(?: are| do i have)?(?: open)?(?: right now)?"),
    ("focused", r"(?:which|what) (?:pane|split|window) (?:is|has) (?:focused|active|selected|in focus|the focus|focus)"
                r"|(?:which|what) (?:pane|split|tab) am i (?:in|on|using)|(?:which|what) tab is (?:active|focused|selected)"
                r"|what(?:'s| is) (?:the )?(?:focused|active|current|selected) (?:pane|split|tab)"),
    ("agents", r"(?:what|which) (?:ai |coding )?agents (?:are )?(?:running|open|active|working|up)(?: right now)?"
               r"|list (?:the |my |all )?(?:running |open )?(?:ai |coding )?agents"
               r"|are (?:there )?any (?:ai |coding )?agents running|is (?:any|an) (?:ai |coding )?agent running"),
    ("count-agents", r"how many (?:ai |coding )?agents (?:are )?(?:running|open|active)(?: right now)?"),
    ("apps", r"(?:what|which) (?:kilix )?(?:apps|applications) (?:are|do i have|do you have|have i got)(?: installed)?"
             r"|(?:what|which) (?:apps|applications) are installed|list (?:the |my |all )?(?:installed )?(?:kilix )?apps"),
    ("app-installed", r"is (?:the )?(?P<name>[a-z0-9][a-z0-9 .+-]{1,40}?) (?:app |game )?installed"),
    ("games", r"(?:what|which) games (?:are there|do you have|do i have|are installed|can i play|are available|have you got)"
              r"|list (?:the |my |all )?games|what games"),
    ("models", r"(?:what|which) (?:ai |local |kilix )?models (?:are|do i have|do you have|have i got)"
               r"(?: installed| downloaded| available| on this (?:computer|machine))?"
               r"|list (?:the |my |all )?(?:installed |local )?models|what models"),
    ("ask-model", r"(?:what|which) (?:ai )?model (?:does (?:kilix )?ask use|is (?:kilix )?ask using|do you use|are you using"
                  r"|are you|powers (?:kilix )?ask|runs (?:kilix )?ask)|what (?:are you|is ask) running on"),
    ("shortcut", r"(?:what(?:'s| is| are)|which (?:is|are)) (?:my |the )?(?:keyboard )?(?:shortcut|shortcuts|key|keys|keybinding"
                 r"|keybindings|hotkey|hotkeys|key combo|key combination)(?: (?:in kilix|in the terminal))? (?:for|to) (?P<action>.+)"
                 r"|(?:the )?(?:keyboard )?(?:shortcut|hotkey|keybinding) (?:for|to) (?P<action2>.+)"
                 r"|(?:what|which) keys? (?:do i press|should i press|do i use|opens?|makes?) (?:for |to )?(?P<action3>.+)"
                 r"|how do i (?P<action4>.+?) (?:with|from|using) the keyboard"),
    ("shortcuts", r"(?:what|which) (?:keyboard )?shortcuts (?:are there|do you have|does kilix have|are available|can i use)"
                  r"|list (?:the |all |my )?(?:keyboard )?shortcuts|(?:keyboard|kilix) shortcuts"
                  r"|what are (?:the |my )?(?:keyboard )?shortcuts"),
    ("version", rf"(?:what|which) version (?:of )?{_KILIX} (?:is this|am i (?:on|running|using)|is (?:installed|running|this))"
                rf"|(?:what|which) {_KILIX} version(?: is this| am i on| is installed)?|{_KILIX} version"
                rf"|what version (?:is this|of kilix)"),
    ("desktop", r"(?:what|which) desktop (?:is this|am i (?:on|using|running|in)|is (?:this|running|in use))"
                r"|what(?:'s| is) (?:my|the|this) (?:default )?desktop|what desktop"),
)


def classify(text: str) -> tuple[str, str] | None:
    text = re.sub(r"[?!.]+$", "", text.strip().replace("’", "'")).strip()
    # "tell me the shortcut for a new tab" is the question without its wrapper.
    text = re.sub(r"(?i)^(?:tell me|let me know)\s+(?=the |my |what |which |how )", "", text)
    for kind, pattern in _PATTERNS:
        match = re.fullmatch(pattern, text, re.IGNORECASE)
        if match:
            group = next((v for k, v in match.groupdict().items() if v), "")
            return kind, group
    return None


def answer(text: str) -> tuple[int, str]:
    found = classify(text)
    if found is None:
        return NOT_SELF, ""
    kind, value = found
    if kind == "tabs":
        return 0, _tabs(_state())
    if kind in ("count-tabs", "count-panes"):
        return 0, _counts(_state(), kind[6:])
    if kind == "focused":
        return 0, _focused(_state(), bool(re.search(r"(?i)\bam i\b", text)))
    if kind in ("agents", "count-agents"):
        return 0, _agents_text(_state(), kind == "count-agents")
    if kind == "apps":
        return 0, _apps_text()
    if kind == "app-installed":
        return _app_installed(value)
    if kind == "games":
        return 0, _games_text()
    if kind == "models":
        return 0, _models_text()
    if kind == "ask-model":
        return 0, _ask_model_text()
    if kind == "shortcut":
        return _shortcut(value)
    if kind == "shortcuts":
        return 0, _shortcuts_text()
    if kind == "version":
        return 0, _version_text()
    if kind == "desktop":
        return 0, _desktop_text()
    return NOT_SELF, ""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["--ask"]:
        print("usage: kilix_self.py --ask TEXT", file=sys.stderr)
        return 2
    try:
        status, text = answer(" ".join(argv[1:]))
    except (SelfError, subprocess.TimeoutExpired) as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    if text:
        print(text)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
