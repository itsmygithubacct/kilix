#!/usr/bin/env bash
set -euo pipefail
umask 077

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_BUILD_DIRECTORY="${KILIX_BUILD_DIRECTORY:-$KILIX_STORAGE_HOME/build}"
STATE_SOURCE="$KILIX_HOME/third_party/kilix-state"
STATE_BUILD="$KILIX_BUILD_DIRECTORY/libraries/kilix-state"
STATE_LIBRARY="$STATE_BUILD/libkilix-state.so"

case "${1:-}" in
  ""|--print-path) ;;
  *) echo "usage: $0 [--print-path]" >&2; exit 2 ;;
esac

_storage="$(realpath -m -- "$KILIX_STORAGE_HOME")"
_build="$(realpath -m -- "$KILIX_BUILD_DIRECTORY")"
_home="$(realpath -m -- "$HOME")"
_source="$(realpath -m -- "$KILIX_HOME")"
if [ "$_storage" = / ] || [ "$_storage" = "$_home" ] \
     || [ "$_storage" = "$_source" ]; then
  echo "kilix state: refusing broad or source-tree storage root: $_storage" >&2
  exit 1
fi
case "$_build" in
  "$_storage"/*) ;;
  *) echo "kilix state: build directory must be below Kilix storage: $_build" >&2
     exit 1 ;;
esac
case "$_storage" in
  "$_source"/*) echo "kilix state: storage cannot be inside the source checkout" >&2
                exit 1 ;;
esac
case "$_source" in
  "$_storage"/*) echo "kilix state: storage cannot contain the source checkout" >&2
                 exit 1 ;;
esac

_ensure_private_directory() {
  local path="$1" label="$2" owner
  if [ -e "$path" ] || [ -L "$path" ]; then
    if [ ! -d "$path" ] || [ -L "$path" ]; then
      echo "kilix state: refusing unsafe $label directory: $path" >&2
      return 1
    fi
  else
    mkdir -p -- "$path"
  fi
  owner="$(stat -c '%u' -- "$path")"
  if [ "$owner" != "$(id -u)" ]; then
    echo "kilix state: $label directory is not owned by this user: $path" >&2
    return 1
  fi
  chmod 0700 -- "$path"
}

[ -f "$STATE_SOURCE/Makefile" ] || {
  echo "kilix state: missing pinned native source; run: git -C $KILIX_HOME submodule update --init --recursive" >&2
  exit 1
}
_expected_source="$(git -C "$KILIX_HOME" ls-files -s -- \
  third_party/kilix-state | awk '$1 == "160000" {print $2}')"
_actual_source="$(git -C "$STATE_SOURCE" rev-parse HEAD 2>/dev/null || true)"
if [ -z "$_expected_source" ] || [ "$_actual_source" != "$_expected_source" ]; then
  echo "kilix state: native source does not match the host's pinned gitlink" >&2
  exit 1
fi
if [ -n "$(git -C "$STATE_SOURCE" status --porcelain \
     --untracked-files=all 2>/dev/null)" ]; then
  echo "kilix state: refusing modified pinned native source" >&2
  exit 1
fi
command -v make >/dev/null 2>&1 || {
  echo "kilix state: make is required to build libkilix-state" >&2
  exit 1
}
command -v flock >/dev/null 2>&1 || {
  echo "kilix state: flock is required to serialize native builds" >&2
  exit 1
}

_ensure_private_directory "$KILIX_STORAGE_HOME" storage
_ensure_private_directory "$KILIX_BUILD_DIRECTORY" build
_ensure_private_directory "$(dirname "$STATE_BUILD")" libraries
_ensure_private_directory "$STATE_BUILD" state-build

exec 9>"$STATE_BUILD/.build.lock"
chmod 0600 "$STATE_BUILD/.build.lock"
flock 9
_source_stamp="$STATE_BUILD/source-id"
_force=()
if [ ! -f "$_source_stamp" ] \
     || [ "$(cat "$_source_stamp" 2>/dev/null || true)" != "$_actual_source" ]; then
  _force=(-B)
fi
if ! make --silent --no-print-directory --question -C "$STATE_SOURCE" \
     "${_force[@]}" BUILD_DIR="$STATE_BUILD" all >/dev/null 2>&1; then
  echo "kilix state: building pinned libkilix-state" >&2
  make --silent --no-print-directory "${_force[@]}" -C "$STATE_SOURCE" \
    BUILD_DIR="$STATE_BUILD" all
fi
[ -f "$STATE_LIBRARY" ] && [ ! -L "$STATE_LIBRARY" ] || {
  echo "kilix state: native build did not produce $STATE_LIBRARY" >&2
  exit 1
}
_stamp_tmp="$(mktemp "$STATE_BUILD/source-id.tmp.XXXXXX")"
printf '%s\n' "$_actual_source" >"$_stamp_tmp"
chmod 0600 "$_stamp_tmp"
mv -f -- "$_stamp_tmp" "$_source_stamp"
if [ "$(stat -c '%u' -- "$STATE_LIBRARY")" != "$(id -u)" ]; then
  echo "kilix state: native library is not owned by this user" >&2
  exit 1
fi
chmod 0700 -- "$STATE_LIBRARY"

if [ "${1:-}" = --print-path ]; then
  printf '%s\n' "$STATE_LIBRARY"
fi
