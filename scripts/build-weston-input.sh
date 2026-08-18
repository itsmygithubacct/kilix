#!/bin/sh
set -eu

SOURCE_HOME=${KILIX_SOURCE_HOME:-$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)}
GPU_ROOT=${KILIX_GPU_HOST_ROOT:-${KILIX_GPU_HOST_HOME:-${KILIX_STORAGE_HOME:-$HOME/.local/gpu_terminal/kilix}/dependencies/gpu-host}/root}
BUILD_ROOT=${KILIX_BUILD_DIRECTORY:-${KILIX_STORAGE_HOME:-$HOME/.local/gpu_terminal/kilix}/build}
OUTPUT=${KILIX_WESTON_INPUT_MODULE:-$BUILD_ROOT/libraries/gpu-host/kilix-weston-input.so}

case ${1:-} in
  --print-path) printf '%s\n' "$OUTPUT"; exit 0 ;;
  ''|--build) ;;
  *) echo "usage: $0 [--build|--print-path]" >&2; exit 2 ;;
esac

header=$GPU_ROOT/usr/include/libweston-14
library=$GPU_ROOT/usr/lib/x86_64-linux-gnu
test -f "$header/libweston/libweston.h" || {
  echo 'kilix weston input: staged libweston development files are required' >&2
  exit 1
}
mkdir -p "$(dirname "$OUTPUT")"
tmp=$OUTPUT.tmp.$$
trap 'rm -f "$tmp"' EXIT HUP INT TERM
# pkg-config deliberately returns compiler-driver words.
# shellcheck disable=SC2046
${CC:-cc} -std=gnu11 -fPIC -shared -O2 -Wall -Wextra -Werror \
  -I"$header" $(pkg-config --cflags pixman-1 wayland-server xkbcommon) \
  "$SOURCE_HOME/native/kilix-weston-input.c" -o "$tmp" \
  -L"$library" -Wl,-rpath,"$library" -lweston-14 \
  $(pkg-config --libs pixman-1 wayland-server xkbcommon)
chmod 0755 "$tmp"
mv "$tmp" "$OUTPUT"
printf 'kilix weston input: built %s\n' "$OUTPUT"
