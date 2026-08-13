#!/usr/bin/env bash
# Prepare the exact Kilix IceWM desktop selected by this Kilix checkout.
# The provider checkout is pinned here; its own lazy builder owns the IceWM pin.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
KILIX_ICEWM_DIR="${KILIX_ICEWM_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-icewm}"
if [ "$KILIX_ICEWM_DIR" = "$GPU_TERMINAL_SOURCE_HOME/kilix-icewm" ] \
   && [ ! -e "$KILIX_ICEWM_DIR" ] && [ ! -L "$KILIX_ICEWM_DIR" ]; then
  KILIX_ICEWM_DIR="$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-icewm"
fi
KILIX_ICEWM_REPO="${KILIX_ICEWM_REPO:-https://github.com/itsmygithubacct/kilix-icewm.git}"
KILIX_ICEWM_AUTO_INSTALL="${KILIX_ICEWM_AUTO_INSTALL:-1}"
KILIX_ICEWM_TRUST_EXISTING_CHECKOUT="${KILIX_ICEWM_TRUST_EXISTING_CHECKOUT:-0}"
KILIX_ICEWM_KEEP_EXISTING_CHECKOUT="${KILIX_ICEWM_KEEP_EXISTING_CHECKOUT:-0}"
KILIX_ICEWM_ALLOW_MUTABLE_REF="${KILIX_ICEWM_ALLOW_MUTABLE_REF:-0}"

# Every run resolves this commit, for a first-use clone and an existing checkout
# alike. The provider lazily reconciles and builds its own pinned IceWM source.
KILIX_ICEWM_DEFAULT_REF=0b9f11b45fddc5370c37b00e9cd9e42ac5a5f6d7

die() { printf 'kilix icewm: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix icewm: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-kilix-icewm.sh [--print-path|--print-ref]

  --print-path  clone/prepare as needed, then print the provider path
  --print-ref   print the immutable pinned provider commit without changing files

Environment:
  KILIX_ICEWM_REF                    prepare this commit instead of the pin
  KILIX_ICEWM_KEEP_EXISTING_CHECKOUT=1
                                     work from an existing checkout as it is;
                                     the resolved ref is not installed
EOF
}

