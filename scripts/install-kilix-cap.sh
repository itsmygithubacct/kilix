#!/usr/bin/env bash
# Prepare the exact Kilix Cap desktop selected by this Kilix checkout.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
KILIX_CAP_DIR="${KILIX_CAP_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-cap}"
if [ "$KILIX_CAP_DIR" = "$GPU_TERMINAL_SOURCE_HOME/kilix-cap" ] \
   && [ ! -e "$KILIX_CAP_DIR" ] && [ ! -L "$KILIX_CAP_DIR" ]; then
  KILIX_CAP_DIR="$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-cap"
fi
KILIX_CAP_REPO="${KILIX_CAP_REPO:-https://github.com/itsmygithubacct/kilix-cap.git}"
KILIX_CAP_AUTO_INSTALL="${KILIX_CAP_AUTO_INSTALL:-1}"
KILIX_CAP_TRUST_EXISTING_CHECKOUT="${KILIX_CAP_TRUST_EXISTING_CHECKOUT:-0}"
KILIX_CAP_ALLOW_MUTABLE_REF="${KILIX_CAP_ALLOW_MUTABLE_REF:-0}"

# This full commit is part of Kilix's transitive source closure. An existing
# sibling checkout remains a development checkout unless KILIX_CAP_REF is
# explicitly set; a first-use download always resolves this immutable default.
KILIX_CAP_DEFAULT_REF=19c6f6b6941772d363b43a57b8f82e1c94e865d5

die() { printf 'kilix cap: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix cap: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-kilix-cap.sh [--print-path|--print-ref]

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
    printf '%s\n' "${KILIX_CAP_REF:-$KILIX_CAP_DEFAULT_REF}"
    exit 0 ;;
  -h|--help)
    usage
    exit 0 ;;
  *)
    usage >&2
    exit 2 ;;
