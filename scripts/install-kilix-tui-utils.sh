#!/usr/bin/env bash
# Prepare the exact kilix-tui-utils checkout selected by this Kilix checkout,
# install its commands, and print the desktop entry point.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
KILIX_TUI_UTILS_DIR="${KILIX_TUI_UTILS_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-tui-utils}"
if [ "$KILIX_TUI_UTILS_DIR" = "$GPU_TERMINAL_SOURCE_HOME/kilix-tui-utils" ] \
   && [ ! -e "$KILIX_TUI_UTILS_DIR" ] && [ ! -L "$KILIX_TUI_UTILS_DIR" ]; then
  KILIX_TUI_UTILS_DIR="$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-tui-utils"
fi
KILIX_TUI_UTILS_REPO="${KILIX_TUI_UTILS_REPO:-https://github.com/itsmygithubacct/kilix-tui-utils.git}"
KILIX_TUI_UTILS_AUTO_INSTALL="${KILIX_TUI_UTILS_AUTO_INSTALL:-1}"
KILIX_TUI_UTILS_TRUST_EXISTING_CHECKOUT="${KILIX_TUI_UTILS_TRUST_EXISTING_CHECKOUT:-0}"
KILIX_TUI_UTILS_KEEP_EXISTING_CHECKOUT="${KILIX_TUI_UTILS_KEEP_EXISTING_CHECKOUT:-0}"
KILIX_TUI_UTILS_ALLOW_MUTABLE_REF="${KILIX_TUI_UTILS_ALLOW_MUTABLE_REF:-0}"
KILIX_TUI_UTILS_PREFIX="${KILIX_TUI_UTILS_PREFIX:-$HOME/.local}"

# This full commit is part of Kilix's transitive source closure. Every run
# resolves it — a first-use download and an existing checkout alike — so a moved
# pin reaches machines that already have the component. Set
# KILIX_TUI_UTILS_KEEP_EXISTING_CHECKOUT=1 to work from a checkout as it is.
KILIX_TUI_UTILS_DEFAULT_REF=88216b449838cfd0778b5a87f9535c619e7c1f46

die() { printf 'kilix tui: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix tui: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-kilix-tui-utils.sh [--print-path|--print-ref]

  --print-path  clone/install as needed, then print the desktop entry point
  --print-ref   print the immutable pinned commit without changing files

Environment:
  KILIX_TUI_UTILS_REF                    install this commit instead of the pin
  KILIX_TUI_UTILS_KEEP_EXISTING_CHECKOUT=1
                                         work from an existing checkout as it
                                         is; the resolved ref is not installed
EOF
}

