#!/usr/bin/env bash
# Install the exact kilix-llm closure selected by this Kilix checkout.
#
# Place the `kilix-llm` launcher and its vendored llama.cpp source (a shallow
# clone of the pinned commit). Nothing is compiled and no weights are
# downloaded: the first `kilix-llm run`, `chat` or `serve` builds the runtime
# it needs, and models arrive through the model wizard.
set -euo pipefail
umask 077

GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_LLM_PREFIX="${KILIX_LLM_PREFIX:-$HOME/.local}"
KILIX_LLM_SOURCES="${KILIX_LLM_SOURCES:-$KILIX_STORAGE_HOME/sources}"

# Immutable source revision, inherited by the parent release manifest: the
# kilix-llm commit with qwen-0.8b-no-loop-02, its `chat` command and the
# first-use build that keeps only the runtime's bin/. pull of that id is
# refused; the build does not fetch llama.cpp's web UI.
KILIX_LLM_REPO="${KILIX_LLM_REPO:-https://github.com/itsmygithubacct/kilix-llm.git}"
KILIX_LLM_REF="${KILIX_LLM_REF:-93f2202be062cb443daf9a3d47243098d9567bf6}"

die() { printf 'kilix llm: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix llm: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-kilix-llm.sh [--force|--print-refs]

  --force       compatibility flag; generated launchers are always refreshed
  --print-refs  print the immutable source closure without changing anything
EOF
}

case "${1:-}" in
  '') ;;
  --force) shift ;;
  --print-refs) printf '%s\n' "kilix-llm=$KILIX_LLM_REF"; exit 0 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[ $# -eq 0 ] || { usage >&2; exit 2; }
[ "$(id -u)" -ne 0 ] || die "run this installer as the desktop user, not root"

[ "$KILIX_LLM_REF" != unset ] \
  || die "KILIX_LLM_REF is unset: kilix-llm has no published commit to pin yet"
[[ "$KILIX_LLM_REF" =~ ^[0-9a-fA-F]{40}$ ]] \
  || die "KILIX_LLM_REF must be a full 40-character commit SHA"

command -v git >/dev/null 2>&1 || die "git is required"
command -v uv >/dev/null 2>&1 || die "uv is required to run kilix-llm"

checkout="$KILIX_LLM_SOURCES/kilix-llm"
mkdir -p -- "$KILIX_LLM_SOURCES" || die "could not create $KILIX_LLM_SOURCES"

if git -C "$checkout" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  origin="$(git -C "$checkout" remote get-url origin 2>/dev/null || true)"
  [ "$origin" = "$KILIX_LLM_REPO" ] \
    || die "checkout has origin '${origin:-missing}', expected '$KILIX_LLM_REPO': $checkout"
  [ -z "$(git -C "$checkout" status --porcelain --untracked-files=normal)" ] \
    || die "checkout has local changes; refusing to install an unpinned tree: $checkout"
  head="$(git -C "$checkout" rev-parse HEAD 2>/dev/null || true)"
  if [ "${head,,}" != "${KILIX_LLM_REF,,}" ]; then
    git -C "$checkout" fetch --quiet origin "$KILIX_LLM_REF" \
      || die "commit $KILIX_LLM_REF is unavailable from $KILIX_LLM_REPO"
    git -C "$checkout" checkout --quiet --detach "$KILIX_LLM_REF" \
      || die "could not check out $KILIX_LLM_REF"
  fi
else
  [ ! -e "$checkout" ] || die "path exists but is not a Git checkout: $checkout"
  clone_tmp="$(mktemp -d "$KILIX_LLM_SOURCES/.clone.XXXXXX")" \
    || die "could not allocate a temporary clone directory"
  trap 'rm -rf -- "${clone_tmp:-}"' EXIT
  log "cloning pinned kilix-llm -> $checkout"
  git clone --quiet --no-checkout -- "$KILIX_LLM_REPO" "$clone_tmp/checkout" \
    || die "could not clone kilix-llm from $KILIX_LLM_REPO"
  git -C "$clone_tmp/checkout" checkout --quiet --detach "$KILIX_LLM_REF" \
    || die "commit $KILIX_LLM_REF is unavailable from $KILIX_LLM_REPO"
  head="$(git -C "$clone_tmp/checkout" rev-parse HEAD)"
  [ "${head,,}" = "${KILIX_LLM_REF,,}" ] \
    || die "kilix-llm resolved to the wrong commit"
  mv -- "$clone_tmp/checkout" "$checkout" || die "could not publish the checkout"
fi

# kilix-llm's first-use build compiles this source; it must be the pinned
# commit. Only that commit is fetched: llama.cpp's history is about 480 MB.
git -C "$checkout" submodule update --init --recursive --depth 1 \
  || die "could not fetch kilix-llm's pinned llama.cpp source"
pinned="$(sed -n 's/^commit=//p' "$checkout/third_party/llama.cpp.pin" 2>/dev/null || true)"
vendored="$(git -C "$checkout/third_party/llama.cpp" rev-parse HEAD 2>/dev/null || true)"
[[ "$pinned" =~ ^[0-9a-f]{40}$ ]] && [ "$vendored" = "$pinned" ] \
  || die "vendored llama.cpp ${vendored:-missing} does not match the kilix-llm pin ${pinned:-missing}"

KILIX_LLM_PREFIX="$KILIX_LLM_PREFIX" "$checkout/install.sh" \
  || die "the kilix-llm installer failed"
[ -x "$KILIX_LLM_PREFIX/bin/kilix-llm" ] \
  || die "installer did not create $KILIX_LLM_PREFIX/bin/kilix-llm"
log "installed at $KILIX_LLM_REF; the first run, chat or serve builds the runtime (or: kilix-llm build --backend cpu)"
