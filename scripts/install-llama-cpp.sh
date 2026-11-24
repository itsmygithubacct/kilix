#!/usr/bin/env bash
# The llama.cpp runtime row of `kilix install`: local models through kilix-llm.
#
# kilix-llm is pinned by scripts/install-kilix-llm.sh and builds llama.cpp on
# first use, so nothing here is needed before a first chat. This helper is
# what the software lists call: --install does the first use's work now, and
# --upgrade moves to llama.cpp's newest release (kilix-llm update), keeping
# the build it replaces for `kilix llama rollback`.
set -euo pipefail

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KILIX_LLM_PREFIX="${KILIX_LLM_PREFIX:-$HOME/.local}"
LAUNCHER="$KILIX_LLM_PREFIX/bin/kilix-llm"

die() { printf 'kilix llama: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix llama: %s\n' "$*" >&2; }

usage() {
  cat <<'USAGE'
usage: install-llama-cpp.sh [--print-path|--check|--install|--upgrade] [--yes]

  --print-path  the kilix-llm command this installs
  --check       exit 0 when installed (and say whether llama.cpp has a newer
                release), 1 when not
  --install     install the pinned kilix-llm and build llama.cpp now: the CPU
                backend, plus Vulkan when a GPU and glslc are present
  --upgrade     refresh kilix-llm to this Kilix's pin, then build llama.cpp's
                newest release and switch to it (undo: kilix llama rollback)
  --yes         accepted for the software lists; nothing here asks
USAGE
}

action=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --print-path|--check|--install|--upgrade)
      [ -z "$action" ] || { usage >&2; exit 2; }
      action=$1; shift ;;
    --yes|-y) shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[ -n "$action" ] || { usage >&2; exit 2; }

# The backends a first chat would need on this machine. Vulkan is only worth
# a build when there is a GPU render node to run it on.
backends() {
  printf '%s\n' cpu
  if command -v glslc >/dev/null 2>&1 && compgen -G '/dev/dri/renderD*' >/dev/null; then
    printf '%s\n' vulkan
  fi
}

case "$action" in
  --print-path)
    printf '%s\n' "$LAUNCHER" ;;
  --check)
    if [ ! -x "$LAUNCHER" ]; then
      echo "llama.cpp: not installed (kilix llama install, or the first kilix llama chat)"
      exit 1
    fi
    "$LAUNCHER" runtime || exit 1
    # Reaching upstream is a convenience; offline, the install is still fine.
    status=0
    "$LAUNCHER" update --check || status=$?
    [ "$status" = 0 ] || [ "$status" = 10 ] || log "could not check for a newer llama.cpp release"
    exit 0 ;;
  --install)
    "$KILIX_HOME/scripts/install-kilix-llm.sh"
    [ -x "$LAUNCHER" ] || die "the kilix-llm installer did not create $LAUNCHER"
    while read -r backend; do
      log "building the $backend llama.cpp runtime (a few minutes the first time)"
      if "$LAUNCHER" build --backend "$backend" --if-needed; then
        continue
      fi
      # The GPU backend is an extra: without it, chat runs on the CPU, and
      # the first GPU use builds it again once its packages are there.
      [ "$backend" = cpu ] \
        || { log "the $backend runtime was not built (see above); chat still runs on the CPU"; continue; }
      die "the $backend build failed; rerun: kilix-llm build --backend $backend"
    done < <(backends)
    "$LAUNCHER" runtime ;;
  --upgrade)
    "$KILIX_HOME/scripts/install-kilix-llm.sh"
    [ -x "$LAUNCHER" ] || die "the kilix-llm installer did not create $LAUNCHER"
    args=()
    while read -r backend; do args+=(--backend "$backend"); done < <(backends)
    "$LAUNCHER" update "${args[@]}" ;;
esac
