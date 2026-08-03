#!/usr/bin/env python3
"""Print safe command names for installed graphical desktop applications.

This is intentionally a catalogue reader, not a launcher.  The Bash profile
still verifies that every printed name is a real PATH command before creating
an alias, and user aliases/functions continue to win.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil


TRUE = {"1", "true", "yes", "on"}
SAFE_COMMAND = re.compile(r"[A-Za-z0-9_+.-]+\Z")
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*\Z")

# Aliasing an interpreter, privilege boundary, desktop service, or generic
# dispatcher would change unrelated terminal commands.  Entries using one of
# these wrappers are skipped; /usr/bin/env is handled separately so a desktop
# entry such as `env FOO=1 real-app` can still contribute `real-app`.
WRAPPERS = {
    "bash", "dash", "dbus-launch", "dbus-run-session", "doas", "fish",
    "env", "flatpak", "gio", "gtk-launch", "java", "node", "nohup", "perl",
    "pkexec", "python", "python2", "python3", "ruby", "sh", "snap",
    "sudo", "systemd-run", "xdg-open", "zsh",
}
INFRASTRUCTURE = {
    "X", "Xorg", "Xvfb", "kitty", "kitten", "kilix", "openbox",
    "startx", "xinit",
}


def truthy(value: str) -> bool:
    return value.strip().lower() in TRUE


def application_directories() -> list[Path]:
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
        "~/.local/share")
    raw_data_dirs = os.environ.get("XDG_DATA_DIRS")
    data_dirs = (raw_data_dirs.split(":") if raw_data_dirs is not None
                 else ["/usr/local/share", "/usr/share"])
    roots = [Path(data_home), *(Path(item) for item in data_dirs if item)]
    return [root / "applications" for root in roots]


def read_entry(path: Path) -> dict[str, str]:
    wanted = {
        "Type", "Exec", "TryExec", "Terminal", "Hidden", "NoDisplay",
        "OnlyShowIn", "NotShowIn",
    }
    answer: dict[str, str] = {}
    section = ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return answer
    for raw in lines:
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "Desktop Entry" or not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key in wanted and key not in answer:
            answer[key] = value.strip()
    return answer


def command_from_exec(value: str) -> str | None:
    try:
        words = shlex.split(value, posix=True)
    except ValueError:
        return None
    if not words:
        return None

    executable = words.pop(0)
    if os.path.basename(executable) == "env":
        # Support the common, unambiguous env prefix without mistaking env
        # itself for a graphical application.  Skip entries using an option
        # whose argument rules we cannot safely infer.
        while words:
            word = words.pop(0)
            if word == "--":
                if not words:
                    return None
                executable = words.pop(0)
                break
            if ASSIGNMENT.fullmatch(word):
                continue
            if word in {"-i", "--ignore-environment", "-0", "--null"}:
                continue
            if word.startswith("--unset=") or word.startswith("--chdir="):
                continue
            if word in {"-u", "--unset", "-C", "--chdir"}:
                if not words:
                    return None
                words.pop(0)
                continue
            if word.startswith("-"):
                return None
            executable = word
            break
        else:
            return None

    command = os.path.basename(executable)
    if (not SAFE_COMMAND.fullmatch(command) or command in WRAPPERS
            or command in INFRASTRUCTURE):
        return None
    return command


def _desktop_list(value: str) -> set[str]:
    return {item for item in value.split(";") if item}


def current_desktops() -> set[str]:
    raw = (os.environ.get("XDG_CURRENT_DESKTOP")
           or os.environ.get("XDG_SESSION_DESKTOP") or "")
    return {item for item in raw.split(":") if item}


def entry_is_visible(entry: dict[str, str]) -> bool:
    if entry.get("Type") != "Application":
        return False
    if any(truthy(entry.get(key, ""))
           for key in ("Terminal", "Hidden", "NoDisplay")):
        return False
    desktops = current_desktops()
    only = _desktop_list(entry.get("OnlyShowIn", ""))
    blocked = _desktop_list(entry.get("NotShowIn", ""))
    if only and not desktops.intersection(only):
        return False
    if desktops.intersection(blocked):
        return False
    try_exec = entry.get("TryExec", "")
    if try_exec:
        executable = os.path.expanduser(try_exec)
        if os.path.isabs(executable):
            if not (os.path.isfile(executable)
                    and os.access(executable, os.X_OK)):
                return False
        elif shutil.which(executable) is None:
            return False
    return True


def installed_gui_commands() -> list[str]:
    commands: set[str] = set()
    seen_ids: set[str] = set()
    for directory in application_directories():
        if not directory.is_dir():
            continue
        try:
            paths = sorted(directory.rglob("*.desktop"))
        except OSError:
            continue
        for path in paths:
            try:
                desktop_id = str(path.relative_to(directory)).replace(os.sep, "-")
            except ValueError:
                continue
            # XDG precedence: a user entry (including Hidden=true) suppresses
            # the same desktop id in later system directories.
            if desktop_id in seen_ids:
                continue
            seen_ids.add(desktop_id)
            entry = read_entry(path)
            if not entry_is_visible(entry):
                continue
            command = command_from_exec(entry.get("Exec", ""))
            if command:
                commands.add(command)
    return sorted(commands)


if __name__ == "__main__":
    print(*installed_gui_commands(), sep="\n")
