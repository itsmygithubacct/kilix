#!/usr/bin/env bash
# Install the exact Kilix Memory source closure selected by this Kilix checkout.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/gpu_terminal}"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_STATE_DIRECTORY="${KILIX_STATE_DIRECTORY:-$KILIX_STORAGE_HOME/state}"
KILIX_MEMORY_PREFIX="${KILIX_MEMORY_PREFIX:-$HOME/.local}"

# These full commits are part of Kilix's source closure. Plebian-OS pins the
# parent Kilix commit, so every network-fetched dashboard input is transitive
# and immutable without adding another independently coordinated release ref.
KILIX_MEMORY_REPO="${KILIX_MEMORY_REPO:-https://github.com/itsmygithubacct/kilix-memory.git}"
KILIX_MEMORY_REF="${KILIX_MEMORY_REF:-579e25d820d27aff33f2a8742ac243f5ba8ba3fa}"
KILIX_MEMORY_PRESENTER_REPO="${KILIX_MEMORY_PRESENTER_REPO:-https://github.com/itsmygithubacct/kitty-frame-presenter.git}"
KILIX_MEMORY_PRESENTER_REF="${KILIX_MEMORY_PRESENTER_REF:-a30bd238f55397a86a404b7d842289ef09c4fb91}"
KILIX_MEMORY_SOFT_RASTER_PY_REPO="${KILIX_MEMORY_SOFT_RASTER_PY_REPO:-https://github.com/itsmygithubacct/soft-raster-py.git}"
KILIX_MEMORY_SOFT_RASTER_PY_REF="${KILIX_MEMORY_SOFT_RASTER_PY_REF:-42d24aac97e1817ea5848235ee60ad560012bfa7}"
KILIX_MEMORY_SOFT_RASTER_REPO="${KILIX_MEMORY_SOFT_RASTER_REPO:-https://github.com/itsmygithubacct/soft-raster.git}"
KILIX_MEMORY_SOFT_RASTER_REF="${KILIX_MEMORY_SOFT_RASTER_REF:-b42a8e4a4dc14e082f8971708dd9bca781f2699d}"

die() { printf 'kilix memory: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix memory: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-kilix-memory.sh [--force|--print-refs]

  --force       rebuild and reinstall even when the verified closure is current
  --print-refs  print the immutable source closure without changing anything
EOF
}

force=0
case "${1:-}" in
  '') ;;
  --force) force=1; shift ;;
  --print-refs)
    printf '%s\n' \
      "kilix-memory=$KILIX_MEMORY_REF" \
      "kitty-frame-presenter=$KILIX_MEMORY_PRESENTER_REF" \
      "soft-raster-py=$KILIX_MEMORY_SOFT_RASTER_PY_REF" \
      "soft-raster=$KILIX_MEMORY_SOFT_RASTER_REF"
    exit 0 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
