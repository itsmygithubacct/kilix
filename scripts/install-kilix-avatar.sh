#!/usr/bin/env bash
# Install the exact Kilix Avatar closure selected by this Kilix checkout.
#
# Build and install Avatar; model downloads and credentials remain explicit.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_AVATAR_PREFIX="${KILIX_AVATAR_PREFIX:-$HOME/.local}"
KILIX_AVATAR_SOURCES="${KILIX_AVATAR_SOURCES:-$KILIX_STORAGE_HOME/sources}"

# Immutable source revision, inherited by the parent release manifest.
KILIX_AVATAR_REPO="${KILIX_AVATAR_REPO:-https://github.com/itsmygithubacct/kilix-avatar.git}"
KILIX_AVATAR_REF="${KILIX_AVATAR_REF:-9021e60c6b892ad3bd10e20fec9fc84fdcf98eff}"

die() { printf 'kilix avatar: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix avatar: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-kilix-avatar.sh [--force|--print-refs]

  --force       compatibility flag; generated launchers are always refreshed
  --print-refs  print the immutable source closure without changing anything
EOF
}

case "${1:-}" in
  '') ;;
  --force) shift ;;
  --print-refs) printf '%s\n' "kilix-avatar=$KILIX_AVATAR_REF"; exit 0 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[ $# -eq 0 ] || { usage >&2; exit 2; }
[ "$(id -u)" -ne 0 ] || die "run this installer as the desktop user, not root"

[ "$KILIX_AVATAR_REF" != unset ] \
  || die "KILIX_AVATAR_REF is unset: kilix-avatar has no published commit to pin yet"
[[ "$KILIX_AVATAR_REF" =~ ^[0-9a-fA-F]{40}$ ]] \
  || die "KILIX_AVATAR_REF must be a full 40-character commit SHA"

command -v git >/dev/null 2>&1 || die "git is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v make >/dev/null 2>&1 || die "make is required"
command -v pkg-config >/dev/null 2>&1 || die "pkg-config is required"
pkg-config --exists freetype2 fontconfig zlib \
  || die "install build-essential, pkg-config, libfreetype-dev, libfontconfig-dev and zlib1g-dev"

checkout="$KILIX_AVATAR_SOURCES/kilix-avatar"
mkdir -p -- "$KILIX_AVATAR_SOURCES" || die "could not create $KILIX_AVATAR_SOURCES"

if git -C "$checkout" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  origin="$(git -C "$checkout" remote get-url origin 2>/dev/null || true)"
  [ "$origin" = "$KILIX_AVATAR_REPO" ] \
    || die "checkout has origin '${origin:-missing}', expected '$KILIX_AVATAR_REPO': $checkout"
  [ -z "$(git -C "$checkout" status --porcelain --untracked-files=normal)" ] \
    || die "checkout has local changes; refusing to install an unpinned tree: $checkout"
  head="$(git -C "$checkout" rev-parse HEAD 2>/dev/null || true)"
  if [ "${head,,}" != "${KILIX_AVATAR_REF,,}" ]; then
    git -C "$checkout" fetch --quiet origin "$KILIX_AVATAR_REF" \
      || die "commit $KILIX_AVATAR_REF is unavailable from $KILIX_AVATAR_REPO"
    git -C "$checkout" checkout --quiet --detach "$KILIX_AVATAR_REF" \
      || die "could not check out $KILIX_AVATAR_REF"
  fi
else
  [ ! -e "$checkout" ] || die "path exists but is not a Git checkout: $checkout"
  clone_tmp="$(mktemp -d "$KILIX_AVATAR_SOURCES/.clone.XXXXXX")" \
    || die "could not allocate a temporary clone directory"
  trap 'rm -rf -- "${clone_tmp:-}"' EXIT
  log "cloning pinned kilix-avatar -> $checkout"
  git clone --quiet --no-checkout -- "$KILIX_AVATAR_REPO" "$clone_tmp/checkout" \
    || die "could not clone kilix-avatar from $KILIX_AVATAR_REPO"
  git -C "$clone_tmp/checkout" checkout --quiet --detach "$KILIX_AVATAR_REF" \
    || die "commit $KILIX_AVATAR_REF is unavailable from $KILIX_AVATAR_REPO"
  head="$(git -C "$clone_tmp/checkout" rev-parse HEAD)"
  [ "${head,,}" = "${KILIX_AVATAR_REF,,}" ] \
    || die "kilix-avatar resolved to the wrong commit"
  mv -- "$clone_tmp/checkout" "$checkout" || die "could not publish the checkout"
fi

git -C "$checkout" submodule update --init --recursive \
  || die "could not fetch Avatar's pinned build dependencies"
make -C "$checkout" verify-dependencies \
  || die "Avatar build dependencies do not match the release pins"
make -C "$checkout" -j2 PREFIX="$KILIX_AVATAR_PREFIX" install \
  || die "the kilix-avatar build/install failed"
[ -x "$KILIX_AVATAR_PREFIX/bin/kilix-avatar" ] \
  || die "installer did not create $KILIX_AVATAR_PREFIX/bin/kilix-avatar"
# Read-only fit planning is pinned by this host; it downloads no voice weights.
"$KILIX_HOME/scripts/install-kilix-tts-sizer.sh" >/dev/null \
  || die "Avatar installed, but its speech fit planner could not be installed"
log "installed at $KILIX_AVATAR_REF"