esac
[ $# -eq 0 ] || { usage >&2; exit 2; }
[ "$(id -u)" -ne 0 ] || die "run this installer as the desktop user, not root"

case "$KILIX_CAP_DIR" in
  /*) ;;
  *) die "KILIX_CAP_DIR must be a normalized absolute path: $KILIX_CAP_DIR" ;;
esac
cap_dir="$(realpath -m -- "$KILIX_CAP_DIR" 2>/dev/null)" \
  || die "could not normalize KILIX_CAP_DIR=$KILIX_CAP_DIR"
[ "$cap_dir" = "$KILIX_CAP_DIR" ] \
  || die "KILIX_CAP_DIR must be normalized and contain no symlink components: $KILIX_CAP_DIR"
case "$cap_dir" in
  /|"$HOME"|"$GPU_TERMINAL_SOURCE_HOME")
    die "refusing broad Kilix Cap checkout path: $cap_dir" ;;
esac

for command in git make cc; do
  command -v "$command" >/dev/null 2>&1 \
    || die "$command is required (install git, make, a C compiler, and zlib development headers)"
done

explicit_ref="${KILIX_CAP_REF:-}"
install_ref="${explicit_ref:-$KILIX_CAP_DEFAULT_REF}"
if ! [[ "$install_ref" =~ ^[0-9a-fA-F]{40}$ ]] \
     && [ "$KILIX_CAP_ALLOW_MUTABLE_REF" != 1 ]; then
  die "KILIX_CAP_REF must be a full 40-character commit SHA (set KILIX_CAP_ALLOW_MUTABLE_REF=1 only to trust a mutable tag/branch)"
fi

build_checkout() {
  local directory="$1" binary
  binary="$directory/bin/kilix-cap"
  if [ ! -f "$directory/Makefile" ] || [ -L "$directory/Makefile" ]; then
    die "missing or unsafe Kilix Cap Makefile: $directory/Makefile"
  fi
  # Kilix Cap takes its shared stack from a pinned kilix-game-sdk submodule
  # rather than vendored files. A plain clone leaves that directory empty and
  # the build then fails on a missing source, so the closure is resolved here
  # before anything is compiled. A trusted packaged tree is not a Git checkout
  # and must already carry it.
  if git -C "$directory" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$directory" submodule update --init --recursive >&2 \
      || die "could not prepare the pinned Kilix Cap module closure"
  fi
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
    || die "could not fetch KILIX_CAP_REF=$ref"
  target="$(git -C "$directory" rev-parse --verify 'FETCH_HEAD^{commit}' 2>/dev/null)" \
    || die "KILIX_CAP_REF did not resolve to a commit"
  git -C "$directory" checkout --detach "$target" >&2 \
    || die "could not check out KILIX_CAP_REF=$ref"
  [ "$(git -C "$directory" rev-parse --verify HEAD 2>/dev/null)" = "$target" ] \
    || die "Kilix Cap checkout verification failed"
}

if git -C "$cap_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  checkout_root="$(git -C "$cap_dir" rev-parse --show-toplevel 2>/dev/null)" \
    || die "could not resolve the Kilix Cap checkout root"
  checkout_root="$(realpath -m -- "$checkout_root")"
  [ "$checkout_root" = "$cap_dir" ] \
    || die "$cap_dir is nested inside a different Git checkout: $checkout_root"
  origin="$(git -C "$cap_dir" remote get-url origin 2>/dev/null || true)"
  if [ "$origin" != "$KILIX_CAP_REPO" ] \
       && [ "$KILIX_CAP_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$cap_dir has origin '${origin:-missing}', expected '$KILIX_CAP_REPO' (set KILIX_CAP_TRUST_EXISTING_CHECKOUT=1 only for a trusted checkout)"
  fi
  if [ -n "$explicit_ref" ]; then
    checkout_ref "$cap_dir" "$explicit_ref"
  else
    log "using existing checkout at $(git -C "$cap_dir" rev-parse --short HEAD)"
  fi
  build_checkout "$cap_dir"
  printf '%s\n' "$cap_dir/bin/kilix-cap"
  exit 0
fi

if [ -e "$cap_dir" ] || [ -L "$cap_dir" ]; then
  if [ "$KILIX_CAP_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$cap_dir exists but is not a Git checkout"
  fi
  log "using trusted packaged source at $cap_dir"
  build_checkout "$cap_dir"
  printf '%s\n' "$cap_dir/bin/kilix-cap"
  exit 0
fi

case "$KILIX_CAP_AUTO_INSTALL" in
  1|yes|true|on) ;;
  *) die "Kilix Cap is not installed at $cap_dir; set KILIX_CAP_AUTO_INSTALL=1 to download it" ;;
esac

parent="$(dirname "$cap_dir")"
mkdir -p -- "$parent" || die "could not create checkout parent: $parent"
if [ ! -d "$parent" ] || [ -L "$parent" ]; then
  die "checkout parent must be a real directory: $parent"
fi

clone_tmp="$(mktemp -d "$parent/.kilix-cap.clone.XXXXXX")" \
  || die "could not allocate a temporary clone directory"
cleanup() {
  [ -z "${clone_tmp:-}" ] || rm -rf -- "$clone_tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

checkout="$clone_tmp/checkout"
log "downloading pinned Kilix Cap $install_ref -> $cap_dir"
git clone --no-checkout -- "$KILIX_CAP_REPO" "$checkout" >&2 \
  || die "git clone failed ($KILIX_CAP_REPO)"
checkout_ref "$checkout" "$install_ref" 0
build_checkout "$checkout"
if [ -e "$cap_dir" ] || [ -L "$cap_dir" ]; then
  die "checkout path appeared while Kilix Cap was being prepared: $cap_dir"
fi
mv -- "$checkout" "$cap_dir" || die "could not publish the prepared checkout"
rm -rf -- "$clone_tmp"
clone_tmp=""

printf '%s\n' "$cap_dir/bin/kilix-cap"
