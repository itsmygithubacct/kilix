#!/bin/sh
set -eu

script_home=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
KILIX_SOURCE_HOME=${KILIX_SOURCE_HOME:-$script_home}
BUILD_ROOT=${KILIX_BUILD_DIRECTORY:-${KILIX_STORAGE_HOME:-$HOME/.local/gpu_terminal/kilix}/build}
OUTPUT=${KILIX_GPU_CAPTURE:-$BUILD_ROOT/libraries/gpu-host/kilix-pw-capture}

case ${1:-} in
  --print-path) printf '%s\n' "$OUTPUT"; exit 0 ;;
  ''|--build) ;;
  *) echo "usage: $0 [--build|--print-path]" >&2; exit 2 ;;
esac

command -v pkg-config >/dev/null 2>&1 || {
  echo 'kilix gpu capture: pkg-config is required' >&2; exit 1; }
pkg-config --exists libpipewire-0.3 libdrm || {
  echo 'kilix gpu capture: PipeWire and DRM development files are required' >&2
  exit 1
}
mkdir -p "$(dirname "$OUTPUT")"
tmp="$OUTPUT.tmp.$$"
trap 'rm -f "$tmp"' EXIT HUP INT TERM
# pkg-config deliberately returns shell words for the compiler driver.
# shellcheck disable=SC2046
${CC:-cc} -std=gnu11 -D_GNU_SOURCE -O2 -Wall -Wextra -Werror \
  $(pkg-config --cflags libpipewire-0.3 libdrm) \
  "$KILIX_SOURCE_HOME/native/kilix-pw-capture.c" -o "$tmp" \
  $(pkg-config --libs libpipewire-0.3 libdrm)
chmod 0755 "$tmp"
mv "$tmp" "$OUTPUT"
printf 'kilix gpu capture: built %s\n' "$OUTPUT"
