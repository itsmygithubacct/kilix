#!/usr/bin/env bash
# Compatibility entry point: Kilix Memory now ships inside kilix-tui-utils.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PREFIX="${KILIX_TUI_UTILS_PREFIX:-${KILIX_MEMORY_PREFIX:-$HOME/.local}}"
PROVIDER="$KILIX_HOME/scripts/install-kilix-tui-utils.sh"

usage() {
  cat <<'EOF'
usage: install-kilix-memory.sh [--force|--print-refs]

Kilix Memory is provided by the single kilix-tui-utils checkout. --force is
accepted for compatibility; the unified installer always refreshes launchers.
EOF
}

case "${1:-}" in
  "") ;;
  --force) shift ;;
  --print-refs)
    shift
    [ $# -eq 0 ] || { usage >&2; exit 2; }
    printf 'kilix-tui-utils=%s\n' "$("$PROVIDER" --print-ref)"
    exit 0 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[ $# -eq 0 ] || { usage >&2; exit 2; }
[ -x "$PROVIDER" ] || {
  printf 'kilix memory: missing unified installer: %s\n' "$PROVIDER" >&2
  exit 1
}

KILIX_TUI_UTILS_PREFIX="$PREFIX" "$PROVIDER" --print-path >/dev/null
TARGET="$PREFIX/bin/kilix-memory"
[ -f "$TARGET" ] && [ ! -L "$TARGET" ] && [ -x "$TARGET" ] || {
  printf 'kilix memory: unified installer did not create %s\n' "$TARGET" >&2
  exit 1
}
