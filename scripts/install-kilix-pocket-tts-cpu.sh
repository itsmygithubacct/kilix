#!/usr/bin/env bash
# Install the locked Pocket TTS Python runtime only after explicit selection.
set -euo pipefail
umask 077

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_DATA_HOME="${KILIX_DATA_HOME:-$GPU_TERMINAL_HOME/kilix/data}"
project="$KILIX_HOME/runtimes/pocket-cpu"
root="$KILIX_DATA_HOME/voice/pocket-cpu"
current="$root/current"

fail() { printf 'kilix pocket cpu: %s\n' "$*" >&2; exit 1; }
[ -f "$project/pyproject.toml" ] && [ -f "$project/uv.lock" ] \
  && [ ! -L "$project" ] && [ ! -L "$project/pyproject.toml" ] \
  && [ ! -L "$project/uv.lock" ] \
  || fail 'the locked Pocket runtime project is unavailable'
digest="$(cd "$project" && sha256sum pyproject.toml uv.lock | sha256sum)" \
  || fail 'could not identify the locked runtime'
digest="${digest%% *}"
generation="$root/generations/$digest"
python="$generation/bin/python"

case "${1:-}" in
  --print-path)
    [ "$#" = 1 ] || fail 'usage: install-kilix-pocket-tts-cpu.sh [--print-path]'
    printf '%s\n' "$current/bin/python"
    exit 0 ;;
  '') [ "$#" = 0 ] || fail 'usage: install-kilix-pocket-tts-cpu.sh [--print-path]' ;;
  *) fail 'usage: install-kilix-pocket-tts-cpu.sh [--print-path]' ;;
esac

[ "$(id -u)" -ne 0 ] || fail 'run this as the desktop user'
[ "$(uname -m)" = x86_64 ] || fail 'this locked runtime supports x86_64 only'
[ -x /usr/bin/python3.13 ] || fail 'the Debian Python 3.13 interpreter is required'
uv_cmd="$(command -v uv || true)"
if [ -z "$uv_cmd" ] && [ -x "$HOME/.local/bin/uv" ]; then
  uv_cmd="$HOME/.local/bin/uv"
fi
[ -n "$uv_cmd" ] || fail 'uv is required for the locked Pocket runtime'
case "$root" in /|"$HOME") fail 'refusing a broad runtime directory' ;; esac
mkdir -p -- "$root/generations" "$root/tmp"
[ ! -e "$current" ] || [ -L "$current" ] \
  || fail 'the managed Pocket current path is not a symlink'
exec 9<"$root"
flock 9
if [ -L "$current" ]; then
  previous="$(readlink -- "$current")"
  case "$previous" in "$root/generations/"*) ;; \
    *) fail 'the managed Pocket current link points outside the generation store' ;; esac
fi
if [ -L "$current" ] && [ "$(readlink -- "$current")" = "$generation" ] \
    && [ -x "$python" ] && [ -f "$generation/.kilix-verified" ] \
    && [ "$(<"$generation/.kilix-verified")" = "$digest" ]; then
  printf '%s\n' "$current/bin/python"
  exit 0
fi

TMPDIR="$root/tmp" UV_PROJECT_ENVIRONMENT="$generation" \
  "$uv_cmd" sync --locked --no-dev --project "$project" --python /usr/bin/python3.13 \
  || fail 'the locked Pocket CPU runtime could not be installed'
[ -x "$python" ] \
  && "$python" -c 'import pocket_tts, torch, torchaudio; from pocket_tts import TTSModel; assert torch.__version__ == "2.6.0+cpu"; assert callable(TTSModel.load_model)' \
       >/dev/null 2>&1 \
  || fail 'the locked Pocket CPU runtime failed import verification'
printf '%s\n' "$digest" >"$generation/.kilix-verified"
link="$root/.current-$$"
ln -s -- "$generation" "$link"
mv -fT -- "$link" "$current" || fail 'could not publish the Pocket CPU runtime'
printf 'kilix pocket cpu: runtime ready; model installation still requires first-use acceptance\n' >&2
printf '%s\n' "$current/bin/python"
