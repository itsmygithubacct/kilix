"""Shared GPU Terminal settings used by Kilix and its companion projects.

The file is deliberately a small ``KEY=value`` document rather than a shell
fragment.  Kilix, Kilix 95, Pleb, and Plebian-OS can therefore share settings
without executing user-controlled configuration as code.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import tempfile
from typing import Mapping


SETTINGS_BASENAME = "settings.conf"
SETTINGS_HEADER = "# GPU Terminal shared settings (KEY=value; not shell code)."
SETTINGS_MARKER = "# -- Kilix clickable chrome --"
GAMES_MARKER = "# -- Kilix game availability --"


@dataclass(frozen=True)
class ToggleSpec:
    key: str
    label: str
    section: str
    default: bool = True


TOP_BAR_TOGGLES = (
    ToggleSpec("KILIX_CHROME_VOLUME", "Volume", "Top bar"),
    ToggleSpec("KILIX_CHROME_NETWORK", "Network / Wi-Fi", "Top bar"),
    ToggleSpec("KILIX_CHROME_CALENDAR", "Calendar", "Top bar"),
    ToggleSpec("KILIX_CHROME_CLOCK", "Date and time", "Top bar"),
    ToggleSpec("KILIX_CHROME_BATTERY", "Battery", "Top bar"),
)

PANE_BUTTON_TOGGLES = (
    ToggleSpec("KILIX_CHROME_BUTTON_FONT_INCREASE", "Increase text size", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_FONT_DECREASE", "Decrease text size", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_SPLIT_LEFT", "Split pane left", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_SPLIT_UP", "Split pane up", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_SPLIT_DOWN", "Split pane down", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_SPLIT_RIGHT", "Split pane right", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_MAXIMIZE", "Maximize / restore pane", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_CLOSE", "Close pane", "Pane buttons"),
)

# Stable IDs cover the two built-in Kilix 95 games and every game-like entry
# in the pinned host content catalog.  Keeping these in the host SDK gives the
# CLI, TUI, built-in desktop, and external Kilix 95 provider one vocabulary.
GAME_TOGGLE_IDS = (
    ("minesweeper", "Minesweeper"),
    ("solitaire", "Solitaire"),
    ("doom", "Doom"),
    ("dosbox", "DOSBox"),
    ("bashed-earth", "Bashed Earth"),
    ("kilix-jpak", "Kilix JPAK"),
    ("kilix-rancher", "Kilix Rancher"),
    ("kilix-pong", "Kilix Pong"),
    ("kilix-lights", "Kilix Lights"),
    ("super-kilix", "Super Kilix"),
    ("joustix", "Joustix"),
    ("chess-bash", "Chess Bash"),
    ("kilix-fishtank", "Kilix Fishtank"),
    ("terminal-lander", "Terminal Lander"),
    ("kitty-brokeout", "Kitty Brokeout"),
)


def _game_setting_key(game_id: str) -> str:
    return "KILIX_GAME_" + game_id.upper().replace("-", "_")


GAME_TOGGLES = tuple(
    ToggleSpec(_game_setting_key(game_id), label, "Games")
    for game_id, label in GAME_TOGGLE_IDS
)
GAME_KEY_BY_ID = {
    game_id: spec.key
    for (game_id, _label), spec in zip(GAME_TOGGLE_IDS, GAME_TOGGLES)
}
GAME_ID_BY_KEY = {key: game_id for game_id, key in GAME_KEY_BY_ID.items()}

TOGGLE_SPECS = TOP_BAR_TOGGLES + PANE_BUTTON_TOGGLES + GAME_TOGGLES
TOGGLE_BY_KEY = {spec.key: spec for spec in TOGGLE_SPECS}
CLOCK_FORMAT_KEY = "KILIX_CHROME_CLOCK_FORMAT"
CLOCK_FORMAT_DEFAULT = "%Y-%m-%d %H:%M"
MANAGED_KEYS = tuple(spec.key for spec in TOGGLE_SPECS) + (CLOCK_FORMAT_KEY,)

_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def settings_path() -> str:
    """Return the one shared settings file used by every stack component."""
    override = os.environ.get("GPU_TERMINAL_SETTINGS_FILE")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    root = os.environ.get("GPU_TERMINAL_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "gpu_terminal")
    return os.path.join(os.path.abspath(os.path.expanduser(root)), SETTINGS_BASENAME)


def parse_text(text: str) -> dict[str, str]:
    """Parse a settings document, with the last assignment winning."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def read_text(path: str | None = None) -> tuple[str, bool]:
    target = path or settings_path()
    try:
        with open(target, encoding="utf-8", errors="replace") as stream:
            return stream.read(), True
    except OSError:
        return "", False


def truthy(value: object) -> bool:
    return str(value).strip().lower() not in (
        "", "0", "no", "false", "off", "disabled")


def defaults(*, migrate_environment: bool = False) -> dict[str, str]:
    values = {
        spec.key: "1" if spec.default else "0"
        for spec in TOGGLE_SPECS
    }
    values[CLOCK_FORMAT_KEY] = CLOCK_FORMAT_DEFAULT
    if migrate_environment:
        # Clock and battery were historically stored in kilix.env.  On the
        # first shared-file creation, preserve those effective preferences.
        for key in MANAGED_KEYS:
            if key in os.environ:
                values[key] = os.environ[key]
        if "KILIX_CHROME_CALENDAR" not in os.environ \
                and "KILIX_CHROME_CLOCK" in os.environ:
            values["KILIX_CHROME_CALENDAR"] = os.environ["KILIX_CHROME_CLOCK"]
    return values


