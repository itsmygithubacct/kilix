"""`kilix llm lab` — edit and measure a local transformer.

A blank `kilix llm` or `kilix llm lab` lists the options and does not load a
checkpoint. A command is handed to lab/scripts/lab.sh in the kilix-llm
checkout. That checkout is KILIX_LLM_HOME, else the kilix-llm directory beside
this tree, else $GPU_TERMINAL_SOURCE_HOME/kilix-llm.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

COMMANDS = (
    ("venv", "create the uv environment"),
    ("fetch", "download one checkpoint into HF_HOME (default Qwen/Qwen3.5-4B)"),
    ("means", "sad-minus-happy residual directions"),
    ("story", "steer a prompt and score the change"),
    ("neurons", "contrast MLP neurons on one layer"),
    ("neuron", "increase, decrease, activate, or deactivate one neuron"),
    ("bench", "compare answers before and after one weight edit"),
)

_LLM_USAGE = """usage: kilix llm lab [COMMAND] [ARGS...]
  lab    edit and measure a local transformer without fine-tuning
"""


def lab_usage() -> str:
    width = max(len(name) for name, _help in COMMANDS)
    lines = ["usage: kilix llm lab COMMAND [ARGS...]"]
    for name, help_text in COMMANDS:
        lines.append(f"  {name:<{width}}  {help_text}")
    return "\n".join(lines) + "\n"


def _candidate(path: str) -> Path | None:
    if not path or not path.startswith("/"):
        return None
    root = Path(path)
    script = root / "lab" / "scripts" / "lab.sh"
    if not script.is_file() or not os.access(script, os.X_OK):
        return None
    try:
        root_real = root.resolve()
        script_real = script.resolve()
    except OSError:
        return None
    if not script_real.is_relative_to(root_real):
        return None
    return script


def find_script() -> Path | None:
    """The lab dispatcher, or None when no checkout is configured."""
    override = os.environ.get("KILIX_LLM_HOME", "")
    if override:
        return _candidate(override)
    found = []
    home = os.environ.get("KILIX_HOME", "")
    if home.startswith("/"):
        found.append(str(Path(home).parent / "kilix-llm"))
    source = os.environ.get("GPU_TERMINAL_SOURCE_HOME", "")
    if source.startswith("/"):
        found.append(str(Path(source) / "kilix-llm"))
    for path in found:
        script = _candidate(path)
        if script is not None:
            return script
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        sys.stdout.write(_LLM_USAGE)
        return 0
    if args[0] != "lab":
        sys.stderr.write(_LLM_USAGE)
        return 2
    rest = args[1:]
    if not rest or rest[0] in ("-h", "--help", "help"):
        sys.stdout.write(lab_usage())
        return 0
    names = {name for name, _help in COMMANDS}
    if rest[0] not in names:
        sys.stderr.write(lab_usage())
        return 2
    script = find_script()
    if script is None:
        sys.stderr.write("kilix llm lab: kilix-llm checkout not found\n")
        sys.stderr.write(
            "Set KILIX_LLM_HOME to the checkout that contains lab/scripts/lab.sh.\n"
        )
        return 1
    os.execv(script, [str(script), *rest])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
