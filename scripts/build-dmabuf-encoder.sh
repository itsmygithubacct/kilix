#!/bin/sh
set -eu

SOURCE_HOME=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STORAGE=${KILIX_STORAGE_HOME:-$HOME/.local/gpu_terminal/kilix}
BUILD_ROOT=${KILIX_BUILD_DIRECTORY:-$STORAGE/build}
DEV_ROOT=${KILIX_VIDEO_DEV_ROOT:-$STORAGE/dependencies/video-encode-dev}
OUTPUT=${KILIX_DMABUF_ENCODER:-$BUILD_ROOT/libraries/gpu-host/kilix-dmabuf-encode}
packages='libavcodec-dev|7:7.1.5-0+deb13u1|36f190f54441bd3bac0eddcfa4713e41c3b41da6476ac9bb868cc3c9f027fe02
libavfilter-dev|7:7.1.5-0+deb13u1|499737081b46818f8ad9f918babb09df4549f1cd54cf52e83ede8e0caec6e932
libavutil-dev|7:7.1.5-0+deb13u1|71aae526cbdb5fb55cb8269427769eca120308393f70590843ba4d1a1b6befdd'

case ${1:-} in
  --print-path) printf '%s\n' "$OUTPUT"; exit 0 ;;
  ''|--build) ;;
  *) echo 'usage: build-dmabuf-encoder.sh [--build|--print-path]' >&2; exit 2 ;;
esac
for tool in apt-get cc dpkg-deb sha256sum; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "kilix DMA-BUF encoder: $tool is required" >&2; exit 1; }
done
if ! test -f "$DEV_ROOT/usr/include/x86_64-linux-gnu/libavcodec/avcodec.h" ||
   ! test -f "$DEV_ROOT/usr/include/x86_64-linux-gnu/libavfilter/avfilter.h"; then
  work=$(mktemp -d "$STORAGE/.video-dev.XXXXXX")
  trap 'rm -rf "$work"' EXIT HUP INT TERM
  mkdir -p "$work/packages" "$work/root"
  printf '%s\n' "$packages" | while IFS='|' read -r package version digest; do
    (cd "$work/packages" && apt-get download "$package=$version")
    archive=$(find "$work/packages" -maxdepth 1 -type f -name "${package}_*.deb" -print)
    test "$(printf '%s\n' "$archive" | wc -l)" -eq 1 || exit 1
    printf '%s  %s\n' "$digest" "$archive" | sha256sum -c --status || {
      echo "kilix DMA-BUF encoder: checksum mismatch for $package" >&2; exit 1; }
    dpkg-deb -x "$archive" "$work/root"
  done
  rm -rf "$DEV_ROOT.new"
  mv "$work/root" "$DEV_ROOT.new"
  if test -e "$DEV_ROOT"; then mv "$DEV_ROOT" "$work/old"; fi
  mv "$DEV_ROOT.new" "$DEV_ROOT"
fi

libdir=/usr/lib/x86_64-linux-gnu
for library in libavfilter.so.10 libavcodec.so.61 libavutil.so.59; do
  test -f "$libdir/$library" || {
    echo "kilix DMA-BUF encoder: $library is unavailable (install ffmpeg)" >&2
    exit 1
  }
done
mkdir -p "$(dirname -- "$OUTPUT")"
temporary=$OUTPUT.tmp.$$
trap 'rm -f "$temporary"' EXIT HUP INT TERM
${CC:-cc} -std=gnu11 -O2 -Wall -Wextra -Werror \
  -I"$DEV_ROOT/usr/include/x86_64-linux-gnu" -I"$DEV_ROOT/usr/include" \
  "$SOURCE_HOME/native/kilix-dmabuf-encode.c" -o "$temporary" \
  "$libdir/libavfilter.so.10" "$libdir/libavcodec.so.61" \
  "$libdir/libavutil.so.59"
chmod 0700 "$temporary"
mv "$temporary" "$OUTPUT"
printf 'kilix DMA-BUF encoder: built %s\n' "$OUTPUT"
