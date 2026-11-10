"""Ask adapter for the kilix-sensors reader in kilix-telemetry.

One whole request prints the current sysfs reading. The telemetry sampler
is not started, and the saved temperature unit is not written.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

NOT_SENSOR = 3

# Plain phrasings of a fan reading that the telemetry grammar does not list.
_FAN_QUESTION = re.compile(
    r"(?:how fast (?:is|are) (?:the |my )?(?:cpu |case )?fans?(?: spinning| running| going)?"
    r"|(?:what|how) (?:speed|rpm) (?:is|are) (?:the |my )?fans?(?: running| spinning| at)?"
    r"|(?:is|are) (?:the |my )?fans? (?:on|running|spinning)"
    r"|(?:how (?:are|is) )?(?:the |my )?fans?(?: speeds?)?"
    r")[?!.]*",
    re.IGNORECASE,
)


def reader():
    path = (
        Path(__file__).resolve().parents[1]
        / "third_party" / "kilix-telemetry" / "src"
        / "kilix_telemetry" / "sensors.py"
    )
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("kilix_sensors", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def saved_unit() -> str:
    override = os.environ.get("GPU_TERMINAL_SETTINGS_FILE")
    if override:
        path = Path(override).expanduser()
    else:
        root = os.environ.get("GPU_TERMINAL_HOME") or os.path.expanduser(
            "~/.local/gpu_terminal")
        path = Path(root).expanduser() / "settings.conf"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "fahrenheit"
    value = "fahrenheit"
    for line in lines:
        if line.startswith("KILIX_TEMPERATURE_UNIT="):
            candidate = line.split("=", 1)[1].strip().lower()
            if candidate in {"celsius", "fahrenheit"}:
                value = candidate
    return value


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["--ask"]:
        print("usage: kilix_sensors.py --ask TEXT", file=sys.stderr)
        return 2
    module = reader()
    if module is None:
        return NOT_SENSOR
    sysfs = Path(os.environ.get("KILIX_SENSORS_SYSFS", "/sys"))
    request = " ".join(argv[1:])
    if _FAN_QUESTION.fullmatch(request.strip()):
        request = "fan speed"
    try:
        status, text = module.answer(
            request, sysfs=sysfs, unit=saved_unit(),
        )
    except OSError as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    if status == 1:
        print(f"kilix ask: {text}", file=sys.stderr)
    elif text:
        print(text)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
