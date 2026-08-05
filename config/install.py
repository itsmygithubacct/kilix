"""`kilix install` — one list of everything this system can install.

Two collections that were previously only reachable in different places: the
pinned content catalog (games and applications, installed and verified by
kilix-content) and the coding agents (installed by their vendors' own scripts,
updated by their own updaters). A user looking for "what can I put on this
machine" should not have to know which of those a thing belongs to.

Nothing is installed here that is not already installed somewhere else in the
stack. The catalog half calls the same `Installer` the Kilix 95 Start menu
calls, so a launch and a typed command cannot end up running different builds.
The agent half runs the vendor command the rollout-resume tool already uses,
and prints it first — an install is never an opaque pipe to a shell.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kilix_sdk import content  # noqa: E402

# The desktop's games module already owns catalog installation: which root a
# kind installs under, the recorded install directory, the readiness check and
# the build. Driving it is what keeps a `kilix install` and a Start-menu launch
# on one implementation instead of two that drift.
_DESKTOP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "desktop")
if _DESKTOP not in sys.path:
    sys.path.insert(0, _DESKTOP)
try:
    import games as _games                      # noqa: E402
except Exception:                               # noqa: BLE001
    _games = None

def _providers_from_rollout():
    """The agent definitions from kilix-rollout, when that checkout is present.

    Those are the authoritative ones: the rollout-resume tool installs, updates
    and resumes each agent, so it is the thing that has to be right about their
    commands. Reading them here means one definition when both are installed,
    and the copy below is a fallback for a machine that never installed the
    utilities — not a second opinion.
    """
    import importlib.util
    source = os.environ.get("GPU_TERMINAL_SOURCE_HOME") or ""
    roots = []
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for base in (source, os.path.join(os.path.expanduser("~"), "gpu_terminal"),
                 os.path.dirname(os.path.dirname(here))):
        if base:
            roots.append(os.path.join(base, "kilix-desktops", "kilix-tui-utils",
                                      "src"))
    for root in roots:
        candidate = os.path.join(root, "kilix_rollout", "providers.py")
        if not os.path.isfile(candidate):
            continue
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from kilix_rollout import providers
        except Exception:                        # noqa: BLE001
            return None
        return tuple({
            "id": item.key,
            "label": item.label,
            "kind": "agent",
            "command": item.command,
            "install": item.install_shell,
            "update": tuple(item.update_argv),
            "source": item.install_source,
        } for item in providers.PROVIDERS)
    return None


# Fallback definitions, used only when the utilities are not installed. They
# drifted from the authoritative copy once already — Kimi updates with
# `upgrade`, not `update` — which is why the import above is tried first.
_FALLBACK_AGENTS = (
    {
        "id": "claude",
        "label": "Claude Code",
        "kind": "agent",
        "command": "claude",
        "install": "curl -fsSL https://claude.ai/install.sh | bash",
        "update": ("claude", "update"),
        "source": "https://code.claude.com/docs/en/quickstart",
    },
    {
        "id": "codex",
        "label": "Codex",
        "kind": "agent",
        "command": "codex",
        "install": "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
        "update": ("codex", "update"),
        "source": "https://developers.openai.com/codex/cli/",
    },
    {
        "id": "kimi",
        "label": "Kimi Code",
        "kind": "agent",
        "command": "kimi",
        "install": "curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash",
        "update": ("kimi", "upgrade"),
        "source": "https://moonshotai.github.io/kimi-code/",
    },
)

AGENTS = _providers_from_rollout() or _FALLBACK_AGENTS


def _catalog_rows() -> list[dict]:
    try:
        catalog = content.default_catalog()
    except Exception as error:                  # noqa: BLE001 - reported, not raised
        return [{"error": str(error)}]
    rows = []
    for entry in catalog:
        installed = False
        if _games is not None:
            try:
                installed = bool(_games.game_ready(entry.content_id))
            except Exception:                    # noqa: BLE001
                installed = False
        rows.append({
            "id": entry.content_id,
            "label": entry.label,
            "kind": entry.kind,
            "description": entry.description,
            "installed": installed,
        })
    return rows


def _agent_rows() -> list[dict]:
    rows = []
    for agent in AGENTS:
        found = shutil.which(agent["command"])
        rows.append({
            "id": agent["id"],
            "label": agent["label"],
            "kind": "agent",
            "description": f"Coding agent — {agent['source']}",
            "installed": bool(found),
            "path": found or "",
        })
    return rows


def rows() -> list[dict]:
    """Everything installable, catalog and agents together."""
    return _catalog_rows() + _agent_rows()


def _agent(identifier: str) -> dict | None:
    for agent in AGENTS:
        if agent["id"] == identifier:
            return agent
    return None


def install(identifier: str, *, assume_yes: bool = False) -> int:
    agent = _agent(identifier)
    if agent is not None:
        return _install_agent(agent, assume_yes=assume_yes)
    return _install_catalog(identifier)


def _install_agent(agent: dict, *, assume_yes: bool) -> int:
    # The command is shown before it runs. A vendor install script fetched over
    # the network and piped into a shell is worth reading first, and the user
    # cannot read what they were never shown.
    print(f"{agent['label']} installs with the vendor's own script:")
    print(f"    {agent['install']}")
    print(f"    documented at {agent['source']}")
    if not assume_yes:
        try:
            answer = input("run it? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("cancelled.")
            return 1
    shell = os.environ.get("SHELL") or "/bin/sh"
    return subprocess.run([shell, "-c", agent["install"]], check=False).returncode


def _install_catalog(identifier: str) -> int:
    if _games is None:
        print("kilix install: the desktop content module is unavailable",
              file=sys.stderr)
        return 2
    try:
        spec = content.default_catalog().require(identifier)
    except Exception as error:                   # noqa: BLE001
        print(f"kilix install: {error}", file=sys.stderr)
        return 2
    print(f"installing {spec.label} ({spec.kind})")
    try:
        _games.ensure(identifier)
    except SystemExit as error:
        print(f"kilix install: {error}", file=sys.stderr)
        return 1
    except Exception as error:                   # noqa: BLE001
        print(f"kilix install: {spec.label}: {error}", file=sys.stderr)
        return 1
    print(f"{spec.label} is installed.")
    return 0


def update(identifier: str) -> int:
    agent = _agent(identifier)
    if agent is None:
        # Catalog content is pinned; "updating" it means installing the pin the
        # current Kilix carries, which is what ensure() already does.
        return _install_catalog(identifier)
    command = shutil.which(agent["command"])
    if not command:
        print(f"{agent['label']} is not installed", file=sys.stderr)
        return 1
    argv = list(agent["update"])
    argv[0] = command
    print(f"updating {agent['label']}: {' '.join(argv)}")
    return subprocess.run(argv, check=False).returncode


def _print_table(entries: list[dict]) -> None:
    width = max((len(r.get("id", "")) for r in entries), default=2)
    kinds = ("agent", "app", "game")
    for kind in kinds:
        group = [r for r in entries if r.get("kind") == kind]
        if not group:
            continue
        print(f"\n{kind}s")
        for row in sorted(group, key=lambda r: r["label"].lower()):
            mark = "installed" if row.get("installed") else ""
            print(f"  {row['id']:<{width}}  {row['label']:<24} {mark}")
    print("\ninstall one with:  kilix install <id>")
    print("update one with:   kilix install --update <id>")


def main(argv: list[str]) -> int:
    args = list(argv)
    as_json = "--json" in args
    assume_yes = "--yes" in args or "-y" in args
    do_update = "--update" in args
    args = [a for a in args if a not in ("--json", "--yes", "-y", "--update",
                                         "--list")]
    entries = rows()
    failure = next((r for r in entries if "error" in r), None)
    if failure is not None:
        print(f"kilix install: {failure['error']}", file=sys.stderr)
        return 2
    if not args:
        if as_json:
            print(json.dumps(entries, indent=2))
        else:
            _print_table(entries)
        return 0
    identifier = args[0]
    known = {r["id"] for r in entries}
    if identifier not in known:
        print(f"kilix install: unknown item: {identifier}", file=sys.stderr)
        print("run `kilix install` for the list", file=sys.stderr)
        return 2
    return update(identifier) if do_update else install(identifier,
                                                        assume_yes=assume_yes)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
