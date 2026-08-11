#!/usr/bin/env bash
# Run Kilix's pinned shared telemetry service and inspection CLI.
set -euo pipefail
umask 077

_self="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
KILIX_TELEMETRY_HOME="$(cd "$(dirname "$_self")/.." && pwd)"
KILIX_TELEMETRY_SOURCE="$KILIX_TELEMETRY_HOME/third_party/kilix-telemetry/src"

if [ ! -f "$KILIX_TELEMETRY_SOURCE/kilix_telemetry/__init__.py" ]; then
  echo "kilix telemetry: pinned component is unavailable; initialize submodules with:" >&2
  echo "  git -C $KILIX_TELEMETRY_HOME submodule update --init --recursive" >&2
  exit 1
fi

if [ -z "${KILIX_TELEMETRY_RUNTIME:-}" ]; then
  _storage="${KILIX_STORAGE_HOME:-${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}/kilix}"
  export KILIX_TELEMETRY_RUNTIME="${KILIX_SESSION_HOME:-$_storage/session}/telemetry"
fi

_python="${KILIX_TELEMETRY_PYTHON:-python3}"
_pythonpath="$KILIX_TELEMETRY_SOURCE${PYTHONPATH:+:$PYTHONPATH}"

case "${1:-status}" in
  start)
    shift || true
    [ $# -eq 0 ] || {
      echo "usage: kilix telemetry start" >&2
      exit 2
    }
    case "${KILIX_TELEMETRY_DISABLE:-}" in
      1|true|TRUE|yes|YES|on|ON) exit 0 ;;
    esac
    if env PYTHONPATH="$_pythonpath" "$_python" -m kilix_telemetry \
         status >/dev/null 2>&1; then
      exit 0
    fi
    nohup env PYTHONPATH="$_pythonpath" "$_python" -m kilix_telemetry \
      serve --quiet </dev/null >/dev/null 2>&1 &
    ;;
  *)
    exec env PYTHONPATH="$_pythonpath" "$_python" -m kilix_telemetry "$@" ;;
esac