def load(path: str | None = None) -> dict[str, str]:
    """Load effective values, falling back to defaults for absent keys."""
    text, exists = read_text(path)
    values = defaults(migrate_environment=not exists)
    if exists:
        values.update(parse_text(text))
    return values


def enabled(key: str, path: str | None = None) -> bool:
    spec = TOGGLE_BY_KEY.get(key)
    if spec is None:
        raise KeyError(f"unknown shared Kilix toggle: {key}")
    return truthy(load(path).get(key, "1" if spec.default else "0"))


def game_enabled(game_id: str, path: str | None = None) -> bool:
    """Return whether a Kilix game is exposed by desktop providers."""
    try:
        key = GAME_KEY_BY_ID[game_id]
    except KeyError as error:
        raise KeyError(f"unknown Kilix game: {game_id}") from error
    return enabled(key, path)


def game_availability(path: str | None = None) -> dict[str, bool]:
    """Return one consistent snapshot of all Kilix game selections."""
    values = load(path)
    return {
        game_id: truthy(values[key])
        for game_id, key in GAME_KEY_BY_ID.items()
    }


def _initial_text(values: Mapping[str, str]) -> str:
    lines = [SETTINGS_HEADER, "", SETTINGS_MARKER]
    for spec in TOP_BAR_TOGGLES:
        lines.append(f"{spec.key}={values[spec.key]}")
    lines.append(f"{CLOCK_FORMAT_KEY}={values[CLOCK_FORMAT_KEY]}")
    for spec in PANE_BUTTON_TOGGLES:
        lines.append(f"{spec.key}={values[spec.key]}")
    lines.extend(("", GAMES_MARKER))
    for spec in GAME_TOGGLES:
        lines.append(f"{spec.key}={values[spec.key]}")
    return "\n".join(lines) + "\n"


def ensure_file(path: str | None = None) -> str:
    """Create the shared file and add defaults introduced by newer hosts."""
    target = path or settings_path()
    if os.path.isfile(target) and not os.path.islink(target):
        os.chmod(target, 0o600, follow_symlinks=False)
        text, _exists = read_text(target)
        present = parse_text(text)
        missing = [spec.key for spec in TOGGLE_SPECS
                   if spec.key not in present]
        if missing:
            values = defaults()
            update({key: values[key] for key in missing}, target)
        return target
    if os.path.lexists(target):
        # A writer will atomically replace links, but startup must not silently
        # adopt a redirected source of truth.
        raise OSError(f"refusing unsafe shared settings path: {target}")
    directory = os.path.dirname(target)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    data = _initial_text(defaults(migrate_environment=True)).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError:
        if os.path.isfile(target) and not os.path.islink(target):
            return target
        raise OSError(f"refusing unsafe shared settings path: {target}")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if fd >= 0:
            os.close(fd)
    return target


def _set_value(text: str, key: str, value: str) -> str:
    value = value.replace("\r", " ").replace("\n", " ").strip()
    line = f"{key}={value}"
    pattern = re.compile(rf"^\s*{re.escape(key)}=.*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if matches:
        last = matches[-1]
        return text[:last.start()] + line + text[last.end():]
    marker = GAMES_MARKER if key in GAME_ID_BY_KEY else SETTINGS_MARKER
    if marker not in text:
        text = text.rstrip("\n") + f"\n\n{marker}\n"
    return text.rstrip("\n") + "\n" + line + "\n"


def update(changes: Mapping[str, object], path: str | None = None) -> str:
    """Atomically update managed keys while preserving comments/unknown keys."""
    unknown = set(changes) - set(MANAGED_KEYS)
    if unknown:
        raise KeyError(f"unknown shared setting(s): {', '.join(sorted(unknown))}")
    target = path or settings_path()
    text, exists = read_text(target)
    if not exists:
        text = _initial_text(defaults(migrate_environment=True))
    for key, raw_value in changes.items():
        if key in TOGGLE_BY_KEY:
            value = "1" if truthy(raw_value) else "0"
        else:
            value = str(raw_value) or CLOCK_FORMAT_DEFAULT
        text = _set_value(text, key, value)

    directory = os.path.dirname(target)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(target)}.", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = ""
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return target


__all__ = [
    "CLOCK_FORMAT_DEFAULT",
    "CLOCK_FORMAT_KEY",
    "GAMES_MARKER",
    "GAME_ID_BY_KEY",
    "GAME_KEY_BY_ID",
    "GAME_TOGGLE_IDS",
    "GAME_TOGGLES",
    "MANAGED_KEYS",
    "PANE_BUTTON_TOGGLES",
    "SETTINGS_BASENAME",
    "TOGGLE_BY_KEY",
    "TOGGLE_SPECS",
    "TOP_BAR_TOGGLES",
    "ToggleSpec",
    "defaults",
    "enabled",
    "ensure_file",
    "game_availability",
    "game_enabled",
    "load",
    "parse_text",
    "read_text",
    "settings_path",
    "truthy",
    "update",
]
