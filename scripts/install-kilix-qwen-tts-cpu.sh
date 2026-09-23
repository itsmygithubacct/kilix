#!/usr/bin/env bash
# Install the locked CPU audition runtime only when its tier is selected.
set -euo pipefail
umask 077

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_DATA_HOME="${KILIX_DATA_HOME:-$GPU_TERMINAL_HOME/kilix/data}"
project="$KILIX_HOME/runtimes/qwen-cpu"
root="$KILIX_DATA_HOME/voice/qwen-cpu"
current="$root/current"

fail() { printf 'kilix qwen cpu: %s\n' "$*" >&2; exit 1; }
[ -f "$project/pyproject.toml" ] && [ -f "$project/uv.lock" ] \
  && [ ! -L "$project" ] && [ ! -L "$project/pyproject.toml" ] \
  && [ ! -L "$project/uv.lock" ] \
  || fail 'the locked CPU runtime project is unavailable'
digest="$(cd "$project" && sha256sum pyproject.toml uv.lock | sha256sum)" \
  || fail 'could not identify the locked runtime'
digest="${digest%% *}"
generation="$root/generations/$digest"
python="$generation/bin/python"

case "${1:-}" in
  --print-path)
    [ "$#" = 1 ] || fail 'usage: install-kilix-qwen-tts-cpu.sh [--print-path]'
    printf '%s\n' "$current/bin/python"
    exit 0 ;;
  '') [ "$#" = 0 ] || fail 'usage: install-kilix-qwen-tts-cpu.sh [--print-path]' ;;
  *) fail 'usage: install-kilix-qwen-tts-cpu.sh [--print-path]' ;;
esac

[ "$(id -u)" -ne 0 ] || fail 'run this as the desktop user'
[ "$(uname -m)" = x86_64 ] || fail 'this locked CPU runtime supports x86_64 only'
[ -x /usr/bin/python3.13 ] || fail 'the Debian Python 3.13 interpreter is required'
command -v uv >/dev/null || fail 'uv is required for the locked CPU runtime'
case "$root" in /|"$HOME") fail 'refusing a broad runtime directory' ;; esac
mkdir -p -- "$root/generations" "$root/tmp"
[ ! -e "$current" ] || [ -L "$current" ] \
  || fail 'the managed Qwen current path is not a symlink'
exec 9<"$root"
flock 9
if [ -L "$current" ]; then
  previous="$(readlink -- "$current")"
  case "$previous" in "$root/generations/"*) ;; \
    *) fail 'the managed Qwen current link points outside the generation store' ;; esac
fi
if [ -L "$current" ] && [ "$(readlink -- "$current")" = "$generation" ] \
    && [ -x "$python" ] && [ -f "$generation/.kilix-verified" ] \
    && [ "$(<"$generation/.kilix-verified")" = "$digest" ]; then
  printf '%s\n' "$current/bin/python"
  exit 0
fi

TMPDIR="$root/tmp" UV_PROJECT_ENVIRONMENT="$generation" \
  uv sync --locked --no-dev --project "$project" --python /usr/bin/python3.13 \
  || fail 'the locked Qwen CPU runtime could not be installed'
[ -x "$python" ] \
  && "$python" -c 'import qwen_tts, torch, torchaudio, transformers; assert torch.__version__ == "2.6.0+cpu"' \
       >/dev/null 2>&1 \
  || fail 'the locked Qwen CPU runtime failed import verification'
printf '%s\n' "$digest" >"$generation/.kilix-verified"
link="$root/.current-$$"
ln -s -- "$generation" "$link"
mv -fT -- "$link" "$current" || fail 'could not publish the Qwen CPU runtime'
printf 'kilix qwen cpu: runtime ready; model downloads after first-use notice\n' >&2
printf '%s\n' "$current/bin/python"
