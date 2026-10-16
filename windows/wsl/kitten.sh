#!/bin/sh
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
exec python3 "$root/wsl/companion.py" "$@"
