#!/usr/bin/env bash
# Lazy, immutable speech resource planner for the installed TTS selector.
set -euo pipefail
umask 077

KILIX_SYSTEM_MONITOR_REF=9ad28c3a34f8b42c49b116ceaa57184bafaffeda
KILIX_SYSTEM_MONITOR_REPO=https://github.com/itsmygithubacct/kilix-system-monitor.git
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$GPU_TERMINAL_HOME/sources}"
source_dir="$GPU_TERMINAL_SOURCE_HOME/.kilix-tts-sizer-$KILIX_SYSTEM_MONITOR_REF"
executable="$source_dir/components/plebian-model-sizer/plebian-model-sizer"

case "${1:-}" in
  --print-ref)
    [ "$#" = 1 ] || exit 2
    printf '%s\n' "kilix-system-monitor=$KILIX_SYSTEM_MONITOR_REF"
    exit 0 ;;
  --print-path)
    [ "$#" = 1 ] || exit 2
    printf '%s\n' "$executable"
    exit 0 ;;
  "") [ "$#" = 0 ] || exit 2 ;;
  *) exit 2 ;;
esac

[ "$(id -u)" -ne 0 ] || { echo 'kilix tts: run the model sizer as the desktop user' >&2; exit 1; }
case "$source_dir" in /|"$HOME") exit 1 ;; esac
if [ -x "$executable" ] \
    && [ "$(git -C "$source_dir" rev-parse HEAD 2>/dev/null)" = "$KILIX_SYSTEM_MONITOR_REF" ] \
    && [ -z "$(git -C "$source_dir" status --porcelain 2>/dev/null)" ]; then
  printf '%s\n' "$executable"
  exit 0
fi
[ ! -e "$source_dir" ] && [ ! -L "$source_dir" ] \
  || { echo 'kilix tts: managed model sizer source is not the pinned clean checkout' >&2; exit 1; }
command -v git >/dev/null || { echo 'kilix tts: git is required to install the model sizer' >&2; exit 1; }
mkdir -p -- "$GPU_TERMINAL_SOURCE_HOME"
stage="$(mktemp -d "$GPU_TERMINAL_SOURCE_HOME/.kilix-tts-sizer-stage.XXXXXX")" || exit 1
trap 'rm -rf -- "$stage"' EXIT
git -C "$stage" init -q
git -C "$stage" remote add origin "$KILIX_SYSTEM_MONITOR_REPO"
git -C "$stage" fetch -q --depth=1 origin "$KILIX_SYSTEM_MONITOR_REF"
git -C "$stage" checkout -q --detach "$KILIX_SYSTEM_MONITOR_REF"
[ -x "$stage/components/plebian-model-sizer/plebian-model-sizer" ] \
  || { echo 'kilix tts: pinned model sizer is missing' >&2; exit 1; }
mv -- "$stage" "$source_dir"
trap - EXIT
printf '%s\n' "$executable"
