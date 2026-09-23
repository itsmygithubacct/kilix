#!/usr/bin/env bash
# Install the pinned Piper runtime only when the user selects its TTS tier.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$GPU_TERMINAL_HOME/sources}"
KILIX_DATA_HOME="${KILIX_DATA_HOME:-$GPU_TERMINAL_HOME/kilix/data}"
KILIX_PIPER_REF=a246e281ed0190cd2c963974be934dfe51359dfb
KILIX_PIPER_REPO=https://github.com/itsmygithubacct/kilix-piper-tts.git
KILIX_CONTENT_REF=d9a1335db520594c6796209b0f9342000a2b34e7

fail() { printf 'kilix piper: %s\n' "$*" >&2; exit 1; }
root="$KILIX_DATA_HOME/voice/piper"
source_dir="$GPU_TERMINAL_SOURCE_HOME/.kilix-piper-tts-$KILIX_PIPER_REF"
generation="$root/generations/$KILIX_PIPER_REF"
current="$root/current"
binary="$generation/bin/kilix-piper-tts"

if [ "${1:-}" = --print-ref ]; then
  [ "$#" = 1 ] || fail 'usage: install-kilix-piper-tts.sh [--print-ref|--print-path]'
  printf '%s\n' "kilix-piper-tts=$KILIX_PIPER_REF"
  exit 0
fi
if [ "${1:-}" = --print-path ]; then
  [ "$#" = 1 ] || fail 'usage: install-kilix-piper-tts.sh [--print-ref|--print-path]'
  printf '%s\n' "$current/bin/kilix-piper-tts"
  exit 0
fi
[ "$#" = 0 ] || fail 'usage: install-kilix-piper-tts.sh [--print-ref|--print-path]'
[ "$(id -u)" -ne 0 ] || fail 'run this as the desktop user'
case "$root" in /|"$HOME") fail 'refusing a broad runtime directory' ;; esac
case "$source_dir" in /|"$HOME") fail 'refusing a broad source directory' ;; esac
mkdir -p -- "$GPU_TERMINAL_SOURCE_HOME" "$root/generations"
[ ! -e "$current" ] || [ -L "$current" ] \
  || fail 'the managed Piper current path is not a symlink'
exec 9<"$root"
flock 9
[ ! -L "$current" ] || [ "$(readlink -- "$current")" = "$generation" ] \
  || fail 'the managed Piper current link points outside this release generation'

# A completed generation is reusable; no package or model fetch on repeat use.
if [ -L "$current" ] && [ "$(readlink -- "$current")" = "$generation" ] \
    && [ -x "$binary" ] && "$binary" --version >/dev/null 2>&1; then
  exit 0
fi

content="$KILIX_HOME/third_party/kilix-content"
[ -f "$content/pyproject.toml" ] && [ ! -L "$content" ] \
  || fail 'the pinned Kilix Content checkout is unavailable'
command -v git >/dev/null || fail 'git is required for the pinned Piper source'
command -v uv >/dev/null || fail 'uv is required for the pinned Piper runtime'
[ "$(git -C "$content" rev-parse HEAD 2>/dev/null)" = "$KILIX_CONTENT_REF" ] \
  || fail 'the Kilix Content checkout has the wrong pinned commit'
[ -z "$(git -C "$content" status --porcelain 2>/dev/null)" ] \
  || fail 'the Kilix Content checkout has local changes'
if [ ! -d "$source_dir" ]; then
  stage="$(mktemp -d "$GPU_TERMINAL_SOURCE_HOME/.kilix-piper-tts-stage.XXXXXX")" \
    || fail 'could not stage the Piper source'
  trap 'rm -rf -- "$stage"' EXIT
  git -C "$stage" init -q
  git -C "$stage" remote add origin "$KILIX_PIPER_REPO"
  git -C "$stage" fetch -q --depth=1 origin "$KILIX_PIPER_REF" \
    || fail 'could not fetch the pinned Piper source'
  git -C "$stage" checkout -q --detach "$KILIX_PIPER_REF"
  mv -- "$stage" "$source_dir" || fail 'could not publish the pinned Piper source'
  trap - EXIT
fi
[ "$(git -C "$source_dir" rev-parse HEAD 2>/dev/null)" = "$KILIX_PIPER_REF" ] \
  || fail 'the managed Piper source has a different commit'
[ -z "$(git -C "$source_dir" status --porcelain 2>/dev/null)" ] \
  || fail 'the managed Piper source has local changes'

UV_PROJECT_ENVIRONMENT="$generation" uv sync --locked --no-dev \
  --directory "$source_dir" --project "$source_dir" \
  || fail 'the pinned Piper environment could not be installed'
uv pip install --python "$generation/bin/python" --no-deps "$content" \
  || fail 'the pinned Content package could not be installed for Piper'
[ -x "$binary" ] && "$binary" --version >/dev/null 2>&1 \
  || fail 'the pinned Piper command failed verification'
link="$root/.current-$$"
ln -s -- "$generation" "$link"
mv -fT -- "$link" "$current" || fail 'could not publish the Piper runtime'
printf 'kilix piper: runtime ready; model downloads when first selected\n' >&2
