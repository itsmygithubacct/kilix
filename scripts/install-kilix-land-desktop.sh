#!/usr/bin/env bash
# Prepare the exact Kilix Land desktop selected by this Kilix checkout.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
KILIX_LAND_DESKTOP_DIR="${KILIX_LAND_DESKTOP_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-land-desktop}"
if [ "$KILIX_LAND_DESKTOP_DIR" = "$GPU_TERMINAL_SOURCE_HOME/kilix-land-desktop" ] \
   && [ ! -e "$KILIX_LAND_DESKTOP_DIR" ] \
   && [ ! -L "$KILIX_LAND_DESKTOP_DIR" ]; then
  KILIX_LAND_DESKTOP_DIR="$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-land-desktop"
fi
KILIX_LAND_DESKTOP_REPO="${KILIX_LAND_DESKTOP_REPO:-https://github.com/itsmygithubacct/kilix-land-desktop.git}"
KILIX_LAND_DESKTOP_AUTO_INSTALL="${KILIX_LAND_DESKTOP_AUTO_INSTALL:-1}"
KILIX_LAND_DESKTOP_TRUST_EXISTING_CHECKOUT="${KILIX_LAND_DESKTOP_TRUST_EXISTING_CHECKOUT:-0}"
KILIX_LAND_DESKTOP_ALLOW_MUTABLE_REF="${KILIX_LAND_DESKTOP_ALLOW_MUTABLE_REF:-0}"

# This full commit is part of Kilix's transitive source closure. An existing
# sibling checkout remains a development checkout unless
# KILIX_LAND_DESKTOP_REF is explicitly set; a first-use download always
# resolves this immutable default.
KILIX_LAND_DESKTOP_DEFAULT_REF=c3275afdf31bfbc932c9d8511efcea79e473835e

die() { printf 'kilix land: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix land: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-kilix-land-desktop.sh [--print-path|--print-ref]

  --print-path  clone/build as needed, then print the executable path
  --print-ref   print the immutable first-install commit without changing files
EOF
}

action="${1:---print-path}"
[ $# -eq 0 ] || shift
case "$action" in
  --print-path) ;;
  --print-ref)
    [ $# -eq 0 ] || { usage >&2; exit 2; }
    printf '%s\n' \
      "${KILIX_LAND_DESKTOP_REF:-$KILIX_LAND_DESKTOP_DEFAULT_REF}"
    exit 0 ;;
  -h|--help)
    usage
    exit 0 ;;
  *)
    usage >&2
    exit 2 ;;
