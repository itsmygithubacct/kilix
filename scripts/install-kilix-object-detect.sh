#!/usr/bin/env bash
# Prepare the exact kilix-look image and video analyzer selected by this Kilix
# checkout.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
KILIX_LOOK_DIR="${KILIX_LOOK_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-modules/kilix-object-detect}"
KILIX_LOOK_REPO="${KILIX_LOOK_REPO:-https://github.com/itsmygithubacct/kilix-object-detect.git}"
KILIX_LOOK_AUTO_INSTALL="${KILIX_LOOK_AUTO_INSTALL:-1}"
KILIX_LOOK_TRUST_EXISTING_CHECKOUT="${KILIX_LOOK_TRUST_EXISTING_CHECKOUT:-0}"
KILIX_LOOK_KEEP_EXISTING_CHECKOUT="${KILIX_LOOK_KEEP_EXISTING_CHECKOUT:-0}"
KILIX_LOOK_ALLOW_MUTABLE_REF="${KILIX_LOOK_ALLOW_MUTABLE_REF:-0}"

# This full commit is part of Kilix's transitive source closure. Every run
# resolves it — a first-use download and an existing checkout alike — so a moved
# pin reaches machines that already have the component. Set
# KILIX_LOOK_KEEP_EXISTING_CHECKOUT=1 to work from a checkout as it is.
KILIX_LOOK_DEFAULT_REF=ba773db8e8b05d283f1efae909ba3b5ac4044269

die() { printf 'kilix look: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix look: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-kilix-object-detect.sh [--print-path|--print-ref]

  --print-path  clone/build as needed, then print the executable path
  --print-ref   print the immutable pinned commit without changing files

Environment:
  KILIX_LOOK_REF                    build this commit instead of the pin
  KILIX_LOOK_KEEP_EXISTING_CHECKOUT=1
                                   work from an existing checkout as it is; the
                                   resolved ref is not installed
EOF
}