[ $# -eq 0 ] || { usage >&2; exit 2; }
[ "$(id -u)" -ne 0 ] || die "run this installer as the desktop user, not root"

for value in \
    "$KILIX_MEMORY_REF" "$KILIX_MEMORY_PRESENTER_REF" \
    "$KILIX_MEMORY_SOFT_RASTER_PY_REF" "$KILIX_MEMORY_SOFT_RASTER_REF"; do
  [[ "$value" =~ ^[0-9a-fA-F]{40}$ ]] \
    || die "every source ref must be a full 40-character commit SHA"
done

normalize_absolute() {
  local value="$1" normalized
  case "$value" in /*) ;; *) return 1 ;; esac
  normalized="$(realpath -m -- "$value" 2>/dev/null)" || return 1
  [ "$normalized" = "$value" ] || return 1
  printf '%s\n' "$normalized"
}

source_home="$(normalize_absolute "$GPU_TERMINAL_SOURCE_HOME")" \
  || die "GPU_TERMINAL_SOURCE_HOME must be a normalized absolute path"
prefix="$(normalize_absolute "$KILIX_MEMORY_PREFIX")" \
  || die "KILIX_MEMORY_PREFIX must be a normalized absolute path"
state_dir="$(normalize_absolute "$KILIX_STATE_DIRECTORY")" \
  || die "KILIX_STATE_DIRECTORY must be a normalized absolute path"
case "$source_home" in /|"$HOME") die "refusing broad source root: $source_home" ;; esac
case "$prefix" in /|"$HOME") die "refusing broad install prefix: $prefix" ;; esac

mkdir -p -- "$source_home" "$state_dir"
chmod 0700 -- "$state_dir" 2>/dev/null || true
for protected in "$source_home" "$state_dir"; do
  [ -d "$protected" ] && [ ! -L "$protected" ] \
    && [ "$(stat -c '%u' -- "$protected" 2>/dev/null)" = "$(id -u)" ] \
    || die "source/state directories must be real directories owned by the current user: $protected"
done
if command -v flock >/dev/null 2>&1; then
  exec 9>"$state_dir/kilix-memory-install.lock"
  flock 9
fi

# Keep installer-owned exact checkouts separate from editable sibling projects.
# Versioned paths make an update rollback-safe without resetting or replacing a
# developer's checkout, and an older closure remains available after rollback.
managed_sources="$source_home/.kilix-memory-sources"
mkdir -p -- "$managed_sources"
chmod 0700 -- "$managed_sources" 2>/dev/null || true
[ -d "$managed_sources" ] && [ ! -L "$managed_sources" ] \
  && [ "$(stat -c '%u:%a' -- "$managed_sources" 2>/dev/null)" = "$(id -u):700" ] \
  || die "managed source directory must be owned by the current user with mode 0700"
memory_dir="$managed_sources/kilix-memory-$KILIX_MEMORY_REF"
presenter_dir="$managed_sources/kitty-frame-presenter-$KILIX_MEMORY_PRESENTER_REF"
soft_raster_py_dir="$managed_sources/soft-raster-py-$KILIX_MEMORY_SOFT_RASTER_PY_REF"
soft_raster_dir="$managed_sources/soft-raster-$KILIX_MEMORY_SOFT_RASTER_REF"
installed_bin="$prefix/bin/kilix-memory"
installed_library="$prefix/lib/kilix-memory/libsoft-raster.so"
stamp="$state_dir/kilix-memory-install.refs"
expected_refs="$(printf '%s\n' \
  "kilix-memory=$KILIX_MEMORY_REF" \
  "kitty-frame-presenter=$KILIX_MEMORY_PRESENTER_REF" \
  "soft-raster-py=$KILIX_MEMORY_SOFT_RASTER_PY_REF" \
  "soft-raster=$KILIX_MEMORY_SOFT_RASTER_REF")"

graphics_work() {
  [ -x "$installed_bin" ] && [ -f "$installed_library" ] || return 1
  SOFT_RASTER_LIBRARY="$installed_library" python3 - "$installed_bin" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from kilix_memory.graphics import graphics_available

available, reason = graphics_available()
if not available:
    print(reason, file=sys.stderr)
    raise SystemExit(1)
PY
}

if [ "$force" = 0 ] && [ -f "$stamp" ] \
     && printf '%s\n' "$expected_refs" | cmp -s - "$stamp" \
     && graphics_work; then
  log "verified graphical dashboard already installed at $installed_bin"
  exit 0
fi

for command in git make cc python3 install; do
  command -v "$command" >/dev/null 2>&1 \
    || die "$command is required (install build-essential, git, and python3)"
done

clone_tmp=""
cleanup() {
  [ -z "$clone_tmp" ] || rm -rf -- "$clone_tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ensure_checkout() {
  local label="$1" directory="$2" repository="$3" ref="$4"
  local origin head checkout
  if git -C "$directory" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    origin="$(git -C "$directory" remote get-url origin 2>/dev/null || true)"
    [ "$origin" = "$repository" ] \
      || die "$label checkout has origin '${origin:-missing}', expected '$repository': $directory"
    [ -z "$(git -C "$directory" status --porcelain --untracked-files=normal)" ] \
      || die "$label checkout has local changes; refusing to build an unpinned tree: $directory"
    head="$(git -C "$directory" rev-parse HEAD 2>/dev/null || true)"
    [ "${head,,}" = "${ref,,}" ] \
      || die "$label checkout is at ${head:-unknown}, expected $ref: $directory"
    return 0
  fi
  [ ! -e "$directory" ] \
    || die "$label path exists but is not a Git checkout: $directory"
  clone_tmp="$(mktemp -d "$managed_sources/.clone.XXXXXX")" \
    || die "could not allocate a temporary clone directory"
  checkout="$clone_tmp/checkout"
  log "cloning pinned $label -> $directory"
  git clone --no-checkout -- "$repository" "$checkout" \
    || die "could not clone $label from $repository"
  git -C "$checkout" checkout --detach "$ref" \
    || die "$label commit $ref is unavailable from $repository"
  head="$(git -C "$checkout" rev-parse HEAD)"
  [ "${head,,}" = "${ref,,}" ] || die "$label resolved to the wrong commit"
  mv -- "$checkout" "$directory" || die "could not publish $label checkout"
  rm -rf -- "$clone_tmp"
  clone_tmp=""
}

ensure_checkout "Kilix Memory" "$memory_dir" \
  "$KILIX_MEMORY_REPO" "$KILIX_MEMORY_REF"
ensure_checkout "kitty-frame-presenter" "$presenter_dir" \
  "$KILIX_MEMORY_PRESENTER_REPO" "$KILIX_MEMORY_PRESENTER_REF"
ensure_checkout "soft-raster-py" "$soft_raster_py_dir" \
  "$KILIX_MEMORY_SOFT_RASTER_PY_REPO" "$KILIX_MEMORY_SOFT_RASTER_PY_REF"
ensure_checkout "soft-raster" "$soft_raster_dir" \
  "$KILIX_MEMORY_SOFT_RASTER_REPO" "$KILIX_MEMORY_SOFT_RASTER_REF"

log "building and installing the pinned graphical dashboard"
make -B -C "$memory_dir" install \
  PREFIX="$prefix" \
  PRESENTER_DIR="$presenter_dir" \
  SOFT_RASTER_PY_DIR="$soft_raster_py_dir" \
  SOFT_RASTER_DIR="$soft_raster_dir"
graphics_work || die "installed dashboard failed its graphical dependency check"

stamp_tmp="$(mktemp "$state_dir/.kilix-memory-refs.XXXXXX")" \
  || die "could not create install stamp"
printf '%s\n' "$expected_refs" >"$stamp_tmp"
chmod 0600 "$stamp_tmp"
mv -fT -- "$stamp_tmp" "$stamp"
log "installed and verified $installed_bin"
