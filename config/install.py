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

Drivers are the third kind, and the same rule holds: `kilix install
nvidia-driver` runs the Plebian-OS helper that owns that install rather than
reimplementing it here. The row only appears on a machine that has the hardware.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kilix_sdk import content, paths  # noqa: E402
import content_app  # noqa: E402

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
    commands.

    The utilities are not an optional extra — Kilix installs them itself
    (`scripts/install-kilix-tui-utils.sh`, which Kilix calls for the TUI
    desktop and the tmux manager, and which `pleb install` runs), so on any
    provisioned system this import is the normal path. The copy below covers
    the window before that installer has run, not a machine that will never
    have them.

    `KILIX_TUI_UTILS_DIR` is the installer's own override and is honoured
    first; otherwise the default location it clones into is searched. Guessing
    only the default would silently fall back to the local copy on a system
    whose checkout was relocated.
    """
    source = os.environ.get("GPU_TERMINAL_SOURCE_HOME") or ""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = []
    if configured := os.environ.get("KILIX_TUI_UTILS_DIR"):
        roots.append(os.path.join(configured, "src"))
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

# Where the vendors' installers land their binaries when PATH cannot say:
# claude's install.sh links into ~/.local/bin, kimi's into ~/.kimi-code/bin
# (codex uses /usr/local/bin, which every PATH already carries). The same
# resolution contract as kilix_rollout.config.resolve_program — the rollout
# tool is authoritative about the agents, so this list must not drift from
# its. PATH alone is not enough here: desktop launch contexts routinely run
# without ~/.local/bin, and an agent that is installed but off-PATH must
# read as installed, not as absent.
_AGENT_PREFIX_BINDIRS = ("~/.local/bin", "~/.kimi-code/bin")


def _resolve_agent_command(command: str) -> str | None:
    """The agent's executable: PATH first, then the known landing spots."""
    found = shutil.which(command)
    if found:
        return found
    for bindir in _AGENT_PREFIX_BINDIRS:
        candidate = os.path.join(os.path.expanduser(bindir), command)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None

# Hardware drivers that are a deliberate opt-in rather than part of the image.
# The install itself belongs to Plebian-OS — its helper preflights the machine,
# picks the package with nvidia-detect, builds via DKMS and refuses hardware too
# old for any driver Debian still ships. Reimplementing any of that here would
# be a second opinion on a question that already has an owner.
DRIVERS = (
    {
        "id": "nvidia-driver",
        "label": "NVIDIA driver",
        "helper": "plebian-os-nvidia-driver",
        "description": "Proprietary NVIDIA driver — CUDA, NVENC/NVDEC, full clocks",
    },
)


def _nvidia_gpu_present() -> bool:
    """True when this machine has an NVIDIA display device.

    Matched on the display class specifically: an NVIDIA GPU also presents an
    audio function for HDMI/DisplayPort sound, which reports the same vendor.
    """
    lspci = shutil.which("lspci")
    if not lspci:
        return False
    try:
        result = subprocess.run([lspci], capture_output=True, text=True, check=False)
    except Exception:                            # noqa: BLE001
        return False
    for line in result.stdout.splitlines():
        low = line.lower()
        if "nvidia" in low and ("vga" in low or "3d controller" in low):
            return True
    return False


def _nvidia_driver_loaded() -> bool:
    """True when the proprietary module is the one actually loaded."""
    try:
        with open("/proc/modules", "r", encoding="utf-8") as handle:
            return any(line.startswith("nvidia ") for line in handle)
    except OSError:
        return False


def _driver_rows() -> list[dict]:
    # A machine with no NVIDIA card has nothing to decide here, so the row is
    # absent rather than listed-and-inapplicable. The Start menu and the TUI
    # render whatever this returns, so an empty list is how they stay clean too.
    if not _nvidia_gpu_present():
        return []
    rows = []
    for driver in DRIVERS:
        helper = shutil.which(driver["helper"])
        rows.append({
            "id": driver["id"],
            "label": driver["label"],
            "kind": "driver",
            "description": driver["description"],
            "installed": _nvidia_driver_loaded(),
            "path": helper or "",
        })
    return rows


