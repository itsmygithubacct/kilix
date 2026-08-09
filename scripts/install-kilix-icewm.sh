#!/usr/bin/env bash
# Prepare the exact Kilix IceWM desktop selected by this Kilix checkout.
#
# Two stages, both lazy: fetch/verify the kilix-icewm checkout, then let it
# build the IceWM it pins. Neither happens until this desktop is selected.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
KILIX_ICEWM_DIR="${KILIX_ICEWM_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-desktops/kilix-icewm}"
KILIX_ICEWM_REPO="${KILIX_ICEWM_REPO:-https://github.com/itsmygithubacct/kilix-icewm.git}"
KILIX_ICEWM_AUTO_INSTALL="${KILIX_ICEWM_AUTO_INSTALL:-1}"
KILIX_ICEWM_TRUST_EXISTING_CHECKOUT="${KILIX_ICEWM_TRUST_EXISTING_CHECKOUT:-0}"
KILIX_ICEWM_ALLOW_MUTABLE_REF="${KILIX_ICEWM_ALLOW_MUTABLE_REF:-0}"

# This full commit is part of Kilix's transitive source closure. An existing
# sibling checkout stays a development checkout unless KILIX_ICEWM_REF is set;
# a first-use download always resolves this immutable default.
KILIX_ICEWM_DEFAULT_REF=REPLACE_WITH_PUBLISHED_COMMIT

die() { printf 'kilix icewm: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix icewm: %s\n' "$*" >&2; }

print_path_only=0
for arg in "$@"; do
  case "$arg" in
    --print-path) print_path_only=1 ;;
    -h|--help) echo "usage: install-kilix-icewm.sh [--print-path]"; exit 0 ;;
    *) die "unknown argument: $arg" ;;
  esac
done

entry() { printf '%s/bin/kilix-icewm' "$KILIX_ICEWM_DIR"; }

ref_is_immutable() {
  # A 40-hex object name pins one tree; a branch name does not.
  [[ "$1" =~ ^[0-9a-f]{40}$ ]]
}

ensure_checkout() {
  local want="${KILIX_ICEWM_REF:-$KILIX_ICEWM_DEFAULT_REF}"
  if [ "$want" = "REPLACE_WITH_PUBLISHED_COMMIT" ]; then
    # Until kilix-icewm is published, only a local sibling checkout can supply
    # it. Failing loudly beats cloning from a URL that does not exist yet.
    [ -d "$KILIX_ICEWM_DIR" ] \
      || die "kilix-icewm is not published yet and no checkout exists at $KILIX_ICEWM_DIR
Set KILIX_ICEWM_DIR to a local checkout, or publish the repository and pin its
commit as KILIX_ICEWM_DEFAULT_REF in this installer."
    return 0
  fi
  if ! ref_is_immutable "$want" && [ "$KILIX_ICEWM_ALLOW_MUTABLE_REF" != 1 ]; then
    die "KILIX_ICEWM_REF must be a full 40-character commit (got '$want'); \
set KILIX_ICEWM_ALLOW_MUTABLE_REF=1 to override"
  fi
  if [ -d "$KILIX_ICEWM_DIR/.git" ]; then
    if [ "$KILIX_ICEWM_TRUST_EXISTING_CHECKOUT" = 1 ] || [ -n "${KILIX_ICEWM_REF:-}" ]; then
      return 0
    fi
    return 0
  fi
  [ "$KILIX_ICEWM_AUTO_INSTALL" = 1 ] \
    || die "Kilix IceWM is not installed and auto-install is disabled"
  log "cloning kilix-icewm -> $KILIX_ICEWM_DIR"
  mkdir -p "$(dirname "$KILIX_ICEWM_DIR")"
  git clone "$KILIX_ICEWM_REPO" "$KILIX_ICEWM_DIR" \
    || die "git clone failed ($KILIX_ICEWM_REPO)"
  git -C "$KILIX_ICEWM_DIR" checkout --quiet --detach "$want" \
    || die "could not check out pinned commit $want"
}

ensure_checkout
[ -f "$(entry)" ] || die "missing bin/kilix-icewm in $KILIX_ICEWM_DIR"
[ ! -L "$(entry)" ] || die "refusing a symlinked provider entry point"
chmod u+x "$(entry)" 2>/dev/null || true

# Stage two: the desktop builds the IceWM it pins, on first use only.
builder="$KILIX_ICEWM_DIR/scripts/build-icewm.sh"
if [ -x "$builder" ] && [ ! -L "$builder" ]; then
  "$builder" --print-path >/dev/null \
    || die "could not prepare IceWM (see the build output above)"
fi

if [ "$print_path_only" = 1 ]; then
  entry
  exit 0
fi
log "ready: $(entry)"
