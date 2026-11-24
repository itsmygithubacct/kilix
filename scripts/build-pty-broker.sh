#!/usr/bin/env bash
set -euo pipefail
# Locate portable.sh without dirname(1): callers may run this on a bare PATH.
case "${BASH_SOURCE[0]}" in */*) . "${BASH_SOURCE[0]%/*}/portable.sh" ;; *) . ./portable.sh ;; esac
umask 077

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_BUILD_DIRECTORY="${KILIX_BUILD_DIRECTORY:-$KILIX_STORAGE_HOME/build}"
if [ -n "${KILIX_PTY_BROKER_HOME:-}" ]; then
  BROKER_SOURCE="$KILIX_PTY_BROKER_HOME"
elif [ -f "$KILIX_HOME/third_party/kitty-pty-broker/Makefile" ]; then
  BROKER_SOURCE="$KILIX_HOME/third_party/kitty-pty-broker"
else
  # Keep the module workspace convenient for contributors while installed
  # Kilix releases use the pinned submodule above.
  BROKER_SOURCE="$(dirname "$KILIX_HOME")/kilix-modules/kitty-pty-broker"
fi
BROKER_BUILD="$KILIX_BUILD_DIRECTORY/libraries/kitty-pty-broker"
BROKER_EXECUTABLE="$BROKER_BUILD/kitty-pty-broker"
BROKER_LIBRARY="$BROKER_BUILD/libkitty-pty-broker.so"

case "${1:-}" in
  ""|--print-path) ;;
  *) echo "usage: $0 [--print-path]" >&2; exit 2 ;;
esac

_source="$(kilix_realpath_m "$BROKER_SOURCE")"
_storage="$(kilix_realpath_m "$KILIX_STORAGE_HOME")"
_build="$(kilix_realpath_m "$KILIX_BUILD_DIRECTORY")"
_home="$(kilix_realpath_m "$HOME")"
_kilix="$(kilix_realpath_m "$KILIX_HOME")"

case "$_source" in
  /|"$HOME"|"$KILIX_HOME")
    echo "kilix pty broker: refusing broad source path: $_source" >&2
    exit 1 ;;
esac
if [ "$_storage" = / ] || [ "$_storage" = "$_home" ] || [ "$_storage" = "$_kilix" ]; then
  echo "kilix pty broker: refusing broad storage root: $_storage" >&2
  exit 1
fi
case "$_build" in
  "$_storage"/*) ;;
  *) echo "kilix pty broker: build directory must be below Kilix storage: $_build" >&2
     exit 1 ;;
esac

_ensure_private_directory() {
  local path="$1" label="$2" owner
  if [ -e "$path" ] || [ -L "$path" ]; then
    if [ ! -d "$path" ] || [ -L "$path" ]; then
      echo "kilix pty broker: refusing unsafe $label directory: $path" >&2
      return 1
    fi
  else
    mkdir -p -- "$path"
  fi
  owner="$(kilix_stat_uid "$path")"
  if [ "$owner" != "$(id -u)" ]; then
    echo "kilix pty broker: $label directory is not owned by this user: $path" >&2
    return 1
  fi
  chmod -- 0700 "$path"
}

if [ ! -f "$_source/Makefile" ] || [ ! -f "$_source/include/kitty_pty_broker.h" ]; then
  echo "kilix pty broker: source not found at $_source" >&2
  echo "initialize third_party/kitty-pty-broker or set KILIX_PTY_BROKER_HOME" >&2
  exit 1
fi
if [ -L "$_source" ] || [ "$(kilix_stat_uid "$_source")" != "$(id -u)" ]; then
  echo "kilix pty broker: source must be a real directory owned by this user: $_source" >&2
  exit 1
fi
command -v "$(kilix_make_tool)" >/dev/null 2>&1 || {
  echo "kilix pty broker: $(kilix_make_tool) is required" >&2
  exit 1
}
command -v "$(kilix_lock_tool)" >/dev/null 2>&1 || {
  echo "kilix pty broker: $(kilix_lock_tool) is required" >&2
  exit 1
}

_ensure_private_directory "$KILIX_STORAGE_HOME" storage
_ensure_private_directory "$KILIX_BUILD_DIRECTORY" build
_ensure_private_directory "$(dirname "$BROKER_BUILD")" libraries
_ensure_private_directory "$BROKER_BUILD" broker-build

exec 9>"$BROKER_BUILD/.build.lock"
chmod 0600 "$BROKER_BUILD/.build.lock"
kilix_lock_fd 9
if ! kilix_make --silent --no-print-directory --question -C "$_source" \
     BUILD_DIR="$BROKER_BUILD" all >/dev/null 2>&1; then
  echo "kilix: building kitty-pty-broker" >&2
  kilix_make --silent --no-print-directory -C "$_source" \
    BUILD_DIR="$BROKER_BUILD" all
fi
if [ ! -x "$BROKER_EXECUTABLE" ] || [ -L "$BROKER_EXECUTABLE" ] \
     || [ ! -f "$BROKER_LIBRARY" ] || [ -L "$BROKER_LIBRARY" ]; then
  echo "kilix pty broker: native build did not produce the expected artifacts" >&2
  exit 1
fi
if [ "$(kilix_stat_uid "$BROKER_EXECUTABLE")" != "$(id -u)" ] \
     || [ "$(kilix_stat_uid "$BROKER_LIBRARY")" != "$(id -u)" ]; then
  echo "kilix pty broker: build artifacts are not owned by this user" >&2
  exit 1
fi
chmod -- 0700 "$BROKER_EXECUTABLE" "$BROKER_LIBRARY"

if [ "${1:-}" = --print-path ]; then
  printf '%s\n' "$BROKER_EXECUTABLE"
fi
