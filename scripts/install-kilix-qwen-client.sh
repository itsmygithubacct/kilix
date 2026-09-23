#!/usr/bin/env bash
# Lazy, inference-free client for Voice's receipt-backed Qwen provider.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$GPU_TERMINAL_HOME/sources}"
KILIX_DATA_HOME="${KILIX_DATA_HOME:-$GPU_TERMINAL_HOME/kilix/data}"
KILIX_QWEN_CLIENT_REF=3f1031263b4369761d77a90fec8e480fcb87f3d0
KILIX_QWEN_CLIENT_REPO=https://github.com/itsmygithubacct/kilix-qwen-tts.git

fail() { printf 'kilix qwen client: %s\n' "$*" >&2; exit 1; }
root="$KILIX_DATA_HOME/voice/qwen-client"
source_dir="$GPU_TERMINAL_SOURCE_HOME/.kilix-qwen-tts-$KILIX_QWEN_CLIENT_REF"
generation="$root/generations/$KILIX_QWEN_CLIENT_REF"
current="$root/current"
binary="$generation/bin/kilix-qwen-tts"

case "${1:-}" in
  --print-ref)
    [ "$#" = 1 ] || fail 'usage: install-kilix-qwen-client.sh [--print-ref|--print-path]'
    printf '%s\n' "kilix-qwen-tts=$KILIX_QWEN_CLIENT_REF"; exit 0 ;;
  --print-path)
    [ "$#" = 1 ] || fail 'usage: install-kilix-qwen-client.sh [--print-ref|--print-path]'
    printf '%s\n' "$current/bin/kilix-qwen-tts"; exit 0 ;;
  '') [ "$#" = 0 ] || fail 'usage: install-kilix-qwen-client.sh [--print-ref|--print-path]' ;;
  *) fail 'usage: install-kilix-qwen-client.sh [--print-ref|--print-path]' ;;
esac

[ "$(id -u)" -ne 0 ] || fail 'run this as the desktop user'
[ -x /usr/bin/python3 ] || fail 'system Python is unavailable'
command -v git >/dev/null || fail 'git is required for the pinned client source'
command -v flock >/dev/null || fail 'flock is required for the client install lock'
case "$root" in /|"$HOME") fail 'refusing a broad runtime directory' ;; esac
case "$source_dir" in /|"$HOME") fail 'refusing a broad source directory' ;; esac
mkdir -p -- "$GPU_TERMINAL_SOURCE_HOME" "$root/generations"
[ ! -L "$GPU_TERMINAL_SOURCE_HOME" ] && [ ! -L "$root" ] \
    && [ ! -L "$root/generations" ] && [ ! -L "$source_dir" ] \
    && [ ! -L "$generation" ] \
  || fail 'a managed Qwen client directory is a symlink'
[ ! -e "$current" ] || [ -L "$current" ] \
  || fail 'the managed client current path is not a symlink'
exec 9<"$root"
flock 9
# An upgrade finds current on the previously pinned generation; anything that
# is not a managed generation directory is still refused.
if [ -L "$current" ]; then
  previous="$(readlink -- "$current")"
  case "$previous" in
    "$root/generations/"*) previous="${previous#"$root/generations/"}" ;;
    *) previous="" ;;
  esac
  [ "${#previous}" = 40 ] && [ -z "${previous//[0-9a-f]/}" ] \
    || fail 'the managed client current link points outside its generations'
fi

if [ -L "$current" ] && [ -f "$generation/.kilix-verified" ] \
    && [ "$(<"$generation/.kilix-verified")" = "$KILIX_QWEN_CLIENT_REF" ] \
    && [ -x "$binary" ] && "$binary" --help >/dev/null 2>&1; then
  printf '%s\n' "$current/bin/kilix-qwen-tts"
  exit 0
fi

if [ ! -d "$source_dir" ]; then
  stage="$(mktemp -d "$GPU_TERMINAL_SOURCE_HOME/.kilix-qwen-source.XXXXXX")" \
    || fail 'could not stage the pinned Qwen source'
  trap 'rm -rf -- "$stage"' EXIT
  git -C "$stage" init -q
  git -C "$stage" remote add origin "$KILIX_QWEN_CLIENT_REPO"
  git -C "$stage" fetch -q --depth=1 origin "$KILIX_QWEN_CLIENT_REF" \
    || fail 'could not fetch the pinned Qwen source'
  git -C "$stage" checkout -q --detach "$KILIX_QWEN_CLIENT_REF"
  mv -- "$stage" "$source_dir" || fail 'could not publish the pinned Qwen source'
  trap - EXIT
fi
[ "$(git -C "$source_dir" rev-parse HEAD 2>/dev/null)" = "$KILIX_QWEN_CLIENT_REF" ] \
  || fail 'the managed Qwen source has a different commit'
[ -z "$(git -C "$source_dir" status --porcelain 2>/dev/null)" ] \
  || fail 'the managed Qwen source has local changes'
package="$source_dir/src/kilix_qwen_tts"
[ -f "$package/service.py" ] && [ -f "$package/provider-interface-candidate-v1.json" ] \
    && [ ! -L "$package" ] && [ -z "$(find "$package" -type l -print -quit)" ] \
  || fail 'the pinned client package is incomplete or contains symlinks'
[ ! -e "$generation" ] || fail 'an incomplete client generation already exists'
stage="$(mktemp -d "$root/generations/.qwen-client.XXXXXX")" \
  || fail 'could not stage the Qwen client generation'
trap 'rm -rf -- "$stage"' EXIT
mkdir -p -- "$stage/bin" "$stage/lib"
cp -R -- "$package" "$stage/lib/kilix_qwen_tts"
find "$stage/lib/kilix_qwen_tts" -name __pycache__ -type d -prune -exec rm -rf -- {} +
install -m 0700 "$KILIX_HOME/scripts/kilix-qwen-client-launch.sh" "$stage/bin/kilix-qwen-tts"
printf '%s\n' "$KILIX_QWEN_CLIENT_REF" >"$stage/.kilix-verified"
"$stage/bin/kilix-qwen-tts" --help >/dev/null \
  || fail 'the pinned Qwen client failed import verification'
mv -- "$stage" "$generation" || fail 'could not publish the Qwen client generation'
trap - EXIT
link="$root/.current-$$"
ln -s -- "$generation" "$link"
mv -fT -- "$link" "$current" || fail 'could not publish the Qwen client'
printf '%s\n' "$current/bin/kilix-qwen-tts"
