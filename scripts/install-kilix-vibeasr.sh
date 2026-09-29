#!/usr/bin/env bash
# Build the pinned VibeASR.cpp runtime that runs VibeVoice dictation.
#
# kilix-voice runs vibevoice-asr-bitnet through VibeASR.cpp's `asr_infer`.
# This fetches exactly one upstream commit (and the llama.cpp submodule that
# commit records: XsquirrelC/llama.cpp, the VibeASR authors' fork carrying the
# BitNet kernels, at the gitlink hash), builds only `asr_infer` with llama.cpp
# and ggml linked in (libc, libstdc++ and libgomp stay the system's), and publishes it
# as $KILIX_DATA_HOME/voice/vibeasr/current/bin/asr_infer, the path kilix-voice
# resolves. Weights are not handled here: they are the licence-gated
# vibevoice-asr-bitnet model, installed by `kilix stt --install`.
set -euo pipefail
umask 077

GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$GPU_TERMINAL_HOME/sources}"
KILIX_DATA_HOME="${KILIX_DATA_HOME:-$GPU_TERMINAL_HOME/kilix/data}"
# microsoft/VibeASR.cpp, 2026-09-15; MIT. Its llama.cpp gitlink is a2fdadc2.
KILIX_VIBEASR_REF=c4334009c88060f86cdbbd684b62662f710b6c20
KILIX_VIBEASR_REPO=https://github.com/microsoft/VibeASR.cpp.git

fail() { printf 'kilix vibeasr: %s\n' "$*" >&2; exit 1; }
usage='usage: install-kilix-vibeasr.sh [--print-ref|--print-path]'

root="$KILIX_DATA_HOME/voice/vibeasr"
source_dir="$GPU_TERMINAL_SOURCE_HOME/.kilix-vibeasr-$KILIX_VIBEASR_REF"
current="$root/current"
generation="$root/generations/$KILIX_VIBEASR_REF"
binary="$generation/bin/asr_infer"

case "${1:-}" in
  --print-ref)
    [ "$#" = 1 ] || fail "$usage"
    printf '%s\n' "vibeasr=$KILIX_VIBEASR_REF"; exit 0 ;;
  --print-path)
    [ "$#" = 1 ] || fail "$usage"
    printf '%s\n' "$current/bin/asr_infer"; exit 0 ;;
  -h|--help) printf '%s\n' "$usage"; exit 0 ;;
esac
[ "$#" = 0 ] || fail "$usage"
[[ "$KILIX_VIBEASR_REF" =~ ^[0-9a-f]{40}$ ]] \
  || fail 'the VibeASR pin must be a full 40-character commit'
[ "$(id -u)" -ne 0 ] || fail 'run this as the desktop user'
case "$root" in /|"$HOME") fail 'refusing a broad runtime directory' ;; esac

mkdir -p -- "$GPU_TERMINAL_SOURCE_HOME" "$root/generations"
[ ! -e "$current" ] || [ -L "$current" ] \
  || fail 'the managed VibeASR current path is not a symlink'
exec 9<"$root"
flock 9
if [ -L "$current" ]; then
  case "$(readlink -- "$current")" in "$root/generations/"*) ;;
    *) fail 'the managed VibeASR current link points outside the generation store' ;;
  esac
fi
# asr_infer with no arguments prints its usage and exits non-zero; a binary
# that runs at all and names the model flags is the one this built.
runs() { local out; out="$("$1" 2>&1)" || true; [[ "$out" == *--vae-model* ]]; }
# Current only if this exact pin built it: the generation records its ref.
if [ -L "$current" ] && [ "$(readlink -- "$current")" = "$generation" ] \
    && [ "$(cat -- "$generation/REF" 2>/dev/null)" = "vibeasr=$KILIX_VIBEASR_REF" ] \
    && [ -x "$binary" ] && runs "$binary"; then
  printf 'kilix vibeasr: runtime ready at %s\n' "$current/bin/asr_infer" >&2
  exit 0
fi

for tool in git cmake make c++ cc; do
  command -v "$tool" >/dev/null \
    || fail "$tool is required to build VibeASR (Debian: sudo apt install build-essential cmake git)"
done

if [ ! -d "$source_dir" ]; then
  stage="$(mktemp -d "$GPU_TERMINAL_SOURCE_HOME/.kilix-vibeasr-stage.XXXXXX")" \
    || fail 'could not stage the VibeASR source'
  trap 'rm -rf -- "$stage"' EXIT
  git -C "$stage" init -q
  git -C "$stage" remote add origin "$KILIX_VIBEASR_REPO"
  git -C "$stage" fetch -q --depth=1 origin "$KILIX_VIBEASR_REF" \
    || fail 'could not fetch the pinned VibeASR source'
  git -C "$stage" checkout -q --detach "$KILIX_VIBEASR_REF"
  # The submodule commits are the ones this exact commit records.
  git -C "$stage" submodule update -q --init --recursive \
    || fail 'could not fetch the llama.cpp revision VibeASR pins'
  mv -- "$stage" "$source_dir" || fail 'could not publish the pinned VibeASR source'
  trap - EXIT
fi
[ "$(git -C "$source_dir" rev-parse HEAD 2>/dev/null)" = "$KILIX_VIBEASR_REF" ] \
  || fail 'the managed VibeASR source has a different commit'
[ -z "$(git -C "$source_dir" status --porcelain --ignore-submodules=none 2>/dev/null)" ] \
  || fail 'the managed VibeASR source has local changes'

build="$(mktemp -d "$root/.build.XXXXXX")" || fail 'could not stage the VibeASR build'
trap 'rm -rf -- "$build"' EXIT
jobs="$(nproc 2>/dev/null || echo 2)"
printf 'kilix vibeasr: building the pinned runtime (about a minute)…\n' >&2
cmake -S "$source_dir" -B "$build" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF >"$build/configure.log" 2>&1 \
  || { tail -20 "$build/configure.log" >&2; fail 'could not configure VibeASR'; }
cmake --build "$build" --target asr_infer -j "$jobs" >"$build/build.log" 2>&1 \
  || { tail -20 "$build/build.log" >&2; fail 'could not build VibeASR'; }
built="$build/bin/asr_infer"
[ -x "$built" ] && runs "$built" || fail 'the built asr_infer failed verification'

rm -rf -- "$generation"
mkdir -p -- "$generation/bin"
install -m 0755 -- "$built" "$binary"
cp -- "$source_dir/LICENSE" "$generation/LICENSE"
printf '%s\n' "vibeasr=$KILIX_VIBEASR_REF" >"$generation/REF"
rm -rf -- "$build"
trap - EXIT
runs "$binary" || fail 'the published asr_infer failed verification'
link="$root/.current-$$"
ln -s -- "$generation" "$link"
mv -fT -- "$link" "$current" || fail 'could not publish the VibeASR runtime'
printf 'kilix vibeasr: runtime ready at %s\n' "$current/bin/asr_infer" >&2
