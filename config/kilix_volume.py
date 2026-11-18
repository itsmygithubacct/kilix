"""Ask adapter for the kilix-volume command.

A whole request checks or sets the default sink. The mixer is not opened.
Opening the mixer stays on the normal route.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

NOT_VOLUME = 3
_STEP = 10
_PREFIX = re.compile(r"^(?:please\s+)?", re.IGNORECASE)
_TRAIL = re.compile(r"[?!.]+$")
_READING = re.compile(r"(?P<level>\d{1,3})%(?P<muted> muted)?")
_MUTE = re.compile(r"(?P<mode>mute|unmute)(?: the volume)?", re.IGNORECASE)
_SET = re.compile(
    r"(?:set |make |turn )?(?:the )?volume (?:to |at )?(?P<level>\d{1,3})(?:%| percent)?",
    re.IGNORECASE,
)
_MOVE = re.compile(
    r"(?:turn |set )?(?:the )?volume (?P<way>up|down)",
    re.IGNORECASE,
)
_CHECK = re.compile(
    r"(?:show |check )?(?:the )?volume"
    r"|what(?:'s| is) the volume"
    r"|how loud is it",
    re.IGNORECASE,
)
_OUTPUT = re.compile(
    r"what(?:'s| is) the (?:audio|sound) output"
    r"|which (?:speakers|speaker|output|sink) (?:are|is) on"
    r"|what(?:'s| is) the default (?:sink|output|speaker|speakers)",
    re.IGNORECASE,
)
_SINK = re.compile(
    r"^\* (?P<level>\d{1,3}%(?: muted)?)  (?P<description>.+?)  (?P<name>\S+)$"
)


def clean(text: str) -> str:
    text = " ".join(text.strip().replace("’", "'").split())
    text = _TRAIL.sub("", text).strip()
    return _PREFIX.sub("", text).strip()


def classify(text: str) -> tuple[str, int] | None:
    """Return the volume action, or None when Ask should continue.

    The integer is the absolute percent, or the step direction for up and down.
    """
    text = clean(text)
    if not text:
        return None
    mute = _MUTE.fullmatch(text)
    if mute:
        return mute.group("mode").lower(), 0
    level = _SET.fullmatch(text)
    if level:
        return "set", int(level.group("level"))
    move = _MOVE.fullmatch(text)
    if move:
        return "step", _STEP if move.group("way").lower() == "up" else -_STEP
    if _OUTPUT.fullmatch(text):
        return "output", 0
    if _CHECK.fullmatch(text):
        return "get", 0
    return None


def volume_bin() -> Path | None:
    override = os.environ.get("KILIX_VOLUME_BIN")
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() and os.access(path, os.X_OK) else None
    prefix = os.environ.get("KILIX_TUI_UTILS_PREFIX") or os.path.expanduser("~/.local")
    path = Path(prefix).expanduser() / "bin" / "kilix-volume"
    if path.is_file() and os.access(path, os.X_OK):
        return path
    found = shutil.which("kilix-volume")
    return Path(found) if found else None


def _run(binary: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(binary), *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _reading(text: str) -> tuple[int, bool]:
    match = _READING.fullmatch(text.strip())
    if match is None:
        raise ValueError(text.strip())
    return int(match.group("level")), match.group("muted") is not None


def _fail(result: subprocess.CompletedProcess[str]) -> tuple[int, str]:
    message = (result.stderr or result.stdout).strip() or "volume control failed"
    return 1, message


def answer(text: str, binary: Path | None = None) -> tuple[int, str]:
    action = classify(text)
    if action is None:
        return NOT_VOLUME, ""
    binary = volume_bin() if binary is None else binary
    if binary is None:
        return 1, "kilix ask: volume control is not installed"
    kind, number = action
    if kind == "output":
        listed = _run(binary, ["sinks"])
        if listed.returncode != 0:
            return _fail(listed)
        for line in listed.stdout.splitlines():
            match = _SINK.fullmatch(line.rstrip())
            if match:
                return 0, match.group("description").strip()
        return 1, "kilix ask: no default output"
    if kind == "step":
        current = _run(binary, ["get"])
        if current.returncode != 0:
            return _fail(current)
        try:
            level, _muted = _reading(current.stdout)
        except ValueError:
            return 1, "kilix ask: volume control returned an unreadable level"
        number = max(0, min(150, level + number))
        kind = "set"
    if kind == "set":
        changed = _run(binary, ["set", str(number)])
        if changed.returncode != 0:
            return _fail(changed)
    elif kind in {"mute", "unmute"}:
        changed = _run(binary, [kind])
        if changed.returncode != 0:
            return _fail(changed)
    checked = _run(binary, ["get"])
    if checked.returncode != 0:
        return _fail(checked)
    return 0, checked.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["--ask"]:
        print("usage: kilix_volume.py --ask TEXT", file=sys.stderr)
        return 2
    try:
        status, text = answer(" ".join(argv[1:]))
    except (OSError, subprocess.SubprocessError) as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    if status == 1:
        print(text or "kilix ask: volume control failed", file=sys.stderr)
    elif text:
        print(text)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
