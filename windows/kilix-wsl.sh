#!/bin/sh
# Native Kilix command bridge for WSL. Windows adds this directory to the
# session PATH, which WSL imports when appendWindowsPath is enabled.
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
exec python3 -B "$root/wsl/companion.py" "$@"
