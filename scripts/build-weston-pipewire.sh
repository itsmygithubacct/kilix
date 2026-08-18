#!/bin/sh
set -eu

SOURCE_HOME=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
GPU_ROOT=${KILIX_GPU_HOST_ROOT:-${KILIX_GPU_HOST_HOME:-${KILIX_STORAGE_HOME:-$HOME/.local/gpu_terminal/kilix}/dependencies/gpu-host}/root}
DATA_ROOT=${KILIX_DATA_HOME:-${KILIX_STORAGE_HOME:-$HOME/.local/gpu_terminal/kilix}/data}
OUTPUT=${KILIX_WESTON_PIPEWIRE_BACKEND:-$GPU_ROOT/usr/lib/x86_64-linux-gnu/libweston-14/pipewire-backend.so}
ARCHIVE=$DATA_ROOT/gpu-host-sources/weston-14.0.2.tar.xz

case ${1:-} in
  --print-path) printf '%s\n' "$OUTPUT"; exit 0 ;;
  ''|--build) ;;
  *) echo 'usage: build-weston-pipewire.sh [--build|--print-path]' >&2; exit 2 ;;
esac

test -f "$ARCHIVE" || { echo 'kilix pipewire backend: Weston source is missing' >&2; exit 1; }
test -f "$GPU_ROOT/usr/include/libweston-14/libweston/libweston.h" || {
  echo 'kilix pipewire backend: install GPU host dependencies first' >&2; exit 1;
}
for tool in cc patch pkg-config tar; do command -v "$tool" >/dev/null 2>&1 || exit 1; done

work=$(mktemp -d "${TMPDIR:-/tmp}/kilix-pipewire-backend.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
tar -xJf "$ARCHIVE" -C "$work"
source=$work/weston-14.0.2
patch -d "$source" -p1 < "$SOURCE_HOME/native/weston-pipewire-60hz.patch"
printf '%s\n' '#define HAVE_UNREACHABLE 1' '#define HAVE_BUILTIN_POPCOUNT 1' \
  '#define HAVE_BUILTIN_BSWAP32 1' '#define HAVE_BUILTIN_CLZ 1' > "$work/config.h"
mkdir -p "$(dirname -- "$OUTPUT")"
temporary=$work/pipewire-backend.so
cc -shared -fPIC -O2 -D_GNU_SOURCE \
  -I"$work" -I"$source" -I"$source/libweston" \
  -I"$GPU_ROOT/usr/include/libweston-14" -I/usr/include/pixman-1 \
  $(pkg-config --cflags libpipewire-0.3 libspa-0.2 libdrm egl) \
  "$source/libweston/backend-pipewire/pipewire.c" \
  -L"$GPU_ROOT/usr/lib/x86_64-linux-gnu" -lweston-14 \
  $(pkg-config --libs libpipewire-0.3 libdrm egl) -o "$temporary"
chmod 0700 "$temporary"
mv "$temporary" "$OUTPUT"
printf 'kilix pipewire backend: built %s\n' "$OUTPUT"
