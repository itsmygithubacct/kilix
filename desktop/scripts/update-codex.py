#!/usr/bin/env python3
"""Update Codex CLI installs."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


VERSION_RE = re.compile(r"(\d+(?:\.\d+){0,3})")
PACKAGE_NAME = "@openai/codex"
INSTALLER_URL = "https://chatgpt.com/codex/install.sh"


@dataclass(frozen=True)
class Candidate:
    path: Path
    version_text: str
    version: tuple[int, ...]


@dataclass(frozen=True)
class NpmInstall:
    prefix: Path
    codex_path: Path


def dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []

    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def discover_codex_paths() -> list[Path]:
    paths: list[Path] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry:
            paths.append(Path(entry) / "codex")
    home = Path.home()
    paths.extend([
        home / ".local" / "bin" / "codex",
        Path("/usr/local/bin/codex"),
        Path("/usr/bin/codex"),
    ])
    whereis = shutil.which("whereis")
    if whereis:
        result = subprocess.run([whereis, "codex"], text=True,
                                capture_output=True, check=False)
        for token in result.stdout.split()[1:]:
            paths.append(Path(token))
    return [
        path
        for path in dedupe_paths(paths)
        if path.is_file() and os.access(path, os.X_OK)
    ]


def parse_version(text: str) -> tuple[int, ...]:
    match = VERSION_RE.search(text)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def get_candidate(path: Path) -> Candidate | None:
    try:
        result = subprocess.run([str(path), "--version"], text=True,
                                capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None

    output = (result.stdout or result.stderr).strip()
    version = parse_version(output)
    if result.returncode != 0 or not version:
        return None
    return Candidate(path=path, version_text=output, version=version)


def npm_install_for(path: Path) -> NpmInstall | None:
    try:
        real_path = path.resolve()
    except OSError:
        return None

    parts = real_path.parts
    suffix = ("lib", "node_modules", "@openai", "codex", "bin", "codex.js")
    if len(parts) < len(suffix) + 1 or tuple(parts[-len(suffix):]) != suffix:
        return None
    prefix = Path(*parts[:-len(suffix)])
    return NpmInstall(prefix=prefix, codex_path=real_path)


def discover_npm_installs() -> list[NpmInstall]:
    installs: list[NpmInstall] = []
    seen: set[Path] = set()
    for path in discover_codex_paths():
        install = npm_install_for(path)
        if install and install.prefix not in seen:
            seen.add(install.prefix)
            installs.append(install)
    return installs


def npm_command_for(prefix: Path) -> list[str]:
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm was not found in PATH")

    command = [
        npm,
        "install",
        "--global",
        "--prefix",
        str(prefix),
        f"{PACKAGE_NAME}@latest",
    ]

    target = prefix / "lib" / "node_modules"
    if os.geteuid() != 0 and not os.access(target, os.W_OK):
        sudo = shutil.which("sudo")
        if not sudo:
            raise RuntimeError(f"{prefix} is not writable and sudo is missing")
        command = [sudo, *command]
    return command


def update_install(install: NpmInstall) -> int:
    before = get_candidate(install.codex_path)
    before_version = before.version_text if before else "unknown"
    try:
        command = npm_command_for(install.prefix)
    except RuntimeError as exc:
        print(f"skip {install.prefix}: {exc}", file=sys.stderr)
        return 1

    print(f"\nUpdating {PACKAGE_NAME} at {install.prefix} ({before_version})")
    result = subprocess.run(command, check=False)
    after = get_candidate(install.codex_path)
    after_version = after.version_text if after else "unknown"
    if result.returncode == 0:
        print(f"Updated {install.prefix}: {before_version} -> {after_version}")
    else:
        print(f"Failed {install.prefix}: still {after_version}", file=sys.stderr)
    return result.returncode


def update_npm_installs() -> int:
    installs = discover_npm_installs()
    if not installs:
        return 2
    failures = 0
    for install in installs:
        if update_install(install) != 0:
            failures += 1
    return 1 if failures else 0


def update_with_installer() -> int:
    if not discover_codex_paths():
        print("codex update: no Codex executable found", file=sys.stderr)
        return 1
    if not shutil.which("curl"):
        print("codex update: curl is required for the standalone updater",
              file=sys.stderr)
        return 1
    print(f"Running standalone Codex updater from {INSTALLER_URL}")
    return subprocess.run(
        ["sh", "-c", f"curl -fsSL {INSTALLER_URL} | sh"],
        check=False,
    ).returncode


def main(argv: list[str]) -> int:
    method = argv[0] if argv else "auto"
    if method not in {"auto", "npm", "standalone"}:
        print("usage: update-codex.py [auto|npm|standalone]", file=sys.stderr)
        return 2
    if method in {"auto", "npm"}:
        rc = update_npm_installs()
        if method == "npm" or rc != 2:
            return rc
    return update_with_installer()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
