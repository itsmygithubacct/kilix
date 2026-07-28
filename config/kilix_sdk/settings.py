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
SESSION_LOG_MARKER = "# -- Kilix session logging --"
GAMES_MARKER = "# -- Kilix game availability --"


@dataclass(frozen=True)
class ToggleSpec:
    key: str
    label: str
    section: str
    default: bool = True


TOP_BAR_TOGGLES = (
    ToggleSpec("KILIX_CHROME_TEMPERATURE", "Thermal status", "Top bar", default=False),
    ToggleSpec("KILIX_CHROME_VOLUME", "Volume", "Top bar"),
    ToggleSpec("KILIX_CHROME_NETWORK", "Network / Wi-Fi", "Top bar"),
    ToggleSpec("KILIX_CHROME_CALENDAR", "Calendar", "Top bar"),
    ToggleSpec("KILIX_CHROME_CLOCK", "Date and time", "Top bar"),
    ToggleSpec("KILIX_CHROME_BATTERY", "Battery", "Top bar"),
    # Only takes effect in a Pleb session, where the tab bar is the desktop's
    # only taskbar. An ordinary desktop already has a panel of its own.
    ToggleSpec("KILIX_CHROME_WINDOWS", "Native window taskbar (Pleb)", "Top bar"),
)

PANE_BUTTON_TOGGLES = (
    ToggleSpec("KILIX_CHROME_BUTTON_SYNCHRONIZE_INPUT", "Synchronize keyboard input", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_FONT_INCREASE", "Increase text size", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_FONT_DECREASE", "Decrease text size", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_SPLIT_LEFT", "Split pane left", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_SPLIT_UP", "Split pane up", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_SPLIT_DOWN", "Split pane down", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_SPLIT_RIGHT", "Split pane right", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_MAXIMIZE", "Maximize / restore pane", "Pane buttons"),
    ToggleSpec("KILIX_CHROME_BUTTON_CLOSE", "Close pane", "Pane buttons"),
)

# Session logging is on by default: the value of a transcript is that it was
# already running when something went wrong.  Logs are private to the user,
# bounded, and removable from every settings interface.
SESSION_LOG_TOGGLES = (
    ToggleSpec("KILIX_TRANSCRIPT", "Record pane session logs", "Session logging"),
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

TOGGLE_SPECS = (
    TOP_BAR_TOGGLES + PANE_BUTTON_TOGGLES + SESSION_LOG_TOGGLES + GAME_TOGGLES
)
TOGGLE_BY_KEY = {spec.key: spec for spec in TOGGLE_SPECS}
CLOCK_FORMAT_KEY = "KILIX_CHROME_CLOCK_FORMAT"
CLOCK_FORMAT_DEFAULT = "%Y-%m-%d %H:%M"
PANE_MEMORY_MODE_KEY = "KILIX_CHROME_PANE_MEMORY_MODE"
PANE_MEMORY_MODE_DEFAULT = "auto"
PANE_MEMORY_MODE_CHOICES = ("auto", "always", "off")

# Kitty ships images as APC sequences whose payload is base64 pixel data, so a
# pane running the desktop, a browser, or icat can emit megabytes per second.
# ``elide`` records a byte-count marker instead, keeping the transcript a
# readable record of text; ``keep`` captures the stream verbatim.
TRANSCRIPT_GRAPHICS_KEY = "KILIX_TRANSCRIPT_GRAPHICS"
TRANSCRIPT_GRAPHICS_DEFAULT = "elide"
TRANSCRIPT_GRAPHICS_CHOICES = ("elide", "keep")

# Presets rather than a free-form number: every settings interface shares one
# vocabulary, and an unrecognised value would silently read back as the default.
# Stored as human tokens so a dropdown, a TUI cycle, and the file itself all
# read the same way; only the broker needs the byte count.
TRANSCRIPT_LIMIT_KEY = "KILIX_TRANSCRIPT_MAX_SIZE"
TRANSCRIPT_LIMIT_DEFAULT = "8M"
TRANSCRIPT_LIMIT_CHOICES = ("2M", "8M", "32M", "128M")

# The per-pane cap above bounds one file; these bound the directory. Without
# them a long-running kiosk grows without limit, because panes come and go and
# nothing reclaims a dead pane's log.
#
# Every stored transcript is compressed. A pane's log is written plain while the
# pane lives — the broker appends to it and drops the oldest bytes on overflow,
# neither of which works on a compressed stream — and is compressed with
# zstd -3 after the pane dies. Terminal output stores at roughly a
# sixtieth of its size at that level, and decompression is about half a second
# per 400 MB, so reading a recent transcript is effectively free.
TRANSCRIPT_TOTAL_KEY = "KILIX_TRANSCRIPT_MAX_TOTAL"
TRANSCRIPT_TOTAL_DEFAULT = "20G"
TRANSCRIPT_TOTAL_CHOICES = ("1G", "5G", "10G", "20G", "50G", "100G")

# Past that budget the oldest are recompressed at zstd -9, which reaches about
# 116x for roughly three seconds of CPU per 400 MB. Level costs compression
# time, not recall: -9 and -19 both decompress in well under a second, the same
# as -3, so a denser older tier is not a slower one. ``off`` deletes instead of
# recompressing, for operators who want a hard ceiling and no history.
TRANSCRIPT_ARCHIVE_KEY = "KILIX_TRANSCRIPT_ARCHIVE_MAX_TOTAL"
TRANSCRIPT_ARCHIVE_DEFAULT = "10G"
TRANSCRIPT_ARCHIVE_CHOICES = ("off", "1G", "5G", "10G", "20G", "50G", "100G")

MANAGED_KEYS = tuple(spec.key for spec in TOGGLE_SPECS) + (
    CLOCK_FORMAT_KEY,
    PANE_MEMORY_MODE_KEY,
    TRANSCRIPT_GRAPHICS_KEY,
    TRANSCRIPT_LIMIT_KEY,
    TRANSCRIPT_TOTAL_KEY,
    TRANSCRIPT_ARCHIVE_KEY,
)

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
    values[PANE_MEMORY_MODE_KEY] = PANE_MEMORY_MODE_DEFAULT
    values[TRANSCRIPT_GRAPHICS_KEY] = TRANSCRIPT_GRAPHICS_DEFAULT
    values[TRANSCRIPT_LIMIT_KEY] = TRANSCRIPT_LIMIT_DEFAULT
    values[TRANSCRIPT_TOTAL_KEY] = TRANSCRIPT_TOTAL_DEFAULT
    values[TRANSCRIPT_ARCHIVE_KEY] = TRANSCRIPT_ARCHIVE_DEFAULT
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
    lines.append(f"{PANE_MEMORY_MODE_KEY}={values[PANE_MEMORY_MODE_KEY]}")
    lines.extend(("", SESSION_LOG_MARKER))
    for spec in SESSION_LOG_TOGGLES:
        lines.append(f"{spec.key}={values[spec.key]}")
    lines.append(f"{TRANSCRIPT_GRAPHICS_KEY}={values[TRANSCRIPT_GRAPHICS_KEY]}")
    lines.append(f"{TRANSCRIPT_LIMIT_KEY}={values[TRANSCRIPT_LIMIT_KEY]}")
    lines.append(f"{TRANSCRIPT_TOTAL_KEY}={values[TRANSCRIPT_TOTAL_KEY]}")
    lines.append(f"{TRANSCRIPT_ARCHIVE_KEY}={values[TRANSCRIPT_ARCHIVE_KEY]}")
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
        missing = [key for key in MANAGED_KEYS if key not in present]
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
        elif key == PANE_MEMORY_MODE_KEY:
            value = str(raw_value).strip().lower()
            if value not in PANE_MEMORY_MODE_CHOICES:
                choices = ", ".join(PANE_MEMORY_MODE_CHOICES)
                raise ValueError(
                    f"{PANE_MEMORY_MODE_KEY} must be one of: {choices}")
        elif key == TRANSCRIPT_GRAPHICS_KEY:
            value = str(raw_value).strip().lower()
            if value not in TRANSCRIPT_GRAPHICS_CHOICES:
                choices = ", ".join(TRANSCRIPT_GRAPHICS_CHOICES)
                raise ValueError(
                    f"{TRANSCRIPT_GRAPHICS_KEY} must be one of: {choices}")
        elif key == TRANSCRIPT_LIMIT_KEY:
            value = str(raw_value).strip().upper()
            if value not in TRANSCRIPT_LIMIT_CHOICES:
                choices = ", ".join(TRANSCRIPT_LIMIT_CHOICES)
                raise ValueError(
                    f"{TRANSCRIPT_LIMIT_KEY} must be one of: {choices}")
        elif key == TRANSCRIPT_TOTAL_KEY:
            value = str(raw_value).strip().upper()
            if value not in TRANSCRIPT_TOTAL_CHOICES:
                choices = ", ".join(TRANSCRIPT_TOTAL_CHOICES)
                raise ValueError(
                    f"{TRANSCRIPT_TOTAL_KEY} must be one of: {choices}")
        elif key == TRANSCRIPT_ARCHIVE_KEY:
            value = str(raw_value).strip()
            value = "off" if value.lower() == "off" else value.upper()
            if value not in TRANSCRIPT_ARCHIVE_CHOICES:
                choices = ", ".join(TRANSCRIPT_ARCHIVE_CHOICES)
                raise ValueError(
                    f"{TRANSCRIPT_ARCHIVE_KEY} must be one of: {choices}")
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


def pane_memory_mode(path: str | None = None) -> str:
    """Return the normalized per-pane memory-chip visibility policy."""
    value = load(path).get(
        PANE_MEMORY_MODE_KEY, PANE_MEMORY_MODE_DEFAULT).strip().lower()
    return (
        value if value in PANE_MEMORY_MODE_CHOICES
        else PANE_MEMORY_MODE_DEFAULT
    )


def transcript_enabled(path: str | None = None) -> bool:
    """Return whether panes record a durable session log."""
    return enabled("KILIX_TRANSCRIPT", path)


def transcript_graphics(path: str | None = None) -> str:
    """Return the normalized graphics-payload policy for transcripts."""
    value = load(path).get(
        TRANSCRIPT_GRAPHICS_KEY, TRANSCRIPT_GRAPHICS_DEFAULT).strip().lower()
    return (
        value if value in TRANSCRIPT_GRAPHICS_CHOICES
        else TRANSCRIPT_GRAPHICS_DEFAULT
    )


def transcript_limit(path: str | None = None) -> int:
    """Return the per-pane transcript size budget in bytes."""
    value = load(path).get(
        TRANSCRIPT_LIMIT_KEY, TRANSCRIPT_LIMIT_DEFAULT).strip().upper()
    if value not in TRANSCRIPT_LIMIT_CHOICES:
        value = TRANSCRIPT_LIMIT_DEFAULT
    return _size_bytes(value)


def _size_bytes(token: str) -> int:
    """Convert a settings size token such as ``8M`` or ``20G`` to bytes."""
    token = token.strip().upper()
    for suffix, scale in (("G", 1024 ** 3), ("M", 1024 ** 2), ("K", 1024)):
        if token.endswith(suffix):
            return int(token.removesuffix(suffix)) * scale
    return int(token)


def transcript_total(path: str | None = None) -> int:
    """Return the recent zstd -3 transcript-tier budget in bytes."""
    value = load(path).get(
        TRANSCRIPT_TOTAL_KEY, TRANSCRIPT_TOTAL_DEFAULT).strip().upper()
    if value not in TRANSCRIPT_TOTAL_CHOICES:
        value = TRANSCRIPT_TOTAL_DEFAULT
    return _size_bytes(value)


def transcript_archive_total(path: str | None = None) -> int:
    """Return the older zstd -9 tier budget; ``0`` disables that tier.

    ``off`` means a transcript leaving the recent tier is deleted instead of
    recompressed, giving the transcript tree a hard recent-tier ceiling.
    """
    value = load(path).get(
        TRANSCRIPT_ARCHIVE_KEY, TRANSCRIPT_ARCHIVE_DEFAULT).strip()
    value = "off" if value.lower() == "off" else value.upper()
    if value not in TRANSCRIPT_ARCHIVE_CHOICES:
        value = TRANSCRIPT_ARCHIVE_DEFAULT
    return 0 if value == "off" else _size_bytes(value)


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
    "PANE_MEMORY_MODE_CHOICES",
    "PANE_MEMORY_MODE_DEFAULT",
    "PANE_MEMORY_MODE_KEY",
    "SETTINGS_BASENAME",
    "SESSION_LOG_MARKER",
    "SESSION_LOG_TOGGLES",
    "TOGGLE_BY_KEY",
    "TOGGLE_SPECS",
    "TOP_BAR_TOGGLES",
    "TRANSCRIPT_ARCHIVE_CHOICES",
    "TRANSCRIPT_ARCHIVE_DEFAULT",
    "TRANSCRIPT_ARCHIVE_KEY",
    "TRANSCRIPT_GRAPHICS_CHOICES",
    "TRANSCRIPT_GRAPHICS_DEFAULT",
    "TRANSCRIPT_GRAPHICS_KEY",
    "TRANSCRIPT_LIMIT_CHOICES",
    "TRANSCRIPT_LIMIT_DEFAULT",
    "TRANSCRIPT_LIMIT_KEY",
    "TRANSCRIPT_TOTAL_CHOICES",
    "TRANSCRIPT_TOTAL_DEFAULT",
    "TRANSCRIPT_TOTAL_KEY",
    "ToggleSpec",
    "defaults",
    "enabled",
    "ensure_file",
    "game_availability",
    "game_enabled",
    "load",
    "pane_memory_mode",
    "parse_text",
    "read_text",
    "settings_path",
    "transcript_archive_total",
    "transcript_enabled",
    "transcript_graphics",
    "transcript_limit",
    "transcript_total",
    "truthy",
    "update",
]
