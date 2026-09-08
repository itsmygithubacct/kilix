#!/usr/bin/env bash
# Validate only the host TUI selector grammar, before any writable setup.
# A separate process keeps scratch state out of the large host dispatch graph.
set -euo pipefail
umask 077

_self="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
KILIX_HOME="$(cd "$(dirname "$_self")/.." && pwd)"
case "${1:-}" in
  tui|kilix-tui) shift ;;
  desktop)
    case "${2:-}" in tui|kilix-tui) ;; *) exit 0 ;; esac ;;
  *) exit 0 ;;
esac
selected=0 root=
if [ "${1:-}" = --content-root ]; then
  if [ "$#" -lt 2 ]; then
    echo "kilix tui: --content-root requires an absolute path" >&2
    exit 2
  fi
  selected=1; root="$2"; shift 2
fi
for argument in "$@"; do
  case "$argument" in
    --content-root|--content-root=*)
      echo "kilix tui: --content-root is allowed once, immediately after tui or kilix-tui" >&2
      exit 2 ;;
  esac
done
if [ "$selected" = 1 ]; then
  env KILIX_HOME="$KILIX_HOME" python3 "$KILIX_HOME/scripts/install-kilix-amp.py" \
    --print-root --content-root "$root" >/dev/null || exit 2
fi
