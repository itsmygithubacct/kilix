"""Direct Ask answers for time, resources, panes, and how-to questions.

These are closed reads. A request that is not entirely one of them returns
3 so Ask can keep its normal route. Nothing here launches a program, closes
a pane, or runs a suggested command.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

NOT_FACT = 3
HELP = 4

_PATH = r"/(?:[A-Za-z0-9_+.,@%=-]+/)*[A-Za-z0-9_+.,@%=-]*"
_PREFIX = re.compile(r"^(?:please\s+)?", re.IGNORECASE)
_TRAIL = re.compile(r"[?!.]+$")

_CLOCK = re.compile(
    r"what(?:'s| is) the time|what time is it(?: here)?",
    re.IGNORECASE,
)
_DATE = re.compile(
    r"what(?:'s| is) (?:today(?:'s date)?|the date)",
    re.IGNORECASE,
)
_SWAP = re.compile(
    r"(?:show |check )?(?:the )?swap(?: usage| status)?"
    r"|how much swap is (?:free|available)",
    re.IGNORECASE,
)
_MEMORY = re.compile(
    r"(?:show |check )?(?:memory|ram)(?: usage| status)?"
    r"|how much (?:memory|ram) is (?:free|available)",
    re.IGNORECASE,
)
_CPU = re.compile(
    r"(?:show |check )?(?:the )?(?:cpu load|load averages?)"
    r"|what(?:'s| is) (?:the |my )?(?:cpu load|load average)"
    r"|how(?:'s| is) (?:the |my )?cpu load",
    re.IGNORECASE,
)
_DISK = re.compile(
    rf"(?:(?:show |check )?(?:disk space|disk usage|free space)"
    rf"|how much (?:disk |disk space |space )is (?:left|free))"
    rf"(?: (?:on|for) (?P<path>{_PATH}|the root disk))?",
    re.IGNORECASE,
)
_PANES = re.compile(
    r"(?:what|which) panes are open|what panes do i have|"
    r"(?:list|show)(?: me)?(?: the)? panes",
    re.IGNORECASE,
)
_HELP = re.compile(
    r"how (?:do i|can i|to|does )\b|"
    r"what(?:'s| is) the command (?:to|for)\b|"
    r"is there a (?:way|command) to\b|where do i\b",
    re.IGNORECASE,
)


class FactError(Exception):
    """A recognized question that could not be read."""


def _clean(text: str) -> str:
    text = " ".join(text.strip().replace("’", "'").split())
    text = _TRAIL.sub("", text).strip()
    return _PREFIX.sub("", text).strip()


def classify(text: str) -> str | None:
    """Name the direct answer, or None when Ask should continue."""
    text = _clean(text)
    if not text:
        return None
    if _CLOCK.fullmatch(text):
        return "clock"
    if _DATE.fullmatch(text):
        return "date"
    if _SWAP.fullmatch(text):
        return "swap"
    if _MEMORY.fullmatch(text):
        return "memory"
    if _CPU.fullmatch(text):
        return "cpu"
    if _DISK.fullmatch(text):
        return "disk"
    if _PANES.fullmatch(text):
        return "panes"
    if re.match(_HELP, text) and not re.match(r"how (?:much|many)\b", text, re.I):
        return "help"
    return None


def _gib(nbytes: int) -> str:
    value = nbytes / (1024 ** 3)
    if value >= 100:
        return f"{value:.0f} GiB"
    return f"{value:.1f} GiB"


def _memory() -> str:
    found = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            found[key] = int(value.split()[0]) * 1024
    if set(found) != {"MemTotal", "MemAvailable"}:
        raise FactError("memory observation is incomplete")
    return f"{_gib(found['MemAvailable'])} available of {_gib(found['MemTotal'])}"


def _swap() -> str:
    found = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if key in ("SwapTotal", "SwapFree"):
            found[key] = int(value.split()[0]) * 1024
    if set(found) != {"SwapTotal", "SwapFree"}:
        raise FactError("swap observation is incomplete")
    if found["SwapTotal"] == 0:
        return "no swap"
    return f"{_gib(found['SwapFree'])} free of {_gib(found['SwapTotal'])}"


def _cpu() -> str:
    load = Path("/proc/loadavg").read_text(encoding="utf-8").split()
    cpus = os.cpu_count() or 0
    return f"load {load[0]}, {load[1]}, {load[2]} on {cpus} CPUs"


def _disk_path(text: str) -> str:
    match = _DISK.fullmatch(_clean(text))
    if match is None:
        raise FactError("name one disk")
    path = match["path"] or "/"
    if path == "the root disk":
        path = "/"
    if not re.fullmatch(_PATH, path):
        raise FactError("use an absolute path without spaces")
    return path


def _disk(text: str) -> str:
    path = _disk_path(text)
    import subprocess
    result = subprocess.run(
        ["/usr/bin/df", "--block-size=1", "--output=size,used,avail", "--", path],
        check=False, capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "query failed"
        raise FactError(detail[:500])
    lines = result.stdout.splitlines()
    if len(lines) != 2 or len(lines[1].split()) != 3:
        raise FactError("filesystem capacity observation is incomplete")
    total, _used, free = (int(part) for part in lines[1].split())
    return f"{_gib(free)} available of {_gib(total)} on {path}"


def _panes() -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from kilix_sdk.panes import PaneError, snapshot
        return snapshot(timeout=2.0).tree()
    except PaneError as error:
        raise FactError(str(error)) from error
    except FileNotFoundError as error:
        raise FactError("no live Kilix session to list") from error
    except OSError as error:
        raise FactError(str(error)) from error


def answer(text: str) -> tuple[int, str]:
    """Return a status and the text to print.

    Status 4 means the request is a how-to question. The caller runs Help
    search and does not run any command the passage names.
    """
    kind = classify(text)
    if kind is None:
        return NOT_FACT, ""
    if kind == "help":
        return HELP, ""
    now = datetime.now().astimezone()
    if kind == "clock":
        zone = now.tzname() or ""
        return 0, now.strftime("%Y-%m-%d %H:%M:%S") + (f" {zone}" if zone else "")
    if kind == "date":
        return 0, now.strftime("%A, %Y-%m-%d")
    if kind == "swap":
        return 0, _swap()
    if kind == "memory":
        return 0, _memory()
    if kind == "cpu":
        return 0, _cpu()
    if kind == "disk":
        return 0, _disk(text)
    if kind == "panes":
        return 0, _panes()
    return NOT_FACT, ""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["--ask"]:
        print("usage: kilix ask --ask TEXT", file=sys.stderr)
        return 2
    try:
        status, text = answer(" ".join(argv[1:]))
    except FactError as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    if text:
        print(text)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