action="${1:---print-path}"
[ $# -eq 0 ] || shift
case "$action" in
  --print-path) ;;
  --print-ref)
    [ $# -eq 0 ] || { usage >&2; exit 2; }
    printf '%s\n' "${KILIX_ICEWM_REF:-$KILIX_ICEWM_DEFAULT_REF}"
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

case "$KILIX_ICEWM_DIR" in
  /*) ;;
  *) die "KILIX_ICEWM_DIR must be a normalized absolute path: $KILIX_ICEWM_DIR" ;;
esac
icewm_dir="$(realpath -m -- "$KILIX_ICEWM_DIR" 2>/dev/null)" \
  || die "could not normalize KILIX_ICEWM_DIR=$KILIX_ICEWM_DIR"
[ "$icewm_dir" = "$KILIX_ICEWM_DIR" ] \
  || die "KILIX_ICEWM_DIR must be normalized and contain no symlink components: $KILIX_ICEWM_DIR"
case "$icewm_dir" in
  /|"$HOME"|"$GPU_TERMINAL_SOURCE_HOME")
    die "refusing broad Kilix IceWM checkout path: $icewm_dir" ;;
esac

command -v git >/dev/null 2>&1 || die "git is required"

explicit_ref="${KILIX_ICEWM_REF:-}"
install_ref="${explicit_ref:-$KILIX_ICEWM_DEFAULT_REF}"
if ! [[ "$install_ref" =~ ^[0-9a-fA-F]{40}$ ]] \
     && [ "$KILIX_ICEWM_ALLOW_MUTABLE_REF" != 1 ]; then
  die "KILIX_ICEWM_REF must be a full 40-character commit SHA (set KILIX_ICEWM_ALLOW_MUTABLE_REF=1 only to trust a mutable tag/branch)"
fi

build_checkout() {
  local directory="$1" builder entry
  builder="$directory/scripts/build-icewm.sh"
  entry="$directory/bin/kilix-icewm"
  if [ ! -f "$builder" ] || [ -L "$builder" ]; then
    die "missing or unsafe IceWM builder: $builder"
  fi
  log "preparing $directory"
  KILIX_HOME="$KILIX_HOME" bash "$builder" --print-path >/dev/null \
    || die "could not prepare IceWM (see the build output above)"
  if [ ! -f "$entry" ] || [ -L "$entry" ] || [ ! -x "$entry" ]; then
    die "provider did not supply a regular executable: $entry"
  fi
}

checkout_ref() {
  local directory="$1" ref="$2" require_clean="${3:-1}" target
  if [ "$require_clean" = 1 ] \
       && [ -n "$(git -C "$directory" status --porcelain \
                       --untracked-files=normal 2>/dev/null)" ]; then
    die "ref checkout refused because $directory has local modifications"
  fi
  git -C "$directory" fetch --no-tags origin "$ref" >&2 \
    || die "could not fetch KILIX_ICEWM_REF=$ref"
  target="$(git -C "$directory" rev-parse --verify \
              'FETCH_HEAD^{commit}' 2>/dev/null)" \
    || die "KILIX_ICEWM_REF did not resolve to a commit"
  git -C "$directory" checkout --detach "$target" >&2 \
    || die "could not check out KILIX_ICEWM_REF=$ref"
  [ "$(git -C "$directory" rev-parse --verify HEAD 2>/dev/null)" = "$target" ] \
    || die "Kilix IceWM checkout verification failed"
}

advance_existing_checkout() {
  local directory="$1" head
  head="$(git -C "$directory" rev-parse HEAD 2>/dev/null || true)"
  case "$KILIX_ICEWM_KEEP_EXISTING_CHECKOUT" in
    1|yes|true|on)
      log "keeping the existing checkout at ${head:0:12} as asked (KILIX_ICEWM_KEEP_EXISTING_CHECKOUT=1)"
      log "the resolved ref ${install_ref:0:12} was NOT installed"
      return 0 ;;
  esac
  if [ "${head,,}" = "${install_ref,,}" ]; then
    log "existing checkout is already at the resolved ref ${head:0:12}"
    return 0
  fi
  if [ -n "$(git -C "$directory" status --porcelain \
                  --untracked-files=normal 2>/dev/null)" ]; then
    log "keeping the existing checkout at ${head:0:12}: it has local modifications"
    log "the resolved ref ${install_ref:0:12} was NOT installed; commit, stash or remove them"
    return 0
  fi
  checkout_ref "$directory" "$install_ref"
  if [ -n "$head" ] \
       && git -C "$directory" merge-base --is-ancestor \
            "$install_ref" "$head" >/dev/null 2>&1; then
    log "existing checkout REWOUND ${head:0:12} -> ${install_ref:0:12} (the pinned ref is older)"
  else
    log "existing checkout advanced ${head:0:12} -> ${install_ref:0:12}"
  fi
}

if git -C "$icewm_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  checkout_root="$(git -C "$icewm_dir" rev-parse --show-toplevel 2>/dev/null)" \
    || die "could not resolve the Kilix IceWM checkout root"
  checkout_root="$(realpath -m -- "$checkout_root")"
  [ "$checkout_root" = "$icewm_dir" ] \
    || die "$icewm_dir is nested inside a different Git checkout: $checkout_root"
  origin="$(git -C "$icewm_dir" remote get-url origin 2>/dev/null || true)"
  if [ "$origin" != "$KILIX_ICEWM_REPO" ] \
       && [ "$KILIX_ICEWM_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$icewm_dir has origin '${origin:-missing}', expected '$KILIX_ICEWM_REPO' (set KILIX_ICEWM_TRUST_EXISTING_CHECKOUT=1 only for a trusted checkout)"
  fi
  advance_existing_checkout "$icewm_dir"
  build_checkout "$icewm_dir"
  printf '%s\n' "$icewm_dir/bin/kilix-icewm"
  exit 0
fi

if [ -e "$icewm_dir" ] || [ -L "$icewm_dir" ]; then
  if [ "$KILIX_ICEWM_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$icewm_dir exists but is not a Git checkout"
  fi
  log "using trusted packaged source at $icewm_dir"
  build_checkout "$icewm_dir"
  printf '%s\n' "$icewm_dir/bin/kilix-icewm"
  exit 0
fi

case "$KILIX_ICEWM_AUTO_INSTALL" in
  1|yes|true|on) ;;
  *) die "Kilix IceWM is not installed at $icewm_dir; set KILIX_ICEWM_AUTO_INSTALL=1 to download it" ;;
esac

parent="$(dirname "$icewm_dir")"
mkdir -p -- "$parent" || die "could not create checkout parent: $parent"
if [ ! -d "$parent" ] || [ -L "$parent" ]; then
  die "checkout parent must be a real directory: $parent"
fi

clone_tmp="$(mktemp -d "$parent/.kilix-icewm.clone.XXXXXX")" \
  || die "could not allocate a temporary clone directory"
cleanup() {
  [ -z "${clone_tmp:-}" ] || rm -rf -- "$clone_tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

checkout="$clone_tmp/checkout"
log "downloading pinned Kilix IceWM $install_ref -> $icewm_dir"
git clone --no-checkout -- "$KILIX_ICEWM_REPO" "$checkout" >&2 \
  || die "git clone failed ($KILIX_ICEWM_REPO)"
checkout_ref "$checkout" "$install_ref" 0
build_checkout "$checkout"
if [ -e "$icewm_dir" ] || [ -L "$icewm_dir" ]; then
  die "checkout path appeared while Kilix IceWM was being prepared: $icewm_dir"
fi
mv -- "$checkout" "$icewm_dir" || die "could not publish the prepared checkout"
rm -rf -- "$clone_tmp"
clone_tmp=""

printf '%s\n' "$icewm_dir/bin/kilix-icewm"