esac
[ $# -eq 0 ] || { usage >&2; exit 2; }
[ "$(id -u)" -ne 0 ] \
  || die "run this installer as the desktop user, not root"

case "$KILIX_LAND_DESKTOP_DIR" in
  /*) ;;
  *) die "KILIX_LAND_DESKTOP_DIR must be a normalized absolute path: $KILIX_LAND_DESKTOP_DIR" ;;
esac
land_dir="$(realpath -m -- "$KILIX_LAND_DESKTOP_DIR" 2>/dev/null)" \
  || die "could not normalize KILIX_LAND_DESKTOP_DIR=$KILIX_LAND_DESKTOP_DIR"
[ "$land_dir" = "$KILIX_LAND_DESKTOP_DIR" ] \
  || die "KILIX_LAND_DESKTOP_DIR must be normalized and contain no symlink components: $KILIX_LAND_DESKTOP_DIR"
case "$land_dir" in
  /|"$HOME"|"$GPU_TERMINAL_SOURCE_HOME")
    die "refusing broad Kilix Land checkout path: $land_dir" ;;
esac

for command in git make cc; do
  command -v "$command" >/dev/null 2>&1 \
    || die "$command is required (install git, make, a C compiler, and zlib development headers)"
done

explicit_ref="${KILIX_LAND_DESKTOP_REF:-}"
install_ref="${explicit_ref:-$KILIX_LAND_DESKTOP_DEFAULT_REF}"
if ! [[ "$install_ref" =~ ^[0-9a-fA-F]{40}$ ]] \
     && [ "$KILIX_LAND_DESKTOP_ALLOW_MUTABLE_REF" != 1 ]; then
  die "KILIX_LAND_DESKTOP_REF must be a full 40-character commit SHA (set KILIX_LAND_DESKTOP_ALLOW_MUTABLE_REF=1 only to trust a mutable tag/branch)"
fi

initialize_missing_submodules() {
  local directory="$1" status
  [ -f "$directory/.gitmodules" ] || return 0
  [ ! -L "$directory/.gitmodules" ] \
    || die "refusing unsafe submodule manifest: $directory/.gitmodules"
  status="$(git -C "$directory" submodule status --recursive 2>/dev/null)" \
    || die "could not inspect Kilix Land submodules"
  if printf '%s\n' "$status" | grep -q '^-'; then
    log "initializing pinned submodules"
    git -C "$directory" submodule update --init --recursive >&2 \
      || die "could not initialize Kilix Land submodules"
  fi
}

build_checkout() {
  local directory="$1" binary
  binary="$directory/kilix-land-desktop"
  if [ ! -f "$directory/Makefile" ] || [ -L "$directory/Makefile" ]; then
    die "missing or unsafe Kilix Land Makefile: $directory/Makefile"
  fi
  initialize_missing_submodules "$directory"
  log "building $directory"
  make --no-print-directory -C "$directory" >&2 \
    || die "build failed (install a C toolchain and zlib development headers)"
  if [ ! -f "$binary" ] || [ -L "$binary" ] || [ ! -x "$binary" ]; then
    die "build did not produce a regular executable: $binary"
  fi
}

checkout_ref() {
  local directory="$1" ref="$2" require_clean="${3:-1}" target
  if [ "$require_clean" = 1 ] \
       && [ -n "$(git -C "$directory" status --porcelain --untracked-files=normal 2>/dev/null)" ]; then
    die "ref checkout refused because $directory has local modifications"
  fi
  git -C "$directory" fetch --no-tags origin "$ref" >&2 \
    || die "could not fetch KILIX_LAND_DESKTOP_REF=$ref"
  target="$(git -C "$directory" rev-parse --verify 'FETCH_HEAD^{commit}' 2>/dev/null)" \
    || die "KILIX_LAND_DESKTOP_REF did not resolve to a commit"
  git -C "$directory" checkout --detach "$target" >&2 \
    || die "could not check out KILIX_LAND_DESKTOP_REF=$ref"
  [ "$(git -C "$directory" rev-parse --verify HEAD 2>/dev/null)" = "$target" ] \
    || die "Kilix Land checkout verification failed"
  git -C "$directory" submodule update --init --recursive >&2 \
    || die "could not reconcile Kilix Land submodules"
}

if git -C "$land_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  checkout_root="$(git -C "$land_dir" rev-parse --show-toplevel 2>/dev/null)" \
    || die "could not resolve the Kilix Land checkout root"
  checkout_root="$(realpath -m -- "$checkout_root")"
  [ "$checkout_root" = "$land_dir" ] \
    || die "$land_dir is nested inside a different Git checkout: $checkout_root"
  origin="$(git -C "$land_dir" remote get-url origin 2>/dev/null || true)"
  if [ "$origin" != "$KILIX_LAND_DESKTOP_REPO" ] \
       && [ "$KILIX_LAND_DESKTOP_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$land_dir has origin '${origin:-missing}', expected '$KILIX_LAND_DESKTOP_REPO' (set KILIX_LAND_DESKTOP_TRUST_EXISTING_CHECKOUT=1 only for a trusted checkout)"
  fi
  if [ -n "$explicit_ref" ]; then
    checkout_ref "$land_dir" "$explicit_ref"
  else
    log "using existing checkout at $(git -C "$land_dir" rev-parse --short HEAD)"
  fi
  build_checkout "$land_dir"
  printf '%s\n' "$land_dir/kilix-land-desktop"
  exit 0
fi

if [ -e "$land_dir" ] || [ -L "$land_dir" ]; then
  if [ "$KILIX_LAND_DESKTOP_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$land_dir exists but is not a Git checkout"
  fi
  log "using trusted packaged source at $land_dir"
  build_checkout "$land_dir"
  printf '%s\n' "$land_dir/kilix-land-desktop"
  exit 0
fi

case "$KILIX_LAND_DESKTOP_AUTO_INSTALL" in
  1|yes|true|on) ;;
  *) die "Kilix Land is not installed at $land_dir; set KILIX_LAND_DESKTOP_AUTO_INSTALL=1 to download it" ;;
esac

parent="$(dirname "$land_dir")"
mkdir -p -- "$parent" || die "could not create checkout parent: $parent"
if [ ! -d "$parent" ] || [ -L "$parent" ]; then
  die "checkout parent must be a real directory: $parent"
fi

clone_tmp="$(mktemp -d "$parent/.kilix-land-desktop.clone.XXXXXX")" \
  || die "could not allocate a temporary clone directory"
cleanup() {
  [ -z "${clone_tmp:-}" ] || rm -rf -- "$clone_tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

checkout="$clone_tmp/checkout"
log "downloading pinned Kilix Land $install_ref -> $land_dir"
git clone --no-checkout -- "$KILIX_LAND_DESKTOP_REPO" "$checkout" >&2 \
  || die "git clone failed ($KILIX_LAND_DESKTOP_REPO)"
checkout_ref "$checkout" "$install_ref" 0
build_checkout "$checkout"
if [ -e "$land_dir" ] || [ -L "$land_dir" ]; then
  die "checkout path appeared while Kilix Land was being prepared: $land_dir"
fi
mv -- "$checkout" "$land_dir" \
  || die "could not publish the prepared checkout"
rm -rf -- "$clone_tmp"
clone_tmp=""

printf '%s\n' "$land_dir/kilix-land-desktop"
