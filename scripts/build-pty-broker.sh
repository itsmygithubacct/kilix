#!/usr/bin/env bash
set -euo pipefail
umask 077

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_BUILD_DIRECTORY="${KILIX_BUILD_DIRECTORY:-$KILIX_STORAGE_HOME/build}"
BROKER_SOURCE="${KILIX_PTY_BROKER_HOME:-$(dirname "$KILIX_HOME")/kitty-pty-broker}"
BROKER_BUILD="$KILIX_BUILD_DIRECTORY/libraries/kitty-pty-broker"
BROKER_EXECUTABLE="$BROKER_BUILD/kitty-pty-broker"
BROKER_LIBRARY="$BROKER_BUILD/libkitty-pty-broker.so"

case "${1:-}" in
  ""|--print-path) ;;
  *) echo "usage: $0 [--print-path]" >&2; exit 2 ;;
esac

_source="$(realpath -m -- "$BROKER_SOURCE")"
_storage="$(realpath -m -- "$KILIX_STORAGE_HOME")"
_build="$(realpath -m -- "$KILIX_BUILD_DIRECTORY")"
_home="$(realpath -m -- "$HOME")"
_kilix="$(realpath -m -- "$KILIX_HOME")"

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
  owner="$(stat -c '%u' -- "$path")"
  if [ "$owner" != "$(id -u)" ]; then
    echo "kilix pty broker: $label directory is not owned by this user: $path" >&2
    return 1
  fi
  chmod 0700 -- "$path"
}

if [ ! -f "$_source/Makefile" ] || [ ! -f "$_source/include/kitty_pty_broker.h" ]; then
  echo "kilix pty broker: source not found at $_source" >&2
  echo "set KILIX_PTY_BROKER_HOME to the kitty-pty-broker checkout" >&2
  exit 1
fi
if [ -L "$_source" ] || [ "$(stat -c '%u' -- "$_source")" != "$(id -u)" ]; then
  echo "kilix pty broker: source must be a real directory owned by this user: $_source" >&2
  exit 1
fi
command -v make >/dev/null 2>&1 || {
  echo "kilix pty broker: make is required" >&2
  exit 1
}
command -v flock >/dev/null 2>&1 || {
  echo "kilix pty broker: flock is required" >&2
  exit 1
}

_ensure_private_directory "$KILIX_STORAGE_HOME" storage
_ensure_private_directory "$KILIX_BUILD_DIRECTORY" build
_ensure_private_directory "$(dirname "$BROKER_BUILD")" libraries
_ensure_private_directory "$BROKER_BUILD" broker-build

exec 9>"$BROKER_BUILD/.build.lock"
chmod 0600 "$BROKER_BUILD/.build.lock"
flock 9
if ! make --silent --no-print-directory --question -C "$_source" \
     BUILD_DIR="$BROKER_BUILD" all >/dev/null 2>&1; then
  echo "kilix: building kitty-pty-broker" >&2
  make --silent --no-print-directory -C "$_source" \
    BUILD_DIR="$BROKER_BUILD" all
fi
if [ ! -x "$BROKER_EXECUTABLE" ] || [ -L "$BROKER_EXECUTABLE" ] \
     || [ ! -f "$BROKER_LIBRARY" ] || [ -L "$BROKER_LIBRARY" ]; then
  echo "kilix pty broker: native build did not produce the expected artifacts" >&2
  exit 1
fi
if [ "$(stat -c '%u' -- "$BROKER_EXECUTABLE")" != "$(id -u)" ] \
     || [ "$(stat -c '%u' -- "$BROKER_LIBRARY")" != "$(id -u)" ]; then
  echo "kilix pty broker: build artifacts are not owned by this user" >&2
  exit 1
fi
chmod 0700 -- "$BROKER_EXECUTABLE" "$BROKER_LIBRARY"

if [ "${1:-}" = --print-path ]; then
  printf '%s\n' "$BROKER_EXECUTABLE"
fi