def _catalog_installed(entry) -> bool:
    """Read installed state from the owner of each catalog content kind."""
    if entry.source_type == "system":
        try:
            plan = content.application_plan(
                entry.content_id,
                catalog=content.default_catalog(),
                launcher=os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "kilix",
                ),
            )
        except Exception:                         # noqa: BLE001
            return False
        command = plan.argv[0]
        if os.path.isabs(command):
            return os.path.isfile(command) and os.access(command, os.X_OK)
        return bool(shutil.which(command))
    if entry.kind == "app" and entry.source_type in {"git", "archive"}:
        root = os.path.join(paths.data_dir(), "desktop-apps")
        if not os.path.isdir(root):
            return False
        try:
            return bool(content.Installer(root).ready(entry))
        except Exception:                          # noqa: BLE001
            return False
    if _games is not None:
        try:
            return bool(_games.game_ready(entry.content_id))
        except Exception:                         # noqa: BLE001
            return False
    return False


def _shared_application_states(catalog) -> dict[str, bool]:
    """Batch package-provided app readiness by shared install identity."""
    groups: dict[str, list] = {}
    for entry in catalog:
        if entry.kind == "app" and entry.source_type in {"git", "archive"}:
            groups.setdefault(entry.install_id, []).append(entry)
    if not groups:
        return {}
    root = os.path.join(paths.data_dir(), "desktop-apps")
    if not os.path.isdir(root):
        return {
            entry.content_id: False
            for entries in groups.values()
            for entry in entries
        }
    installer = content.Installer(root)
    states: dict[str, bool] = {}
    for entries in groups.values():
        try:
            readiness = installer.ready_provided(entries)
        except Exception:                         # noqa: BLE001
            readiness = {}
        states.update(
            (entry.content_id, bool(readiness.get(entry.content_id)))
            for entry in entries
        )
    return states


def _catalog_rows() -> list[dict]:
    try:
        catalog = content.default_catalog()
    except Exception as error:                  # noqa: BLE001 - reported, not raised
        return [{"error": str(error)}]
    rows = []
    shared_states = _shared_application_states(catalog)
    for entry in catalog:
        installed = (
            shared_states[entry.content_id]
            if entry.content_id in shared_states
            else _catalog_installed(entry)
        )
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
        found = _resolve_agent_command(agent["command"])
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
    """Everything installable: catalog, agents and applicable drivers."""
    return _catalog_rows() + _agent_rows() + _driver_rows()


def _agent(identifier: str) -> dict | None:
    for agent in AGENTS:
        if agent["id"] == identifier:
            return agent
    return None


def _driver(identifier: str) -> dict | None:
    for driver in DRIVERS:
        if driver["id"] == identifier:
            return driver
    return None


def install(identifier: str, *, assume_yes: bool = False) -> int:
    driver = _driver(identifier)
    if driver is not None:
        return _install_driver(driver, assume_yes=assume_yes)
    agent = _agent(identifier)
    if agent is not None:
        return _install_agent(agent, assume_yes=assume_yes)
    return _install_catalog(identifier)


