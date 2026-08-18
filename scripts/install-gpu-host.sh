#!/bin/sh
set -eu

# Complete rootless GPU-host provisioning: the pinned Weston/PipeWire runtime
# plus the Kilix-owned modules that discover_runtime() requires.
SOURCE_HOME=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
TARGET=${KILIX_GPU_HOST_HOME:-${KILIX_STORAGE_HOME:-$HOME/.local/gpu_terminal/kilix}/dependencies/gpu-host}

case $TARGET in
  ''|'/'|"$HOME")
    echo "kilix gpu host: refusing unsafe target: $TARGET" >&2
    exit 1 ;;
esac

helper_path() {
  KILIX_GPU_HOST_HOME=$TARGET "$SOURCE_HOME/scripts/$1" --print-path
}

verify_helpers() {
  for builder in build-weston-pipewire.sh build-weston-input.sh build-gpu-capture.sh \
                 build-kilix-kiosk-shell.sh; do
    helper=$(helper_path "$builder")
    if ! test -f "$helper" || test -L "$helper" || ! test -x "$helper"; then
      echo "kilix gpu host: required helper is missing or unsafe: $helper" >&2
      return 1
    fi
  done
}

build_helpers() {
  for builder in build-weston-pipewire.sh build-weston-input.sh build-gpu-capture.sh \
                 build-kilix-kiosk-shell.sh; do
    KILIX_GPU_HOST_HOME=$TARGET "$SOURCE_HOME/scripts/$builder"
  done
  # Direct encoding is optional. Ordinary GPU panes do not require FFmpeg,
  # and discover_runtime() deliberately retains the authenticated raw-frame
  # broadcast fallback when this helper cannot be built or used.
  if test -f /usr/lib/x86_64-linux-gnu/libavfilter.so.10 \
       && test -f /usr/lib/x86_64-linux-gnu/libavcodec.so.61 \
       && test -f /usr/lib/x86_64-linux-gnu/libavutil.so.59; then
    KILIX_GPU_HOST_HOME=$TARGET "$SOURCE_HOME/scripts/build-dmabuf-encoder.sh"
  else
    echo 'kilix gpu host: FFmpeg 7 libraries unavailable; direct broadcast encoding will use the fallback' >&2
  fi
}

case ${1:-} in
  --print-path)
    KILIX_GPU_HOST_HOME=$TARGET \
      "$SOURCE_HOME/scripts/install-gpu-host-deps.sh" --print-path
    exit 0 ;;
  --verify)
    KILIX_GPU_HOST_HOME=$TARGET \
      "$SOURCE_HOME/scripts/install-gpu-host-deps.sh" --verify
    verify_helpers
    printf 'kilix gpu host: verified runtime and helpers in %s\n' "$TARGET"
    exit 0 ;;
  ''|--install) ;;
  *) echo "usage: $0 [--install|--verify|--print-path]" >&2; exit 2 ;;
esac

KILIX_GPU_HOST_HOME=$TARGET \
  "$SOURCE_HOME/scripts/install-gpu-host-deps.sh" --install
build_helpers
verify_helpers
printf 'kilix gpu host: installed runtime and helpers in %s\n' "$TARGET"
