#!/usr/bin/env bash
set -euo pipefail
umask 077

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_BUILD_DIRECTORY="${KILIX_BUILD_DIRECTORY:-$KILIX_STORAGE_HOME/build}"
if [ -n "${KILIX_MULTIPLEXER_HOME:-}" ]; then
  MULTIPLEXER_SOURCE="$KILIX_MULTIPLEXER_HOME"
  MULTIPLEXER_COMMIT="${KILIX_MULTIPLEXER_COMMIT:-}"
elif [ -f "$KILIX_HOME/third_party/kilix-multiplexer/Makefile" ]; then
  MULTIPLEXER_SOURCE="$KILIX_HOME/third_party/kilix-multiplexer"
  MULTIPLEXER_COMMIT="$(git -C "$KILIX_HOME" ls-files -s -- third_party/kilix-multiplexer | awk '$1 == "160000" {print $2}')"
else
  # The workspace app checkout is convenient for contributors; installed
  # releases use the pinned submodule when it is present.
  MULTIPLEXER_SOURCE="$(dirname "$KILIX_HOME")/kilix-apps/kilix-multiplexer"
  MULTIPLEXER_COMMIT="${KILIX_MULTIPLEXER_COMMIT:-}"
fi
MULTIPLEXER_BUILD="$KILIX_BUILD_DIRECTORY/libraries/kilix-multiplexer"

case "${1:-}" in
  "") ;;
  --print-path)
    case "${2:-}" in
      serve|attach) ;;
      *) echo "usage: $0 [--print-path serve|attach]" >&2; exit 2 ;;
    esac ;;
  *) echo "usage: $0 [--print-path serve|attach]" >&2; exit 2 ;;
esac

source_path="$(realpath -m -- "$MULTIPLEXER_SOURCE")"
storage_path="$(realpath -m -- "$KILIX_STORAGE_HOME")"
build_path="$(realpath -m -- "$KILIX_BUILD_DIRECTORY")"
home_path="$(realpath -m -- "$HOME")"
kilix_path="$(realpath -m -- "$KILIX_HOME")"

case "$source_path" in
  /|"$home_path"|"$kilix_path")
    echo "kilix remote: refusing broad multiplexer source path: $source_path" >&2
    exit 1 ;;
esac
if [ "$storage_path" = / ] || [ "$storage_path" = "$home_path" ] \
     || [ "$storage_path" = "$kilix_path" ]; then
  echo "kilix remote: refusing broad storage root: $storage_path" >&2
  exit 1
fi
case "$build_path" in
  "$storage_path"/*) ;;
  *) echo "kilix remote: build directory must be below Kilix storage: $build_path" >&2
     exit 1 ;;
esac

ensure_private_directory() {
  local path="$1" label="$2" owner
  if [ -e "$path" ] || [ -L "$path" ]; then
    if [ ! -d "$path" ] || [ -L "$path" ]; then
      echo "kilix remote: refusing unsafe $label directory: $path" >&2
      return 1
    fi
  else
    mkdir -p -- "$path"
  fi
  owner="$(stat -c '%u' -- "$path")"
  if [ "$owner" != "$(id -u)" ]; then
    echo "kilix remote: $label directory is not owned by this user: $path" >&2
    return 1
  fi
  chmod 0700 -- "$path"
}

if [ ! -f "$source_path/Makefile" ] \
     || [ ! -f "$source_path/include/kilix_mux.h" ]; then
  echo "kilix remote: multiplexer source not found at $source_path" >&2
  echo "initialize third_party/kilix-multiplexer or set KILIX_MULTIPLEXER_HOME" >&2
  exit 1
fi
if [ -L "$source_path" ] \
     || [ "$(stat -c '%u' -- "$source_path")" != "$(id -u)" ]; then
  echo "kilix remote: source must be a real directory owned by this user: $source_path" >&2
  exit 1
fi
command -v make >/dev/null 2>&1 || {
  echo "kilix remote: make is required" >&2
  exit 1
}
command -v flock >/dev/null 2>&1 || {
  echo "kilix remote: flock is required" >&2
  exit 1
}

ensure_private_directory "$KILIX_STORAGE_HOME" storage
ensure_private_directory "$KILIX_BUILD_DIRECTORY" build
ensure_private_directory "$(dirname "$MULTIPLEXER_BUILD")" libraries
ensure_private_directory "$MULTIPLEXER_BUILD" multiplexer-build

_print=()
if [ "${1:-}" = --print-path ]; then
  _print=(--print-kind "$2")
fi
# The dedicated owner retains the lock until all build descendants are reaped.
exec /usr/bin/python3 "$KILIX_HOME/config/multiplexer_build.py" \
  --source "$source_path" --commit "$MULTIPLEXER_COMMIT" \
  --build-dir "$MULTIPLEXER_BUILD" "${_print[@]}"