action="${1:---print-path}"
[ $# -eq 0 ] || shift
case "$action" in
  --print-path) ;;
  --print-ref)
    [ $# -eq 0 ] || { usage >&2; exit 2; }
    printf '%s\n' "${KILIX_TUI_UTILS_REF:-$KILIX_TUI_UTILS_DEFAULT_REF}"
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

case "$KILIX_TUI_UTILS_DIR" in
  /*) ;;
  *) die "KILIX_TUI_UTILS_DIR must be a normalized absolute path: $KILIX_TUI_UTILS_DIR" ;;
esac
tui_dir="$(realpath -m -- "$KILIX_TUI_UTILS_DIR" 2>/dev/null)" \
  || die "could not normalize KILIX_TUI_UTILS_DIR=$KILIX_TUI_UTILS_DIR"
[ "$tui_dir" = "$KILIX_TUI_UTILS_DIR" ] \
  || die "KILIX_TUI_UTILS_DIR must be normalized and contain no symlink components: $KILIX_TUI_UTILS_DIR"
case "$tui_dir" in
  /|"$HOME"|"$GPU_TERMINAL_SOURCE_HOME")
    die "refusing broad kilix-tui-utils checkout path: $tui_dir" ;;
esac

for command in git python3; do
  command -v "$command" >/dev/null 2>&1 || die "$command is required"
done

explicit_ref="${KILIX_TUI_UTILS_REF:-}"
install_ref="${explicit_ref:-$KILIX_TUI_UTILS_DEFAULT_REF}"
if ! [[ "$install_ref" =~ ^[0-9a-fA-F]{40}$ ]] \
     && [ "$KILIX_TUI_UTILS_ALLOW_MUTABLE_REF" != 1 ]; then
  die "KILIX_TUI_UTILS_REF must be a full 40-character commit SHA (set KILIX_TUI_UTILS_ALLOW_MUTABLE_REF=1 only to trust a mutable tag/branch)"
fi

install_checkout() {
  local directory="$1" installer launcher
  installer="$directory/install.sh"
  if [ ! -f "$installer" ] || [ -L "$installer" ]; then
    die "missing or unsafe kilix-tui-utils installer: $installer"
  fi
  log "installing commands from $directory"
  KILIX_TUI_UTILS_PREFIX="$KILIX_TUI_UTILS_PREFIX" bash "$installer" >&2 \
    || die "install.sh failed"
  launcher="$KILIX_TUI_UTILS_PREFIX/bin/kilix-tui"
  if [ ! -f "$launcher" ] || [ -L "$launcher" ] || [ ! -x "$launcher" ]; then
    die "install did not produce a regular executable: $launcher"
  fi
}

checkout_ref() {
  local directory="$1" ref="$2" require_clean="${3:-1}" target
  if [ "$require_clean" = 1 ] \
       && [ -n "$(git -C "$directory" status --porcelain --untracked-files=normal 2>/dev/null)" ]; then
    die "ref checkout refused because $directory has local modifications"
  fi
  git -C "$directory" fetch --no-tags origin "$ref" >&2 \
    || die "could not fetch KILIX_TUI_UTILS_REF=$ref"
  target="$(git -C "$directory" rev-parse --verify 'FETCH_HEAD^{commit}' 2>/dev/null)" \
    || die "KILIX_TUI_UTILS_REF did not resolve to a commit"
  git -C "$directory" checkout --detach "$target" >&2 \
    || die "could not check out KILIX_TUI_UTILS_REF=$ref"
  [ "$(git -C "$directory" rev-parse --verify HEAD 2>/dev/null)" = "$target" ] \
    || die "kilix-tui-utils checkout verification failed"
}

# An existing checkout is not exempt from the pin.
#
# This used to reinstall from whatever the checkout happened to hold whenever no
# ref was given explicitly, which meant a moved default reached every fresh
# install and no update: `kilix tui: using existing checkout at 372559f`, then
# an install from that stale tree. The resolved ref is the same one a first-use
# download would land on, and it passed the immutable-SHA check above, so the
# only difference between the two paths now is the cost of getting there.
advance_existing_checkout() {
  local directory="$1" head
  head="$(git -C "$directory" rev-parse HEAD 2>/dev/null || true)"
  case "$KILIX_TUI_UTILS_KEEP_EXISTING_CHECKOUT" in
    1|yes|true|on)
      log "keeping the existing checkout at ${head:0:12} as asked (KILIX_TUI_UTILS_KEEP_EXISTING_CHECKOUT=1)"
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

if git -C "$tui_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  checkout_root="$(git -C "$tui_dir" rev-parse --show-toplevel 2>/dev/null)" \
    || die "could not resolve the kilix-tui-utils checkout root"
  checkout_root="$(realpath -m -- "$checkout_root")"
  [ "$checkout_root" = "$tui_dir" ] \
    || die "$tui_dir is nested inside a different Git checkout: $checkout_root"
  origin="$(git -C "$tui_dir" remote get-url origin 2>/dev/null || true)"
  if [ "$origin" != "$KILIX_TUI_UTILS_REPO" ] \
       && [ "$KILIX_TUI_UTILS_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$tui_dir has origin '${origin:-missing}', expected '$KILIX_TUI_UTILS_REPO' (set KILIX_TUI_UTILS_TRUST_EXISTING_CHECKOUT=1 only for a trusted checkout)"
  fi
  advance_existing_checkout "$tui_dir"
  install_checkout "$tui_dir"
  printf '%s\n' "$KILIX_TUI_UTILS_PREFIX/bin/kilix-tui"
  exit 0
fi

if [ -e "$tui_dir" ] || [ -L "$tui_dir" ]; then
  if [ "$KILIX_TUI_UTILS_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$tui_dir exists but is not a Git checkout"
  fi
  log "using trusted packaged source at $tui_dir"
  install_checkout "$tui_dir"
  printf '%s\n' "$KILIX_TUI_UTILS_PREFIX/bin/kilix-tui"
  exit 0
fi

case "$KILIX_TUI_UTILS_AUTO_INSTALL" in
  1|yes|true|on) ;;
  *) die "kilix-tui-utils is not installed at $tui_dir; set KILIX_TUI_UTILS_AUTO_INSTALL=1 to download it" ;;
esac

parent="$(dirname "$tui_dir")"
mkdir -p -- "$parent" || die "could not create checkout parent: $parent"
if [ ! -d "$parent" ] || [ -L "$parent" ]; then
  die "checkout parent must be a real directory: $parent"
fi

clone_tmp="$(mktemp -d "$parent/.kilix-tui-utils.clone.XXXXXX")" \
  || die "could not allocate a temporary clone directory"
cleanup() {
  [ -z "${clone_tmp:-}" ] || rm -rf -- "$clone_tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

checkout="$clone_tmp/checkout"
log "downloading pinned kilix-tui-utils $install_ref -> $tui_dir"
git clone --no-checkout -- "$KILIX_TUI_UTILS_REPO" "$checkout" >&2 \
  || die "git clone failed ($KILIX_TUI_UTILS_REPO)"
checkout_ref "$checkout" "$install_ref" 0
if [ -e "$tui_dir" ] || [ -L "$tui_dir" ]; then
  die "checkout path appeared while kilix-tui-utils was being prepared: $tui_dir"
fi
mv -- "$checkout" "$tui_dir" || die "could not publish the prepared checkout"
rm -rf -- "$clone_tmp"
clone_tmp=""
install_checkout "$tui_dir"

printf '%s\n' "$KILIX_TUI_UTILS_PREFIX/bin/kilix-tui"
