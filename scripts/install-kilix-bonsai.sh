#!/usr/bin/env bash
# Install the exact Kilix Bonsai closure selected by this Kilix checkout.
#
# Kilix Bonsai is the BitNet model store: one folder per model, each with its
# own dependency and download scripts, and one TUI over all of them. Installing
# it places the `kilix-bonsai` command; it downloads no weights. Those are
# separate decisions and the installer keeps them separate — a `kilix update`
# must never start an eleven-gigabyte transfer.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_BONSAI_PREFIX="${KILIX_BONSAI_PREFIX:-$HOME/.local}"
KILIX_BONSAI_SOURCES="${KILIX_BONSAI_SOURCES:-$KILIX_STORAGE_HOME/sources}"

# Kilix Bonsai is part of Kilix's source closure. Plebian-OS pins the parent
# Kilix commit, so every network-fetched input is transitive and immutable
# without adding another independently coordinated release ref.
#
# A full commit SHA, never a branch name: a branch installs whatever HEAD
# happened to be at install time and does so silently, so the checks below
# refuse anything that is not forty hex characters. `unset` is still accepted
# as an explicit override for testing the refusal path; it is not the default
# any more, because the repository is published.
KILIX_BONSAI_REPO="${KILIX_BONSAI_REPO:-https://github.com/itsmygithubacct/kilix-bonsai.git}"
KILIX_BONSAI_REF="${KILIX_BONSAI_REF:-b54e617968f63594bb5e6b887b4ed4e5a8b7f055}"

die() { printf 'kilix bonsai: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix bonsai: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-kilix-bonsai.sh [--force|--print-refs]

  --force       reinstall even when the pinned closure is already current
  --print-refs  print the immutable source closure without changing anything
EOF
}

force=0
case "${1:-}" in
  '') ;;
  --force) force=1; shift ;;
  --print-refs) printf '%s\n' "kilix-bonsai=$KILIX_BONSAI_REF"; exit 0 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[ $# -eq 0 ] || { usage >&2; exit 2; }
[ "$(id -u)" -ne 0 ] || die "run this installer as the desktop user, not root"

[ "$KILIX_BONSAI_REF" != unset ] \
  || die "KILIX_BONSAI_REF is unset: kilix-bonsai has no published commit to pin yet"
[[ "$KILIX_BONSAI_REF" =~ ^[0-9a-fA-F]{40}$ ]] \
  || die "KILIX_BONSAI_REF must be a full 40-character commit SHA"

command -v git >/dev/null 2>&1 || die "git is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

checkout="$KILIX_BONSAI_SOURCES/kilix-bonsai"
mkdir -p -- "$KILIX_BONSAI_SOURCES" || die "could not create $KILIX_BONSAI_SOURCES"

if git -C "$checkout" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  origin="$(git -C "$checkout" remote get-url origin 2>/dev/null || true)"
  [ "$origin" = "$KILIX_BONSAI_REPO" ] \
    || die "checkout has origin '${origin:-missing}', expected '$KILIX_BONSAI_REPO': $checkout"
  [ -z "$(git -C "$checkout" status --porcelain --untracked-files=normal)" ] \
    || die "checkout has local changes; refusing to install an unpinned tree: $checkout"
  head="$(git -C "$checkout" rev-parse HEAD 2>/dev/null || true)"
  if [ "${head,,}" != "${KILIX_BONSAI_REF,,}" ]; then
    git -C "$checkout" fetch --quiet origin "$KILIX_BONSAI_REF" \
      || die "commit $KILIX_BONSAI_REF is unavailable from $KILIX_BONSAI_REPO"
    git -C "$checkout" checkout --quiet --detach "$KILIX_BONSAI_REF" \
      || die "could not check out $KILIX_BONSAI_REF"
  elif [ "$force" = 0 ] \
       && [ -x "$KILIX_BONSAI_PREFIX/bin/kilix-bonsai" ]; then
    log "already at $KILIX_BONSAI_REF"
    exit 0
  fi
else
  [ ! -e "$checkout" ] || die "path exists but is not a Git checkout: $checkout"
  clone_tmp="$(mktemp -d "$KILIX_BONSAI_SOURCES/.clone.XXXXXX")" \
    || die "could not allocate a temporary clone directory"
  trap 'rm -rf -- "${clone_tmp:-}"' EXIT
  log "cloning pinned kilix-bonsai -> $checkout"
  git clone --quiet --no-checkout -- "$KILIX_BONSAI_REPO" "$clone_tmp/checkout" \
    || die "could not clone kilix-bonsai from $KILIX_BONSAI_REPO"
  git -C "$clone_tmp/checkout" checkout --quiet --detach "$KILIX_BONSAI_REF" \
    || die "commit $KILIX_BONSAI_REF is unavailable from $KILIX_BONSAI_REPO"
  head="$(git -C "$clone_tmp/checkout" rev-parse HEAD)"
  [ "${head,,}" = "${KILIX_BONSAI_REF,,}" ] \
    || die "kilix-bonsai resolved to the wrong commit"
  mv -- "$clone_tmp/checkout" "$checkout" || die "could not publish the checkout"
fi

KILIX_BONSAI_PREFIX="$KILIX_BONSAI_PREFIX" "$checkout/install.sh" \
  || die "the kilix-bonsai installer failed"
[ -x "$KILIX_BONSAI_PREFIX/bin/kilix-bonsai" ] \
  || die "installer did not create $KILIX_BONSAI_PREFIX/bin/kilix-bonsai"
log "installed at $KILIX_BONSAI_REF"
