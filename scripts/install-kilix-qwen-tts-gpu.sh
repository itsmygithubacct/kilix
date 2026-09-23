#!/usr/bin/env bash
# Install the locked CUDA/FlashAttention audition runtime on an eligible GPU.
set -euo pipefail
umask 077

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_DATA_HOME="${KILIX_DATA_HOME:-$GPU_TERMINAL_HOME/kilix/data}"
project="$KILIX_HOME/runtimes/qwen-gpu"
root="$KILIX_DATA_HOME/voice/qwen-gpu"
current="$root/current"

fail() { printf 'kilix qwen gpu: %s\n' "$*" >&2; exit 1; }
[ -f "$project/pyproject.toml" ] && [ -f "$project/uv.lock" ] \
  && [ ! -L "$project" ] && [ ! -L "$project/pyproject.toml" ] \
  && [ ! -L "$project/uv.lock" ] \
  || fail 'the locked GPU runtime project is unavailable'
digest="$(cd "$project" && sha256sum pyproject.toml uv.lock | sha256sum)" \
  || fail 'could not identify the locked runtime'
digest="${digest%% *}"
generation="$root/generations/$digest"
python="$generation/bin/python"

case "${1:-}" in
  --print-path)
    [ "$#" = 1 ] || fail 'usage: install-kilix-qwen-tts-gpu.sh [--print-path]'
    printf '%s\n' "$current/bin/python"
    exit 0 ;;
  '') [ "$#" = 0 ] || fail 'usage: install-kilix-qwen-tts-gpu.sh [--print-path]' ;;
  *) fail 'usage: install-kilix-qwen-tts-gpu.sh [--print-path]' ;;
esac

[ "$(id -u)" -ne 0 ] || fail 'run this as the desktop user'
[ "$(uname -m)" = x86_64 ] || fail 'this locked GPU runtime supports x86_64 only'
[ -x /usr/bin/python3.13 ] || fail 'the Debian Python 3.13 interpreter is required'
[ "${CUDA_VISIBLE_DEVICES:-0}" = 0 ] \
  || fail 'physical GPU 0 must be unmasked and not remapped'
command -v nvidia-smi >/dev/null || fail 'NVIDIA driver tools are required'
capability="$(nvidia-smi -i 0 --query-gpu=compute_cap --format=csv,noheader 2>/dev/null)" \
  || fail 'NVIDIA GPU 0 is unavailable'
case "${capability%%.*}" in
  [89]|[1-9][0-9]*) ;;
  *) fail 'FlashAttention 2 requires an Ampere-or-newer NVIDIA GPU 0' ;;
esac
uv_cmd="$(command -v uv || true)"
if [ -z "$uv_cmd" ] && [ -x "$HOME/.local/bin/uv" ]; then
  uv_cmd="$HOME/.local/bin/uv"
fi
[ -n "$uv_cmd" ] || fail 'uv is required for the locked GPU runtime'
case "$root" in /|"$HOME") fail 'refusing a broad runtime directory' ;; esac
mkdir -p -- "$root/generations" "$root/tmp"
[ ! -e "$current" ] || [ -L "$current" ] \
  || fail 'the managed Qwen GPU current path is not a symlink'
exec 9<"$root"
flock 9
if [ -L "$current" ]; then
  previous="$(readlink -- "$current")"
  case "$previous" in "$root/generations/"*) ;; \
    *) fail 'the managed Qwen GPU current link points outside the generation store' ;; esac
fi
if [ -L "$current" ] && [ "$(readlink -- "$current")" = "$generation" ] \
    && [ -x "$python" ] && [ -f "$generation/.kilix-verified" ] \
    && [ "$(<"$generation/.kilix-verified")" = "$digest" ]; then
  printf '%s\n' "$current/bin/python"
  exit 0
fi

TMPDIR="$root/tmp" UV_PROJECT_ENVIRONMENT="$generation" \
  "$uv_cmd" sync --locked --no-dev --project "$project" --python /usr/bin/python3.13 \
  || fail 'the locked Qwen GPU runtime could not be installed'
[ -x "$python" ] \
  && "$python" -c '
import flash_attn, qwen_tts, torch, torchaudio, transformers
from flash_attn import flash_attn_func
assert torch.__version__ == "2.6.0+cu124"
assert torch.cuda.is_available() and torch.cuda.get_device_capability(0)[0] >= 8
q = torch.randn(1, 16, 2, 64, device="cuda:0", dtype=torch.bfloat16)
y = flash_attn_func(q, q, q, causal=True)
torch.cuda.synchronize()
assert y.shape == q.shape and bool(torch.isfinite(y).all())
' \
       >/dev/null 2>&1 \
  || fail 'the locked Qwen GPU runtime failed CUDA/FlashAttention verification'
printf '%s\n' "$digest" >"$generation/.kilix-verified"
link="$root/.current-$$"
ln -s -- "$generation" "$link"
mv -fT -- "$link" "$current" || fail 'could not publish the Qwen GPU runtime'
printf 'kilix qwen gpu: runtime ready; model downloads after first-use notice\n' >&2
printf '%s\n' "$current/bin/python"