action="${1:---print-path}"
[ $# -eq 0 ] || shift
case "$action" in
  --print-path) ;;
  --print-ref)
    [ $# -eq 0 ] || { usage >&2; exit 2; }
    printf '%s\n' "${KILIX_LOOK_REF:-$KILIX_LOOK_DEFAULT_REF}"
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

case "$KILIX_LOOK_DIR" in
  /*) ;;
  *) die "KILIX_LOOK_DIR must be a normalized absolute path: $KILIX_LOOK_DIR" ;;
esac
look_dir="$(realpath -m -- "$KILIX_LOOK_DIR" 2>/dev/null)" \
  || die "could not normalize KILIX_LOOK_DIR=$KILIX_LOOK_DIR"
[ "$look_dir" = "$KILIX_LOOK_DIR" ] \
  || die "KILIX_LOOK_DIR must be normalized and contain no symlink components: $KILIX_LOOK_DIR"
case "$look_dir" in
  /|"$HOME"|"$GPU_TERMINAL_SOURCE_HOME")
    die "refusing broad kilix-object-detect checkout path: $look_dir" ;;
esac

for command in git make cc; do
  command -v "$command" >/dev/null 2>&1 \
    || die "$command is required (install git, make, a C compiler, and sqlite3 and zlib development headers)"
done

explicit_ref="${KILIX_LOOK_REF:-}"
install_ref="${explicit_ref:-$KILIX_LOOK_DEFAULT_REF}"
if ! [[ "$install_ref" =~ ^[0-9a-fA-F]{40}$ ]] \
     && [ "$KILIX_LOOK_ALLOW_MUTABLE_REF" != 1 ]; then
  die "KILIX_LOOK_REF must be a full 40-character commit SHA (set KILIX_LOOK_ALLOW_MUTABLE_REF=1 only to trust a mutable tag/branch)"
fi

build_checkout() {
  local directory="$1" binary
  binary="$directory/build/kilix-look"
  if [ ! -f "$directory/Makefile" ] || [ -L "$directory/Makefile" ]; then
    die "missing or unsafe kilix-object-detect Makefile: $directory/Makefile"
  fi
  # kilix-object-detect is a module over pinned modules - kilix-rtsp for
  # decoding, kilix-motion-detect for the gate, kilix-sound-detect for
  # --listen - and reaches the terminal stack through kilix-rtsp's own
  # closure. A plain clone leaves all of that empty and the build fails on a
  # missing source, so the closure is resolved before anything is compiled.
  # A trusted packaged tree is not a Git checkout and must already carry it.
  if git -C "$directory" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$directory" submodule update --init --recursive >&2 \
      || die "could not prepare the pinned kilix-object-detect module closure"
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
    || die "could not fetch KILIX_LOOK_REF=$ref"
  target="$(git -C "$directory" rev-parse --verify 'FETCH_HEAD^{commit}' 2>/dev/null)" \
    || die "KILIX_LOOK_REF did not resolve to a commit"
  git -C "$directory" checkout --detach "$target" >&2 \
    || die "could not check out KILIX_LOOK_REF=$ref"
  [ "$(git -C "$directory" rev-parse --verify HEAD 2>/dev/null)" = "$target" ] \
    || die "kilix-object-detect checkout verification failed"
}

# An existing checkout is not exempt from the pin.
#
# Reinstalling from whatever a checkout happened to hold whenever no ref was
# given explicitly meant a moved default reached every fresh install and no
# update. The resolved ref is the one a first-use download would land on and it
# passed the immutable-SHA check above, so both paths now agree.
advance_existing_checkout() {
  local directory="$1" head
  head="$(git -C "$directory" rev-parse HEAD 2>/dev/null || true)"
  case "$KILIX_LOOK_KEEP_EXISTING_CHECKOUT" in
    1|yes|true|on)
      log "keeping the existing checkout at ${head:0:12} as asked (KILIX_LOOK_KEEP_EXISTING_CHECKOUT=1)"
      log "the resolved ref ${install_ref:0:12} was NOT installed"
      return 0 ;;
  esac
  if [ "${head,,}" = "${install_ref,,}" ]; then
    log "existing checkout is already at the resolved ref ${head:0:12}"
    return 0
  fi
  if [ -n "$(git -C "$directory" status --porcelain --untracked-files=normal 2>/dev/null)" ]; then
    # A tree someone is working in is kept, loudly. Refusing outright would
    # turn one uncommitted file into a failed stack update; moving it silently
    # would throw the work away.
    log "keeping the existing checkout at ${head:0:12}: it has local modifications"
    log "the resolved ref ${install_ref:0:12} was NOT installed; commit, stash or remove them"
    return 0
  fi
  checkout_ref "$directory" "$install_ref"
  # Said after the checkout, because only then are both commits local enough to
  # compare. A pin that walks a machine backwards is legitimate and must still
  # be visible in the log.
  if [ -n "$head" ] \
       && git -C "$directory" merge-base --is-ancestor \
            "$install_ref" "$head" >/dev/null 2>&1; then
    log "existing checkout REWOUND ${head:0:12} -> ${install_ref:0:12} (the pinned ref is older)"
  else
    log "existing checkout advanced ${head:0:12} -> ${install_ref:0:12}"
  fi
}

if git -C "$look_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  checkout_root="$(git -C "$look_dir" rev-parse --show-toplevel 2>/dev/null)" \
    || die "could not resolve the kilix-object-detect checkout root"
  checkout_root="$(realpath -m -- "$checkout_root")"
  [ "$checkout_root" = "$look_dir" ] \
    || die "$look_dir is nested inside a different Git checkout: $checkout_root"
  origin="$(git -C "$look_dir" remote get-url origin 2>/dev/null || true)"
  if [ "$origin" != "$KILIX_LOOK_REPO" ] \
       && [ "$KILIX_LOOK_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$look_dir has origin '${origin:-missing}', expected '$KILIX_LOOK_REPO' (set KILIX_LOOK_TRUST_EXISTING_CHECKOUT=1 only for a trusted checkout)"
  fi
  advance_existing_checkout "$look_dir"
  build_checkout "$look_dir"
  printf '%s\n' "$look_dir/build/kilix-look"
  exit 0
fi

if [ -e "$look_dir" ] || [ -L "$look_dir" ]; then
  if [ "$KILIX_LOOK_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$look_dir exists but is not a Git checkout"
  fi
  log "using trusted packaged source at $look_dir"
  build_checkout "$look_dir"
  printf '%s\n' "$look_dir/build/kilix-look"
  exit 0
fi

case "$KILIX_LOOK_AUTO_INSTALL" in
  1|yes|true|on) ;;
  *) die "kilix-object-detect is not installed at $look_dir; set KILIX_LOOK_AUTO_INSTALL=1 to download it" ;;
esac

parent="$(dirname "$look_dir")"
mkdir -p -- "$parent" || die "could not create checkout parent: $parent"
if [ ! -d "$parent" ] || [ -L "$parent" ]; then
  die "checkout parent must be a real directory: $parent"
fi

clone_tmp="$(mktemp -d "$parent/.kilix-object-detect.clone.XXXXXX")" \
  || die "could not allocate a temporary clone directory"
cleanup() {
  [ -z "${clone_tmp:-}" ] || rm -rf -- "$clone_tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

checkout="$clone_tmp/checkout"
log "downloading pinned kilix-object-detect $install_ref -> $look_dir"
git clone --no-checkout -- "$KILIX_LOOK_REPO" "$checkout" >&2 \
  || die "git clone failed ($KILIX_LOOK_REPO)"
checkout_ref "$checkout" "$install_ref" 0
build_checkout "$checkout"
if [ -e "$look_dir" ] || [ -L "$look_dir" ]; then
  die "checkout path appeared while kilix-object-detect was being prepared: $look_dir"
fi
mv -- "$checkout" "$look_dir" || die "could not publish the prepared checkout"
rm -rf -- "$clone_tmp"
clone_tmp=""

printf '%s\n' "$look_dir/build/kilix-look"
