#!/usr/bin/env bash
set -euo pipefail
umask 077

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_BUILD_DIRECTORY="${KILIX_BUILD_DIRECTORY:-$KILIX_STORAGE_HOME/build}"
if [ -n "${KILIX_MULTIPLEXER_HOME:-}" ]; then
  MULTIPLEXER_SOURCE="$KILIX_MULTIPLEXER_HOME"
elif [ -f "$KILIX_HOME/third_party/kilix-multiplexer/Makefile" ]; then
  MULTIPLEXER_SOURCE="$KILIX_HOME/third_party/kilix-multiplexer"
else
  # The workspace app checkout is convenient for contributors; installed
  # releases use the pinned submodule when it is present.
  MULTIPLEXER_SOURCE="$(dirname "$KILIX_HOME")/kilix-apps/kilix-multiplexer"
fi
MULTIPLEXER_BUILD="$KILIX_BUILD_DIRECTORY/libraries/kilix-multiplexer"
SERVE="$MULTIPLEXER_BUILD/kmx-serve"
ATTACH="$MULTIPLEXER_BUILD/kmx-attach"

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

exec 9>"$MULTIPLEXER_BUILD/.build.lock"
chmod 0600 "$MULTIPLEXER_BUILD/.build.lock"
flock 9
if ! make --silent --no-print-directory --question -C "$source_path" \
     BUILD_DIR="$MULTIPLEXER_BUILD" all >/dev/null 2>&1; then
  echo "kilix: building kilix-multiplexer" >&2
  make --silent --no-print-directory -C "$source_path" \
    BUILD_DIR="$MULTIPLEXER_BUILD" all
fi
for binary in "$SERVE" "$ATTACH"; do
  if [ ! -x "$binary" ] || [ -L "$binary" ] \
       || [ "$(stat -c '%u' -- "$binary")" != "$(id -u)" ]; then
    echo "kilix remote: native build did not produce safe executables" >&2
    exit 1
  fi
  chmod 0700 -- "$binary"
done

if [ "${1:-}" = --print-path ]; then
  case "$2" in
    serve) printf '%s\n' "$SERVE" ;;
    attach) printf '%s\n' "$ATTACH" ;;
  esac
fi