def _install_driver(driver: dict, *, assume_yes: bool) -> int:
    if not _nvidia_gpu_present():
        print("kilix install: no NVIDIA GPU on this machine — nothing to install",
              file=sys.stderr)
        return 2
    helper = shutil.which(driver["helper"])
    if not helper:
        print(f"kilix install: {driver['helper']} is not on PATH. It ships with "
              "Plebian-OS; on another distribution, install the driver the way "
              "that distribution expects.", file=sys.stderr)
        return 2
    # The helper decides whether this install should happen at all: it refuses
    # hardware too old for any driver Debian still ships, rather than leaving a
    # machine whose X server will not start. It needs root and asks for its own
    # confirmation, and the command is printed before it runs.
    argv = ["sudo", helper, "--install"]
    if assume_yes:
        argv.append("--yes")
    print(f"{driver['label']} installs with the Plebian-OS helper:")
    print(f"    {' '.join(argv)}")
    print("    it preflights the machine, and refuses if the GPU is too old")
    print("    for any driver Debian still ships. A reboot is required after.")
    return subprocess.run(argv, check=False).returncode


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
    status = subprocess.run([shell, "-c", agent["install"]],
                            check=False).returncode
    if status != 0:
        return status
    # The vendor script exiting 0 is its claim; whether the command actually
    # resolves is the fact. Verify, and when the binary landed at a known
    # prefix that PATH cannot see, say exactly that — a plain "installed"
    # followed by `command not found` in the next shell is how this class of
    # bug stayed invisible.
    resolved = _resolve_agent_command(agent["command"])
    if resolved is None:
        print(f"kilix install: the installer finished, but "
              f"`{agent['command']}` does not resolve on PATH or in "
              f"{', '.join(_AGENT_PREFIX_BINDIRS)} — read the installer "
              "output above for where it put things.", file=sys.stderr)
        return 1
    if shutil.which(agent["command"]) is None:
        print(f"{agent['label']} is installed at {resolved}, but that "
              "directory is not on this shell's PATH.")
        print("kilix pane shells put ~/.local/bin on PATH; open a new pane, "
              "or add the directory to PATH for this shell.")
        return 0
    print(f"{agent['label']} is installed: {resolved}")
    return 0


def _install_catalog(identifier: str) -> int:
    try:
        spec = content.default_catalog().require(identifier)
    except Exception as error:                   # noqa: BLE001
        print(f"kilix install: {error}", file=sys.stderr)
        return 2
    print(f"installing {spec.label} ({spec.kind})")
    try:
        if spec.kind == "app" and spec.source_type in {"git", "archive"}:
            content_app.ensure_application(spec, install=True)
        elif spec.kind == "app" and spec.source_type == "system":
            if not _catalog_installed(spec):
                raise RuntimeError("its system command is not installed")
        elif _games is not None:
            _games.ensure(identifier)
        else:
            raise RuntimeError("the desktop content module is unavailable")
    except SystemExit as error:
        print(f"kilix install: {error}", file=sys.stderr)
        return 1
    except Exception as error:                   # noqa: BLE001
        print(f"kilix install: {spec.label}: {error}", file=sys.stderr)
        return 1
    print(f"{spec.label} is installed.")
    return 0


def update(identifier: str) -> int:
    driver = _driver(identifier)
    if driver is not None:
        return _update_driver(driver)
    agent = _agent(identifier)
    if agent is None:
        # Catalog content is pinned; "updating" it means installing the pin the
        # current Kilix carries, which is what ensure() already does.
        return _install_catalog(identifier)
    command = _resolve_agent_command(agent["command"])
    if not command:
        print(f"{agent['label']} is not installed", file=sys.stderr)
        return 1
    argv = list(agent["update"])
    argv[0] = command
    print(f"updating {agent['label']}: {' '.join(argv)}")
    return subprocess.run(argv, check=False).returncode


def _update_driver(driver: dict) -> int:
    """A packaged driver has no separate updater — apt is its updater.

    Saying so is more useful than inventing an action: system upgrades carry
    new driver versions, and DKMS rebuilds the module on each kernel upgrade
    without being asked. Report the current state instead of pretending.
    """
    helper = shutil.which(driver["helper"])
    print(f"{driver['label']} is an apt package: system upgrades carry new")
    print("versions, and DKMS rebuilds its module on every kernel upgrade.")
    if not helper:
        return 0
    print(f"\ncurrent state ({helper} --status):")
    return subprocess.run([helper, "--status"], check=False).returncode


def _print_table(entries: list[dict]) -> None:
    width = max((len(r.get("id", "")) for r in entries), default=2)
    kinds = ("agent", "app", "game", "driver")
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
