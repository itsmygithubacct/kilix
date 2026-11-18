"""Ask launches kilix-alarm for one whole request to set an alarm.

How-to questions never reach this file. A request that is not an alarm
returns 3 so Ask can keep its normal route. The window that opens is the
alarm: flashing white, a quiet hum, a loud beep, or an alarm clock.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

NOT_ALARM = 3
_PREFIX = re.compile(r"^(?:please\s+)?", re.IGNORECASE)
_TRAIL = re.compile(r"[?!.]+$")
_OPEN = re.compile(
    r"(?:set|open|launch|start) (?:an |the |my )?(?:alarm clock|alarm)\b(?P<rest>.*)",
    re.IGNORECASE,
)
_AT = re.compile(
    r"(?:for|at) (?P<h>\d{1,2})(?::(?P<m>\d{2}))?(?: ?(?P<ampm>am|pm))?",
    re.IGNORECASE,
)
_IN = re.compile(
    r"in (?P<n>\d{1,4}) (?P<unit>minutes?|mins?|hours?|hrs?)",
    re.IGNORECASE,
)
_STYLE = re.compile(
    r"(?:with|using|on) (?:a |the )?"
    r"(?P<name>flashing white|white flash|low long quiet hum|quiet hum|"
    r"low hum|quiet beep|loud alert beep|loud alert|loud beep|alarm clock)",
    re.IGNORECASE,
)
_STYLES = {
    "flashing white": "flash",
    "white flash": "flash",
    "low long quiet hum": "hum",
    "quiet hum": "hum",
    "low hum": "hum",
    "quiet beep": "hum",
    "loud alert beep": "alert",
    "loud alert": "alert",
    "loud beep": "alert",
    "alarm clock": "clock",
}
_CLAUSES = (("at", _AT), ("in", _IN), ("style", _STYLE))


class AlarmRequestError(ValueError):
    """The request is an alarm, and the time in it cannot be used."""


@dataclass
class Request:
    at: str | None = None
    delay: int | None = None
    style: str | None = None

    def argv(self) -> list[str]:
        args: list[str] = []
        if self.at:
            args.extend(("--at", self.at))
        if self.delay is not None:
            args.extend(("--in", str(self.delay)))
        if self.style:
            args.extend(("--style", self.style))
        return args


def clean(text: str) -> str:
    text = " ".join(text.strip().replace("’", "'").split())
    text = _TRAIL.sub("", text).strip()
    return _PREFIX.sub("", text).strip()


def _clock(hour: int, minute: int, ampm: str | None) -> str:
    if minute > 59:
        raise AlarmRequestError("kilix ask: that time is not valid")
    if ampm:
        if not 1 <= hour <= 12:
            raise AlarmRequestError("kilix ask: that time is not valid")
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    elif not 0 <= hour <= 23:
        raise AlarmRequestError("kilix ask: that time is not valid")
    return f"{hour:02d}:{minute:02d}"


def _delay(amount: int, unit: str) -> int:
    seconds = amount * (3600 if unit.lower().startswith("h") else 60)
    if amount < 1 or seconds > 7 * 24 * 3600:
        raise AlarmRequestError("kilix ask: pick a time within the next 7 days")
    return seconds


def classify(text: str) -> Request | None:
    """The launch request, or None when Ask should continue."""
    text = clean(text)
    opened = _OPEN.fullmatch(text)
    if opened is None:
        return None
    rest = opened.group("rest").strip()
    request = Request()
    seen: set[str] = set()
    while rest:
        matched = False
        for name, pattern in _CLAUSES:
            found = pattern.match(rest)
            if found is None or not (found.end() == len(rest) or rest[found.end()] == " "):
                continue
            if name in seen:
                return None
            seen.add(name)
            if name == "at":
                request.at = _clock(
                    int(found.group("h")),
                    int(found.group("m") or 0),
                    (found.group("ampm") or "").lower() or None,
                )
            elif name == "in":
                request.delay = _delay(int(found.group("n")), found.group("unit"))
            else:
                request.style = _STYLES[found.group("name").lower()]
            rest = rest[found.end():].strip()
            matched = True
            break
        if not matched:
            return None
    if "at" in seen and "in" in seen:
        return None
    return request


def alarm_bin() -> Path | None:
    override = os.environ.get("KILIX_ALARM_BIN")
    if override is not None:
        path = Path(override).expanduser()
        return path if path.is_file() and os.access(path, os.X_OK) else None
    prefix = os.environ.get("KILIX_TUI_UTILS_PREFIX") or os.path.expanduser("~/.local")
    installed = Path(prefix).expanduser() / "bin" / "kilix-alarm"
    if installed.is_file() and os.access(installed, os.X_OK):
        return installed
    found = shutil.which("kilix-alarm")
    if found:
        return Path(found)
    checkout = os.environ.get("KILIX_TUI_UTILS_DIR")
    if checkout:
        script = Path(checkout).expanduser() / "tools" / "alarm" / "main.py"
        if script.is_file():
            return script
    return None


def answer(text: str) -> tuple[int, str]:
    try:
        request = classify(text)
    except AlarmRequestError as error:
        return 1, str(error)
    if request is None:
        return NOT_ALARM, ""
    binary = alarm_bin()
    if binary is None:
        return 1, "kilix ask: alarm is not installed"
    command = [str(binary), *request.argv()]
    if binary.suffix == ".py":
        command = [sys.executable, "-B", *command]
    status = subprocess.call(command)
    if status in {0, 1, 2}:
        return status, ""
    return 1, ""


def main(argv: list[str]) -> int:
    if argv[:1] != ["--ask"] or len(argv) != 2:
        print("usage: kilix_alarm.py --ask REQUEST", file=sys.stderr)
        return 2
    try:
        status, message = answer(argv[1])
    except OSError as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    if message and status == 1:
        print(message, file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
