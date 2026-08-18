#!/bin/sh
set -eu

SOURCE_HOME=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
GPU_ROOT=${KILIX_GPU_HOST_ROOT:-${KILIX_GPU_HOST_HOME:-${KILIX_STORAGE_HOME:-$HOME/.local/gpu_terminal/kilix}/dependencies/gpu-host}/root}
BUILD_ROOT=${KILIX_BUILD_DIRECTORY:-${KILIX_STORAGE_HOME:-$HOME/.local/gpu_terminal/kilix}/build}
OUTPUT=${KILIX_WESTON_KIOSK_SHELL:-$BUILD_ROOT/libraries/gpu-host/kilix-kiosk-shell.so}
DATA_ROOT=${KILIX_DATA_HOME:-${KILIX_STORAGE_HOME:-$HOME/.local/gpu_terminal/kilix}/data}
ARCHIVE=$DATA_ROOT/gpu-host-sources/weston-14.0.2.tar.xz
URL=https://deb.debian.org/debian/pool/main/w/weston/weston_14.0.2.orig.tar.xz
DIGEST=b47216b3530da76d02a3a1acbf1846a9cd41d24caa86448f9c46f78f20b6e0ac

test -f "$GPU_ROOT/usr/include/libweston-14/libweston/libweston.h" || {
  echo 'kilix kiosk shell: install GPU host dependencies first' >&2
  exit 1
}
for tool in cc curl patch sha256sum tar; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "kilix kiosk shell: $tool is required" >&2
    exit 1
  }
done

work=$(mktemp -d "${TMPDIR:-/tmp}/kilix-kiosk-shell.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
if test -n "${KILIX_WESTON_SOURCE:-}"; then
  weston_source=$KILIX_WESTON_SOURCE
else
  mkdir -p "$(dirname -- "$ARCHIVE")"
  if ! printf '%s  %s\n' "$DIGEST" "$ARCHIVE" | sha256sum -c --status 2>/dev/null; then
    curl -fL --retry 3 -o "$work/weston.tar.xz" "$URL"
    printf '%s  %s\n' "$DIGEST" "$work/weston.tar.xz" | sha256sum -c --status || {
      echo 'kilix kiosk shell: Weston source checksum mismatch' >&2
      exit 1
    }
    mv "$work/weston.tar.xz" "$ARCHIVE"
  fi
  tar -xJf "$ARCHIVE" -C "$work"
  weston_source=$work/weston-14.0.2
fi

patch -d "$weston_source" -p1 < "$SOURCE_HOME/native/weston-kiosk-shared-output.patch"
printf '%s\n' \
  '#define HAVE_UNREACHABLE 1' \
  '#define HAVE_BUILTIN_POPCOUNT 1' \
  '#define HAVE_BUILTIN_BSWAP32 1' \
  '#define HAVE_BUILTIN_CLZ 1' > "$work/config.h"
mkdir -p "$(dirname -- "$OUTPUT")"
temporary=$work/kilix-kiosk-shell.so
cc -shared -fPIC -O2 -D_GNU_SOURCE \
  -I"$work" -I"$weston_source" -I"$GPU_ROOT/usr/include/libweston-14" \
  -I/usr/include/pixman-1 \
  "$weston_source/kiosk-shell/kiosk-shell.c" \
  "$weston_source/kiosk-shell/kiosk-shell-grab.c" \
  -L"$GPU_ROOT/usr/lib/x86_64-linux-gnu" \
  -L"$GPU_ROOT/usr/lib/x86_64-linux-gnu/weston" \
  -lweston-14 -lexec_weston -lwayland-server -lxkbcommon -lm \
  -o "$temporary"
chmod 0700 "$temporary"
mv "$temporary" "$OUTPUT"
printf 'kilix kiosk shell: built %s\n' "$OUTPUT"
