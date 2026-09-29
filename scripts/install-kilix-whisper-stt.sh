#!/usr/bin/env bash
# Install the pinned Whisper dictation runtime (kilix-whisper-stt).
#
# kilix-voice runs Whisper dictation models through the kilix-whisper-stt
# provider: faster-whisper, pinned by the provider's uv.lock, in a private
# environment published as $KILIX_DATA_HOME/voice/whisper/current/bin/
# kilix-whisper-stt, the path kilix-voice resolves. The provider holds no
# model and never downloads one: the weights are the licence-gated
# faster-whisper-small-en asset, installed by `kilix stt --install`.
set -euo pipefail
umask 077

GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$GPU_TERMINAL_HOME/sources}"
KILIX_DATA_HOME="${KILIX_DATA_HOME:-$GPU_TERMINAL_HOME/kilix/data}"
KILIX_WHISPER_REF=15ef23b32da497a41198d3028e34715801de6196
KILIX_WHISPER_REPO=https://github.com/itsmygithubacct/kilix-whisper-stt.git

fail() { printf 'kilix whisper: %s\n' "$*" >&2; exit 1; }
usage='usage: install-kilix-whisper-stt.sh [--print-ref|--print-path]'

root="$KILIX_DATA_HOME/voice/whisper"
source_dir="$GPU_TERMINAL_SOURCE_HOME/.kilix-whisper-stt-$KILIX_WHISPER_REF"
current="$root/current"
generation="$root/generations/$KILIX_WHISPER_REF"
binary="$generation/bin/kilix-whisper-stt"

case "${1:-}" in
  --print-ref)
    [ "$#" = 1 ] || fail "$usage"
    printf '%s\n' "kilix-whisper-stt=$KILIX_WHISPER_REF"; exit 0 ;;
  --print-path)
    [ "$#" = 1 ] || fail "$usage"
    printf '%s\n' "$current/bin/kilix-whisper-stt"; exit 0 ;;
  -h|--help) printf '%s\n' "$usage"; exit 0 ;;
esac
[ "$#" = 0 ] || fail "$usage"
[[ "$KILIX_WHISPER_REF" =~ ^[0-9a-f]{40}$ ]] \
  || fail 'the Whisper provider pin must be a full 40-character commit'
[ "$(id -u)" -ne 0 ] || fail 'run this as the desktop user'
case "$root" in /|"$HOME") fail 'refusing a broad runtime directory' ;; esac
case "$source_dir" in /|"$HOME") fail 'refusing a broad source directory' ;; esac

mkdir -p -- "$GPU_TERMINAL_SOURCE_HOME" "$root/generations"
[ ! -e "$current" ] || [ -L "$current" ] \
  || fail 'the managed Whisper current path is not a symlink'
exec 9<"$root"
flock 9
if [ -L "$current" ]; then
  case "$(readlink -- "$current")" in "$root/generations/"*) ;;
    *) fail 'the managed Whisper current link points outside the generation store' ;;
  esac
fi

# Current only if this exact pin built it: the generation records its ref,
# and a completed generation is reused with no package fetch.
if [ -L "$current" ] && [ "$(readlink -- "$current")" = "$generation" ] \
    && [ "$(cat -- "$generation/REF" 2>/dev/null)" = "kilix-whisper-stt=$KILIX_WHISPER_REF" ] \
    && [ -x "$binary" ] && "$binary" --version >/dev/null 2>&1; then
  printf 'kilix whisper: runtime ready at %s\n' "$current/bin/kilix-whisper-stt" >&2
  exit 0
fi

command -v git >/dev/null || fail 'git is required for the pinned Whisper provider source'
command -v uv >/dev/null || fail 'uv is required for the pinned Whisper runtime'
if [ ! -d "$source_dir" ]; then
  stage="$(mktemp -d "$GPU_TERMINAL_SOURCE_HOME/.kilix-whisper-stt-stage.XXXXXX")" \
    || fail 'could not stage the Whisper provider source'
  trap 'rm -rf -- "$stage"' EXIT
  git -C "$stage" init -q
  git -C "$stage" remote add origin "$KILIX_WHISPER_REPO"
  git -C "$stage" fetch -q --depth=1 origin "$KILIX_WHISPER_REF" \
    || fail 'could not fetch the pinned Whisper provider source'
  git -C "$stage" checkout -q --detach "$KILIX_WHISPER_REF"
  mv -- "$stage" "$source_dir" || fail 'could not publish the pinned Whisper provider source'
  trap - EXIT
fi
[ "$(git -C "$source_dir" rev-parse HEAD 2>/dev/null)" = "$KILIX_WHISPER_REF" ] \
  || fail 'the managed Whisper provider source has a different commit'
[ -z "$(git -C "$source_dir" status --porcelain 2>/dev/null)" ] \
  || fail 'the managed Whisper provider source has local changes'

printf 'kilix whisper: installing the pinned runtime (about 200 MB)…\n' >&2
rm -rf -- "$generation"
UV_PROJECT_ENVIRONMENT="$generation" uv sync --locked --no-dev \
  --directory "$source_dir" --project "$source_dir" --python 3.12.8 \
  || fail 'the pinned Whisper environment could not be installed'
[ -x "$binary" ] && "$binary" --version >/dev/null 2>&1 \
  || fail 'the pinned Whisper command failed verification'
printf '%s\n' "kilix-whisper-stt=$KILIX_WHISPER_REF" >"$generation/REF"
link="$root/.current-$$"
ln -s -- "$generation" "$link"
mv -fT -- "$link" "$current" || fail 'could not publish the Whisper runtime'
printf 'kilix whisper: runtime ready at %s\n' "$current/bin/kilix-whisper-stt" >&2
