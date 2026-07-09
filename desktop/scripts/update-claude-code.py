#!/usr/bin/env python3
"""Update Claude Code to the latest version."""

import argparse
import os
import re
import shutil
import subprocess
import sys

NPM_PKG = "@anthropic-ai/claude-code"
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:\.\d+)?")


def _run(cmd, capture=True):
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )


def _which(name):
    found = shutil.which(name)
    if found:
        return found
    home = os.path.expanduser("~")
    for base in (os.path.join(home, ".local", "bin"),
                 "/usr/local/bin", "/usr/bin"):
        path = os.path.join(base, name)
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return None


def native_version(claude_path):
    base = os.path.basename(os.path.realpath(claude_path))
    return base if VERSION_RE.fullmatch(base) else None


def cli_version(claude_path):
    try:
        cp = _run([claude_path, "--version"])
    except OSError:
        return None
    if cp.returncode != 0:
        return None
    m = VERSION_RE.search(cp.stdout or cp.stderr)
    return m.group(0) if m else None


def npm_version(npm):
    cp = _run([npm, "ls", "-g", "--depth=0", NPM_PKG])
    m = re.search(re.escape(NPM_PKG) + r"@(" + VERSION_RE.pattern + r")",
                  cp.stdout)
    return m.group(1) if m else None


def detect():
    claude_path = _which("claude")
    npm = _which("npm")
    if claude_path and "/claude/versions/" in os.path.realpath(claude_path):
        return "native", claude_path, npm
    if npm and npm_version(npm):
        return "npm", claude_path, npm
    if claude_path:
        return "native", claude_path, npm
    return None, claude_path, npm


def update_native(claude_path):
    before = native_version(claude_path) or cli_version(claude_path)
    print("Install method : native")
    print(f"Current version: {before or 'unknown'}")
    print("Running: claude update\n")
    rc = _run([claude_path, "update"], capture=False).returncode
    after = native_version(claude_path) or cli_version(claude_path)
    return rc, before, after


def update_npm(npm):
    before = npm_version(npm)
    print(f"Install method : npm global ({NPM_PKG})")
    print(f"Current version: {before or 'unknown'}")
    print(f"Running: npm install -g {NPM_PKG}@latest\n")
    rc = _run([npm, "install", "-g", f"{NPM_PKG}@latest"],
              capture=False).returncode
    after = npm_version(npm)
    return rc, before, after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", choices=["auto", "native", "npm"],
                        default="auto")
    args = parser.parse_args()

    method, claude_path, npm = detect()
    if args.method != "auto":
        method = args.method

    if method == "native":
        if not claude_path:
            print("error: native update requested but claude is not on PATH.",
                  file=sys.stderr)
            return 1
        rc, before, after = update_native(claude_path)
    elif method == "npm":
        if not npm:
            print("error: npm update requested but npm is not on PATH.",
                  file=sys.stderr)
            return 1
        rc, before, after = update_npm(npm)
    else:
        print("error: no Claude Code install found.", file=sys.stderr)
        return 1

    print()
    if rc != 0:
        print(f"Update command failed (exit {rc}).", file=sys.stderr)
        return rc
    if before and after and before == after:
        print(f"Already up to date at {after}.")
    elif after:
        print(f"Updated {before or '?'} -> {after}.")
    else:
        print("Update finished, but the new version could not be read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
