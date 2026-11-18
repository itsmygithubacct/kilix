#!/usr/bin/env bash
# Prepare the YOLOX object-detection runtime kilix-look and kilix-nvr can detect
# with instead of Ultralytics YOLO.
#
# The same shape as install-yolo.sh - a virtualenv the detector subprocess runs
# in, weights, and a wrapper - with three differences that are the reason it is
# a separate runtime rather than a model option of that one:
#
#   * The licence.  Ultralytics and its weights are AGPL-3.0; YOLOX is
#     Apache-2.0 with weights included.  This is the detector to use in
#     anything redistributed.
#   * The size.  onnxruntime and numpy are tens of megabytes; torch is a
#     gigabyte or more.  There is no CUDA/cpu choice to make.
#   * The source.  The detector script, the checksum list and the cut tool
#     belong to the kilix-yolox module, which this installer clones at an
#     immutable pinned commit (and moves an existing clean checkout to), the
#     same way the other component installers do.  Nothing in it is built.
#   * The weights.  They are a kilix-content asset, so they arrive by
#     `kilix models install`: the pinned download, the verbatim licence screen
#     and the typed agreement all belong to kilix-license, which writes the
#     receipt.  This script accepts nothing on the user's behalf, so there is
#     no way to pass a licence with --yes.
#
# Nothing is installed system-wide and nothing is installed as root. Removing
# the runtime directory removes the runtime.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_DATA_HOME="${GPU_TERMINAL_DATA_HOME:-$HOME/.local/gpu_terminal}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$GPU_TERMINAL_DATA_HOME/sources}"
source_was_set="${KILIX_YOLOX_SRC:+x}"
model_was_set="${KILIX_YOLOX_MODEL:+x}"
size_was_set="${KILIX_YOLOX_SIZE:+x}"
KILIX_YOLOX_DIR="${KILIX_YOLOX_DIR:-$GPU_TERMINAL_DATA_HOME/runtimes/yolox}"
KILIX_YOLOX_SRC="${KILIX_YOLOX_SRC:-$GPU_TERMINAL_SOURCE_HOME/kilix-modules/kilix-yolox}"
KILIX_YOLOX_REPO="${KILIX_YOLOX_REPO:-https://github.com/itsmygithubacct/kilix-yolox.git}"
KILIX_YOLOX_AUTO_INSTALL="${KILIX_YOLOX_AUTO_INSTALL:-1}"
KILIX_YOLOX_TRUST_EXISTING_CHECKOUT="${KILIX_YOLOX_TRUST_EXISTING_CHECKOUT:-0}"
KILIX_YOLOX_KEEP_EXISTING_CHECKOUT="${KILIX_YOLOX_KEEP_EXISTING_CHECKOUT:-0}"
KILIX_YOLOX_ALLOW_MUTABLE_REF="${KILIX_YOLOX_ALLOW_MUTABLE_REF:-0}"

# This full commit is part of Kilix's transitive source closure. Every run
# resolves it - a first-use clone and an existing checkout alike - so a moved
# pin reaches machines that already have the module. Set
# KILIX_YOLOX_KEEP_EXISTING_CHECKOUT=1 to work from a checkout as it is.
KILIX_YOLOX_DEFAULT_REF=fa085fae3d7c605deb217794886d0053e38e8e3d
KILIX_YOLOX_MODEL="${KILIX_YOLOX_MODEL:-yolox_s}"
# kilix-look sends a 320-pixel square. The detector prefers the cut that matches
# the frame (0.89 on the test image against 0.52 for the 640 export fed the same
# square), so the cut is made at install time rather than left as a manual step.
KILIX_YOLOX_SIZE="${KILIX_YOLOX_SIZE:-320}"
KILIX_YOLOX_ASSUME_YES="${KILIX_YOLOX_ASSUME_YES:-0}"
KILIX_PYTHON="${KILIX_PYTHON:-python3}"
KILIX_UV="${KILIX_UV:-uv}"

die() { printf 'kilix yolox: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix yolox: %s\n' "$*" >&2; }

have_uv() { command -v "$KILIX_UV" >/dev/null 2>&1; }

python_install() {
  if [ "${KILIX_YOLOX_WHEELHOUSE+x}" = x ] || [ "${KILIX_YOLOX_REQUIREMENTS+x}" = x ]; then
    [ -n "${KILIX_YOLOX_WHEELHOUSE:-}" ] && [ -d "$KILIX_YOLOX_WHEELHOUSE" ] \
      && [ -n "${KILIX_YOLOX_REQUIREMENTS:-}" ] && [ -f "$KILIX_YOLOX_REQUIREMENTS" ] \
      || die "offline wheelhouse and hashed requirements must both be present"
    case "$KILIX_YOLOX_WHEELHOUSE" in /*) ;; *) die "offline wheelhouse must be an absolute local directory" ;; esac
    case "$KILIX_YOLOX_REQUIREMENTS" in /*) ;; *) die "hashed requirements must be an absolute local file" ;; esac
    [ ! -L "$KILIX_YOLOX_WHEELHOUSE" ] && [ ! -L "$KILIX_YOLOX_REQUIREMENTS" ] \
      || die "offline dependency inputs cannot be symlinks"
    "$KILIX_PYTHON" - "$KILIX_YOLOX_REQUIREMENTS" "$yolox_dir/.requirements.lock" <<'PY' \
      || die "offline requirements must contain only pinned, hashed packages"
import os,re,stat,sys
from pathlib import Path
fd=os.open(sys.argv[1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
with os.fdopen(fd,'rb') as source:
    info=os.fstat(source.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_size>1024*1024: raise SystemExit(1)
    data=source.read(1024*1024+1)
if len(data)>1024*1024: raise SystemExit(1)
raw=data.decode('utf-8')
lines=[line.strip() for line in raw.splitlines() if line.strip() and not line.lstrip().startswith('#')]
pattern=r'[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9][A-Za-z0-9.+!_-]*\s+--hash=sha256:[0-9a-f]{64}'
names=[re.split('==',line,1)[0].lower().replace('_','-').replace('.','-') for line in lines]
if not lines or any(not re.fullmatch(pattern,line) for line in lines) or len(names)!=len(set(names)):
    raise SystemExit(1)
fd=os.open(sys.argv[2],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'wb') as destination: destination.write(data)
PY
    mkdir -p -- "$yolox_dir/.pip-tmp"
    env -i PATH=/usr/bin:/bin HOME="$yolox_dir" TMPDIR="$yolox_dir/.pip-tmp" PIP_CONFIG_FILE=/dev/null \
      "$python" -I -B -m pip install --quiet --no-index --only-binary=:all: \
      --find-links "$KILIX_YOLOX_WHEELHOUSE" --require-hashes -r "$yolox_dir/.requirements.lock" >&2
  elif have_uv; then
    "$KILIX_UV" pip install --python "$python" "$@" >&2
  else
    "$python" -m pip install --quiet "$@" >&2
  fi
}

usage() {
  cat <<'EOF'
usage: install-yolox.sh [--print-path|--print-ref|--check|--install|--upgrade|--remove] [--yes]
       install-yolox.sh --run [DETECTOR OPTIONS]

  --print-path  install if needed, then print the detector command path
  --print-ref   print the immutable pinned kilix-yolox commit, changing nothing
  --check       report what is present without changing anything
  --run         verify the installed model/runtime and execute it; never install
  --install     install, asking first unless --yes (the licence is never
                skipped: it needs a terminal and the typed agreement)
  --upgrade     prepare and select a new runtime generation
  --remove      delete the runtime directory

Environment:
  KILIX_YOLOX_DIR    where the virtualenv and weights live
  KILIX_YOLOX_SRC    the kilix-yolox source to snapshot into a generation
  KILIX_YOLOX_REF    use this commit instead of the pin
  KILIX_YOLOX_KEEP_EXISTING_CHECKOUT=1
                     work from an existing checkout as it is; the pin is not
                     installed
  KILIX_YOLOX_TRUST_EXISTING_CHECKOUT=1
                     accept a checkout whose origin is not KILIX_YOLOX_REPO
  KILIX_YOLOX_MODEL  yolox_s (default), yolox_tiny or yolox_nano
  KILIX_YOLOX_SIZE   the square the model is cut for, default 320
  KILIX_UV           the uv to use; venv + pip when it is not found
  KILIX_YOLOX_WHEELHOUSE and KILIX_YOLOX_REQUIREMENTS
                     install from this local wheelhouse with a lock containing
                     one `name==version --hash=sha256:...` per line
EOF
}

action="--print-path"
assume_yes=0
while [ $# -gt 0 ]; do
  case "$1" in
    --run) action=--run; shift; break ;;
    --print-path|--print-ref|--check|--install|--upgrade|--remove) action="$1" ;;
    --yes|-y) assume_yes=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done
case "$KILIX_YOLOX_ASSUME_YES" in 1|yes|true|on) assume_yes=1 ;; esac
[ "$(id -u)" -ne 0 ] || die "run this as the desktop user, not root"

case "$KILIX_YOLOX_DIR" in
  /*) ;;
  *) die "KILIX_YOLOX_DIR must be an absolute path: $KILIX_YOLOX_DIR" ;;
esac
yolox_dir="$(realpath -m -- "$KILIX_YOLOX_DIR" 2>/dev/null)" \
  || die "could not normalize KILIX_YOLOX_DIR=$KILIX_YOLOX_DIR"
runtime_base="$yolox_dir"
requested_source="$KILIX_YOLOX_SRC"
if [ "${KILIX_YOLOX_INTERNAL:-0}" = 1 ]; then
  internal_parent="${KILIX_YOLOX_PARENT:-}"
  case "$yolox_dir" in "$internal_parent"/.generations/gen-*) ;; *) die "invalid internal generation path" ;; esac
  case "${yolox_dir#"$internal_parent"/.generations/}" in */*|.|..) die "invalid internal generation path" ;; esac
  [ -n "$internal_parent" ] && [ -d "$internal_parent" ] \
    && [ "$(realpath -e -- "$internal_parent")" = "$internal_parent" ] \
    && [ "$KILIX_YOLOX_SRC" = "$yolox_dir/source" ] \
    || die "invalid internal generation source"
  if [ -e "$internal_parent/.current" ] || [ -L "$internal_parent/.current" ]; then
    [ -L "$internal_parent/.current" ] \
      && [ "$(realpath -m -- "$internal_parent/.current")" != "$yolox_dir" ] \
      || die "refusing to prepare the selected generation in place"
  fi
  case "$action" in
    --install) [ ! -e "$yolox_dir/runtime.json" ] && [ ! -e "$yolox_dir/venv" ] \
      || die "refusing to prepare an existing runtime in place" ;;
    --check) ;;
    *) die "invalid internal generation action" ;;
  esac
fi
case "$yolox_dir" in
  /|"$HOME"|"$GPU_TERMINAL_DATA_HOME")
    die "refusing broad runtime path: $yolox_dir" ;;
esac
# Native detector callers split command arguments on whitespace. The recorded
# wrapper must therefore be one unambiguous token, also safe as an env-file line.
"$KILIX_PYTHON" -c 'import sys; p=sys.argv[1]; sys.exit(any(c.isspace() or not c.isprintable() for c in p))' "$yolox_dir" \
  || die "runtime path must contain no whitespace or control characters"

# A generation is prepared at its permanent path and selected by one private
# symlink only after it is complete. Legacy runtimes without this link remain
# readable and are never modified while a successor is being prepared.
if [ "${KILIX_YOLOX_INTERNAL:-0}" != 1 ] && [ "$action" != --print-ref ]; then
  pointer="$runtime_base/.current"
  if [ -e "$pointer" ] || [ -L "$pointer" ]; then
    [ -L "$pointer" ] || die "runtime generation selection is not a symlink"
    target="$(readlink -- "$pointer")" || die "cannot read runtime generation selection"
    case "$target" in .generations/gen-*) ;; *) die "invalid runtime generation selection" ;; esac
    case "${target#.generations/}" in */*|.|..) die "invalid runtime generation selection" ;; esac
    selected="$runtime_base/$target"
    [ -d "$selected" ] && [ ! -L "$selected" ] \
      && [ "$(realpath -e -- "$selected")" = "$selected" ] \
      || die "runtime generation selection is missing or unsafe"
    yolox_dir="$selected"
    KILIX_YOLOX_SRC="$selected/source"
    [ -n "$source_was_set" ] || requested_source="$KILIX_YOLOX_SRC"
  fi
  if [ -f "$yolox_dir/runtime.json" ] && [ ! -L "$yolox_dir/runtime.json" ]; then
    recorded="$("$KILIX_PYTHON" - "$yolox_dir/runtime.json" <<'PY'
import json,sys
try:
    x=json.load(open(sys.argv[1])); print(str(x['model'])+' '+str(x['size']))
except (OSError,ValueError,KeyError,TypeError):
    pass
PY
)"
    if [ -n "$recorded" ]; then
      read -r recorded_model recorded_size <<< "$recorded"
      [ -n "$model_was_set" ] || KILIX_YOLOX_MODEL="$recorded_model"
      [ -n "$size_was_set" ] || KILIX_YOLOX_SIZE="$recorded_size"
    fi
  fi
fi
# The model name reaches a path and a python argument; only the three the
# checksum list knows are accepted, so it can carry neither.
case "$KILIX_YOLOX_MODEL" in
  yolox_s|yolox_tiny|yolox_nano) ;;
  *) die "KILIX_YOLOX_MODEL must be yolox_s, yolox_tiny or yolox_nano: $KILIX_YOLOX_MODEL" ;;
esac
case "$KILIX_YOLOX_SIZE" in
  ''|*[!0-9]*|0*) die "KILIX_YOLOX_SIZE must be a positive integer: $KILIX_YOLOX_SIZE" ;;
esac
"$KILIX_PYTHON" -c 'import sys; sys.exit(int(sys.argv[1]) % 32 != 0)' "$KILIX_YOLOX_SIZE" \
  || die "KILIX_YOLOX_SIZE must be a positive multiple of 32"

venv="$yolox_dir/venv"
python="$venv/bin/python"
models="$yolox_dir/models"
weights="$models/${KILIX_YOLOX_MODEL}_${KILIX_YOLOX_SIZE}.onnx"
wrapper="$yolox_dir/bin/kilix-yolox-detect"

# ---------------------------------------------------------------- state ----

runtime_binding() {
  "$KILIX_PYTHON" "$KILIX_HOME/config/yolox_runtime.py" "$1" \
    "$yolox_dir" "$KILIX_YOLOX_SRC" "$KILIX_YOLOX_MODEL" "$KILIX_YOLOX_SIZE"
}

runtime_ready() {
  if git -C "$KILIX_YOLOX_SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
      && [ "$KILIX_YOLOX_KEEP_EXISTING_CHECKOUT" != 1 ]; then
    [ "$(git -C "$KILIX_YOLOX_SRC" rev-parse HEAD)" = "$install_ref" ] || return 1
  fi
  [ -x "$python" ] && [ -f "$weights" ] && [ -x "$wrapper" ] \
    && PYTHONDONTWRITEBYTECODE=1 "$python" -c 'import onnxruntime, numpy' >/dev/null 2>&1 \
    && "$KILIX_HOME/kilix" models verify "$KILIX_YOLOX_MODEL" >/dev/null 2>&1 \
    && runtime_binding check >/dev/null 2>&1
}

# ------------------------------------------------------------- source ----

install_ref="${KILIX_YOLOX_REF:-$KILIX_YOLOX_DEFAULT_REF}"
if ! [[ "$install_ref" =~ ^[0-9a-fA-F]{40}$ ]] \
     && [ "$KILIX_YOLOX_ALLOW_MUTABLE_REF" != 1 ]; then
  die "KILIX_YOLOX_REF must be a full 40-character commit SHA (set KILIX_YOLOX_ALLOW_MUTABLE_REF=1 only to trust a mutable tag/branch)"
fi

checkout_ref() {
  local directory="$1" ref="$2" require_clean="${3:-1}" target
  if [ "$require_clean" = 1 ] \
       && [ -n "$(git -C "$directory" status --porcelain --untracked-files=normal 2>/dev/null)" ]; then
    die "ref checkout refused because $directory has local modifications"
  fi
  git -C "$directory" fetch --no-tags origin "$ref" >&2 \
    || die "could not fetch KILIX_YOLOX_REF=$ref"
  target="$(git -C "$directory" rev-parse --verify 'FETCH_HEAD^{commit}' 2>/dev/null)" \
    || die "KILIX_YOLOX_REF did not resolve to a commit"
  git -C "$directory" checkout --detach "$target" >&2 \
    || die "could not check out KILIX_YOLOX_REF=$ref"
  [ "$(git -C "$directory" rev-parse --verify HEAD 2>/dev/null)" = "$target" ] \
    || die "kilix-yolox checkout verification failed"
}

# An existing checkout is not exempt from the pin: reinstalling from whatever
# it happens to hold would let a moved default reach every fresh install and no
# update.
advance_existing_checkout() {
  local directory="$1" head
  head="$(git -C "$directory" rev-parse HEAD 2>/dev/null || true)"
  case "$KILIX_YOLOX_KEEP_EXISTING_CHECKOUT" in
    1|yes|true|on)
      log "keeping the existing checkout at ${head:0:12} as asked (KILIX_YOLOX_KEEP_EXISTING_CHECKOUT=1)"
      log "the resolved ref ${install_ref:0:12} was NOT installed"
      return 0 ;;
  esac
  if [ "${head,,}" = "${install_ref,,}" ]; then
    return 0
  fi
  if [ -n "$(git -C "$directory" status --porcelain --untracked-files=normal 2>/dev/null)" ]; then
    # A tree someone is working in is kept, loudly, rather than thrown away.
    log "keeping the existing checkout at ${head:0:12}: it has local modifications"
    log "the resolved ref ${install_ref:0:12} was NOT installed; commit, stash or remove them"
    return 0
  fi
  if [ -n "${_detector_carrier_tmp:-}" ]; then
    [ -d "$directory/.git" ] && [ ! -L "$directory/.git" ] \
      || die "offline update requires a standalone Git checkout"
    local update_tmp staged
    update_tmp="$(mktemp -d "$(dirname "$directory")/.kilix-yolox.update.XXXXXX")" \
      || die "could not allocate an update stage"
    (
      trap 'rm -rf -- "$update_tmp"' EXIT
      trap 'exit 130' INT
      trap 'exit 143' TERM
      staged="$update_tmp/checkout"
      cp -a -- "$directory" "$staged" || die "could not stage the existing checkout"
      [ "$(git -C "$staged" rev-parse --show-toplevel)" = "$staged" ] \
        || die "staged Git checkout redirects outside its directory"
      checkout_ref "$staged" "$install_ref"
      [ "$(git -C "$directory" rev-parse HEAD)" = "$head" ] \
        && [ -z "$(git -C "$directory" status --porcelain --untracked-files=normal)" ] \
        || die "installed checkout changed while preparing update"
      python3 - "$staged" "$directory" <<'EXCHANGE'
import ctypes
import os
import sys
library = ctypes.CDLL(None, use_errno=True)
rename = library.renameat2
rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                   ctypes.c_char_p, ctypes.c_uint]
rename.restype = ctypes.c_int
if rename(-100, os.fsencode(sys.argv[1]), -100, os.fsencode(sys.argv[2]), 2):
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error))
EXCHANGE
    )
  else
    checkout_ref "$directory" "$install_ref"
  fi
  if [ -n "$head" ] \
       && git -C "$directory" merge-base --is-ancestor "$install_ref" "$head" >/dev/null 2>&1; then
    log "existing checkout REWOUND ${head:0:12} -> ${install_ref:0:12} (the pinned ref is older)"
  else
    log "existing checkout advanced ${head:0:12} -> ${install_ref:0:12}"
  fi
}

resolve_source() {
  local src origin parent clone_tmp checkout
  case "$KILIX_YOLOX_SRC" in
    /*) ;;
    *) die "KILIX_YOLOX_SRC must be a normalized absolute path: $KILIX_YOLOX_SRC" ;;
  esac
  src="$(realpath -m -- "$KILIX_YOLOX_SRC" 2>/dev/null)" \
    || die "could not normalize KILIX_YOLOX_SRC=$KILIX_YOLOX_SRC"
  [ "$src" = "$KILIX_YOLOX_SRC" ] \
    || die "KILIX_YOLOX_SRC must be normalized and contain no symlink components: $KILIX_YOLOX_SRC"
  case "$src" in
    /|"$HOME"|"$GPU_TERMINAL_SOURCE_HOME")
      die "refusing broad kilix-yolox checkout path: $src" ;;
  esac
  command -v git >/dev/null 2>&1 || die "git is required"

  if [ "${KILIX_SOURCE_CARRIER_MANIFEST+x}" = x ] || [ "${KILIX_SOURCE_CARRIER_SHA256+x}" = x ] \
       || [ -e /usr/share/plebian-os/source-carrier/manifest.json ] \
       || [ -L /usr/share/plebian-os/source-carrier/manifest.json ] \
       || [ -e /usr/share/plebian-os/source-carrier/manifest.sha256 ] \
       || [ -L /usr/share/plebian-os/source-carrier/manifest.sha256 ]; then
    local carrier_adapter carrier_parent carrier_keep
    carrier_adapter="$KILIX_HOME/scripts/detector-carrier.sh"
    [ -f "$carrier_adapter" ] && [ ! -L "$carrier_adapter" ] \
      || die "the detector source-carrier adapter is unavailable"
    . "$carrier_adapter"
    carrier_parent="$(dirname "$src")"
    mkdir -p -- "$carrier_parent" || die "could not create the source parent"
    [ -d "$carrier_parent" ] && [ ! -L "$carrier_parent" ] \
      || die "source parent must be a real directory"
    carrier_keep=0
    case "$KILIX_YOLOX_KEEP_EXISTING_CHECKOUT" in 1|yes|true|on)
      [ ! -e "$src/.git" ] || carrier_keep=1 ;;
    esac
    detector_carrier_prepare "$KILIX_HOME" "$KILIX_YOLOX_REPO" "$install_ref" \
      "$carrier_parent" "$carrier_keep" || die "offline source carrier refused the request"
  fi

  if git -C "$src" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    [ "$(realpath -m -- "$(git -C "$src" rev-parse --show-toplevel)")" = "$src" ] \
      || die "$src is nested inside a different Git checkout"
    if [ -n "${_detector_carrier_tmp:-}" ]; then
      # get-url expands the offline rewrite; compare the stored origin instead.
      origin="$(git -C "$src" config --local --get remote.origin.url 2>/dev/null || true)"
    else
      origin="$(git -C "$src" remote get-url origin 2>/dev/null || true)"
    fi
    if [ "$origin" != "$KILIX_YOLOX_REPO" ] \
         && [ "$KILIX_YOLOX_TRUST_EXISTING_CHECKOUT" != 1 ]; then
      die "$src has origin '${origin:-missing}', expected '$KILIX_YOLOX_REPO' (set KILIX_YOLOX_TRUST_EXISTING_CHECKOUT=1 only for a trusted checkout)"
    fi
    advance_existing_checkout "$src"
    return 0
  fi
  if [ -e "$src" ] || [ -L "$src" ]; then
    [ "$KILIX_YOLOX_TRUST_EXISTING_CHECKOUT" = 1 ] \
      || die "$src exists but is not a Git checkout"
    log "using trusted packaged source at $src"
    return 0
  fi
  case "$KILIX_YOLOX_AUTO_INSTALL" in
    1|yes|true|on) ;;
    *) die "kilix-yolox is not installed at $src; set KILIX_YOLOX_AUTO_INSTALL=1 to download it" ;;
  esac
  parent="$(dirname "$src")"
  mkdir -p -- "$parent" || die "could not create checkout parent: $parent"
  [ -d "$parent" ] && [ ! -L "$parent" ] \
    || die "checkout parent must be a real directory: $parent"
  clone_tmp="$(mktemp -d "$parent/.kilix-yolox.clone.XXXXXX")" \
    || die "could not allocate a temporary clone directory"
  checkout="$clone_tmp/checkout"
  log "downloading pinned kilix-yolox $install_ref -> $src"
  if git clone --no-checkout -- "$KILIX_YOLOX_REPO" "$checkout" >&2 \
       && ( checkout_ref "$checkout" "$install_ref" 0 ); then
    [ ! -e "$src" ] && [ ! -L "$src" ] \
      || { rm -rf -- "$clone_tmp"; die "checkout path appeared while kilix-yolox was being prepared: $src"; }
    mv -- "$checkout" "$src" || { rm -rf -- "$clone_tmp"; die "could not publish the prepared checkout"; }
    rm -rf -- "$clone_tmp"
  else
    rm -rf -- "$clone_tmp"
    die "could not prepare kilix-yolox at $install_ref from $KILIX_YOLOX_REPO"
  fi
}

module_tool() {
  local tool="$KILIX_YOLOX_SRC/tools/$1"
  [ -f "$tool" ] && [ ! -L "$tool" ] \
    || die "no $1 in $KILIX_YOLOX_SRC; is it a kilix-yolox checkout?"
  printf '%s\n' "$tool"
}

# Where kilix-content puts the exported weights.  Asked of the authority that
# owns the layout rather than spelled here; `show` changes nothing.
content_asset() {
  local root
  root="$("$KILIX_HOME/kilix" models show "$KILIX_YOLOX_MODEL" 2>/dev/null \
    | "$KILIX_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["root"])')" \
    || die "kilix models could not describe $KILIX_YOLOX_MODEL"
  [ -n "$root" ] || die "kilix models reported no installer root"
  printf '%s\n' "$root/assets/$KILIX_YOLOX_MODEL"
}

report() {
  printf 'runtime:   %s\n' "$yolox_dir"
  printf 'source:    %s\n' "$KILIX_YOLOX_SRC"
  if have_uv; then
    printf 'installer: %s\n' "$("$KILIX_UV" --version 2>/dev/null || echo uv)"
  else
    printf 'installer: venv + pip (uv is not installed)\n'
  fi
  if [ -x "$python" ]; then
    printf 'python:    %s\n' "$("$python" --version 2>&1)"
  else
    printf 'python:    not installed\n'
  fi
  if [ -x "$python" ] && "$python" -c 'import onnxruntime' >/dev/null 2>&1; then
    printf 'onnxruntime: %s\n' \
      "$("$python" -c 'import onnxruntime; print(onnxruntime.__version__)' 2>/dev/null)"
  else
    printf 'onnxruntime: not installed\n'
  fi
  if [ -f "$weights" ]; then
    printf 'weights:   %s (%s bytes)\n' "$weights" "$(stat -c %s "$weights")"
  else
    printf 'weights:   %s missing\n' "$weights"
  fi
  if [ -x "$wrapper" ]; then
    printf 'detector:  %s\n' "$wrapper"
  else
    printf 'detector:  not written\n'
  fi
}

# -------------------------------------------------------------- install ----

confirm() {
  cat >&2 <<EOF
kilix yolox installs the object detector's runtime into
    $yolox_dir
It creates a virtualenv, installs onnxruntime, numpy and onnx into it (about
300 MB), gets $KILIX_YOLOX_MODEL through \`kilix models install\` (pinned download, the
Apache-2.0 licence screen and your typed agreement, recorded by kilix-license),
and cuts it for a ${KILIX_YOLOX_SIZE}-pixel square. Nothing is installed
system-wide and nothing runs as root; deleting that directory removes all of it.
EOF
  [ "$assume_yes" = 1 ] && return 0
  printf 'continue? [y/N] ' >&2
  local answer=""
  read -r answer || true
  case "${answer,,}" in y|yes) return 0 ;; esac
  log "cancelled"
  return 1
}

install_runtime() {
  local cut asset want got
  resolve_source
  module_tool kilix-yolox-detect >/dev/null
  cut="$(module_tool kilix-yolox-cut)"

  # Existing files alone grant neither agreement nor integrity. Use the same
  # authority for fresh and reused models, before creating a runtime.
  asset="$(content_asset)"
  if ! "$KILIX_HOME/kilix" models verify "$KILIX_YOLOX_MODEL" >/dev/null 2>&1; then
    { [ -t 0 ] && [ -t 2 ]; } \
      || die "the YOLOX licence needs your typed agreement and verified model bytes: run \`kilix models install $KILIX_YOLOX_MODEL\` in a terminal, then re-run this"
    log "the model needs installation or agreement; its licence screen follows"
    "$KILIX_HOME/kilix" models install "$KILIX_YOLOX_MODEL" >&2 \
      || die "the model was not installed (declined, or the download failed)"
    "$KILIX_HOME/kilix" models verify "$KILIX_YOLOX_MODEL" >/dev/null \
      || die "the installed model or agreement could not be verified"
  fi
  want="$(awk -v f="${KILIX_YOLOX_MODEL}.onnx" '$2==f {print $1}' "$KILIX_YOLOX_SRC/models/SHA256SUMS")"
  got="$(sha256sum -- "$asset/${KILIX_YOLOX_MODEL}.onnx" | cut -d' ' -f1)"
  [ -n "$want" ] && [ "$want" = "$got" ] \
    || die "$KILIX_YOLOX_MODEL does not match the kilix-yolox checksum list"
  mkdir -p "$models" "$yolox_dir/bin"
  cp -- "$asset/${KILIX_YOLOX_MODEL}.onnx" "$models/${KILIX_YOLOX_MODEL}.onnx"
  cp -- "$asset/notices/LICENSE-apache-2.0.txt" "$models/LICENSE.yolox"
  printf 'YOLOX weights, Megvii-BaseDetection/YOLOX.\nLicensed under the Apache License, Version 2.0; see LICENSE.yolox.\n' > "$models/NOTICE"
  if [ ! -x "$python" ]; then
    if have_uv && [ "${KILIX_YOLOX_WHEELHOUSE+x}" != x ] && [ "${KILIX_YOLOX_REQUIREMENTS+x}" != x ]; then
      local interpreter
      # Resolved to a path first: `uv venv --python python3` may be satisfied
      # by an interpreter uv downloads itself, which is not the one asked for.
      interpreter="$(command -v "$KILIX_PYTHON" 2>/dev/null || true)"
      [ -n "$interpreter" ] || die "no interpreter called $KILIX_PYTHON"
      log "creating the virtualenv with uv on $interpreter"
      "$KILIX_UV" venv --python "$interpreter" "$venv" >&2 \
        || die "uv could not create a virtualenv"
    else
      log "creating the virtualenv (uv is not installed; using venv)"
      "$KILIX_PYTHON" -m venv "$venv" \
        || die "could not create a virtualenv (install python3-venv)"
    fi
  fi
  if ! have_uv && [ "${KILIX_YOLOX_WHEELHOUSE+x}" != x ] && [ "${KILIX_YOLOX_REQUIREMENTS+x}" != x ]; then
    "$python" -m pip install --quiet --upgrade pip >&2 || true
  fi
  python_install onnxruntime numpy onnx \
    || die "could not install onnxruntime"

  # Rebuild rather than trusting a pre-existing derived graph. The cut tool
  # publishes atomically; the binding below records its bytes and inputs.
  log "cutting $KILIX_YOLOX_MODEL for a ${KILIX_YOLOX_SIZE}-pixel square"
  "$python" "$cut" "$models/${KILIX_YOLOX_MODEL}.onnx" \
    --size "$KILIX_YOLOX_SIZE" >&2 || die "could not cut $KILIX_YOLOX_MODEL"
  [ -f "$weights" ] || die "the cut did not produce $weights"

  write_wrapper
  runtime_binding record
  record_setting
  log "installed"
}

write_wrapper() {
  local tool
  tool="$(module_tool kilix-yolox-detect)"
  "$KILIX_PYTHON" - "$wrapper" "$yolox_dir" "$python" "$tool" "$weights" "$KILIX_PYTHON" "$KILIX_HOME" "$KILIX_YOLOX_SRC" "$KILIX_YOLOX_MODEL" "$KILIX_YOLOX_SIZE" <<'PY'
import os
import shlex
import sys
import tempfile

destination, runtime, interpreter, tool, weights, checker_python, host, source, model, size = sys.argv[1:]
payload = ("#!/bin/sh\n"
           "# Written by kilix install yolox.\n"
           'actual=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P) || exit 1\n'
           '[ "$actual" = ' + shlex.quote(runtime) + ' ] || { echo "YOLOX runtime moved; reinstall to rebind" >&2; exit 1; }\n'
           'forbidden=0\n'
           'for argument do\n'
           '  case "$argument" in --model|--model=*) forbidden=1 ;; esac\n'
           'done\n'
           '[ "$forbidden" = 0 ] || { echo "model is selected by the installed runtime" >&2; exit 1; }\n'
           + " ".join(shlex.quote(word) for word in (checker_python, host + "/config/yolox_runtime.py", "check", runtime, source, model, size)) + ' || exit 1\n'
           + " ".join(shlex.quote(word) for word in (host + "/kilix", "models", "verify", model)) + ' >/dev/null 2>&1 || { echo "YOLOX model verification failed" >&2; exit 1; }\n'
           "KILIX_YOLOX_DIR=" + shlex.quote(runtime) + "\n"
           "export KILIX_YOLOX_DIR\n"
           "unset KILIX_YOLOX_MODEL\n"
           "exec " + " ".join(shlex.quote(word) for word in
                              (interpreter, tool, "--model", weights)) + ' "$@"\n')
descriptor, temporary = tempfile.mkstemp(prefix=".yolox-wrapper-",
                                       dir=os.path.dirname(destination))
try:
    with os.fdopen(descriptor, "w") as output:
        os.fchmod(output.fileno(), 0o700)
        output.write(payload)
    os.replace(temporary, destination)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

# The launcher exports allowlisted KILIX_* keys from this file into every pane.
# KILIX_OBJECT_DETECTOR is one slot: recording this runtime replaces a YOLO
# one, which is the point - running `kilix install yolo` again switches back.
record_setting() {
  local config="${KILIX_USER_CONFIG_DIRECTORY:-$GPU_TERMINAL_DATA_HOME/kilix/config}"
  local env_file="$config/kilix.env"
  mkdir -p "$config"
  [ -f "$env_file" ] || : > "$env_file"
  local temporary
  temporary="$(mktemp "$config/.kilix.env.XXXXXX")"
  grep -v -e '^KILIX_OBJECT_DETECTOR=' -e '^KILIX_NVR_DETECT=' "$env_file" \
    > "$temporary" || true
  printf 'KILIX_OBJECT_DETECTOR=%s\n' "$wrapper" >> "$temporary"
  mv -- "$temporary" "$env_file"
  chmod 600 "$env_file"
  log "recorded KILIX_OBJECT_DETECTOR in $env_file"
}

remove_runtime() {
  [ -d "$yolox_dir" ] || { log "nothing to remove at $yolox_dir"; return 0; }
  rm -rf -- "$yolox_dir"
  log "removed $yolox_dir"
  log "KILIX_OBJECT_DETECTOR in the kilix.env is left as it is; run kilix install yolo to point it elsewhere"
}

upgrade_runtime() {
  runtime_ready || die "nothing installed at $yolox_dir yet"
  resolve_source
  log "upgrading onnxruntime, numpy and onnx"
  python_install --upgrade onnxruntime numpy onnx \
    || die "the package upgrade failed; the detector wrapper was not refreshed"
  write_wrapper
  runtime_binding record
  log "upgraded"
}

install_generation() {
  local transaction="$KILIX_HOME/scripts/yolox-generation.py"
  local config="${KILIX_USER_CONFIG_DIRECTORY:-$GPU_TERMINAL_DATA_HOME/kilix/config}"
  [ -f "$transaction" ] && [ ! -L "$transaction" ] \
    || die "runtime generation installer is unavailable"
  exec "$KILIX_PYTHON" -I -B "$transaction" "$(realpath -e -- "${BASH_SOURCE[0]}")" \
    "$KILIX_HOME" "$runtime_base" "$requested_source" "$config/kilix.env" \
    "$KILIX_YOLOX_MODEL" "$KILIX_YOLOX_SIZE"
}

# All public install and upgrade operations build a new, path-bound runtime.
# The internal child runs the existing installer only inside that new path.
if [ "${KILIX_YOLOX_INTERNAL:-0}" != 1 ]; then
  case "$action" in
    --upgrade)
      runtime_ready || die "nothing installed at $yolox_dir yet"
      install_generation
      exit $? ;;
    --install)
      confirm || exit 1
      install_generation
      exit $? ;;
    --print-path)
      if runtime_ready; then printf '%s\n' "$wrapper"; exit 0; fi
      confirm || die "the YOLOX runtime is not installed"
      install_generation
      exit $? ;;
    --remove)
      yolox_dir="$runtime_base" ;;
  esac
fi

case "$action" in
  --print-ref)
    printf '%s\n' "$install_ref"
    exit 0 ;;
  --check)
    report
    runtime_ready && exit 0 || exit 1 ;;
  --run)
    [ "${1:-}" != -- ] || shift
    detector_args=("$@")
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --geometry|--conf|--nms|--input-size|--threads)
          [ "$#" -ge 2 ] || die "missing detector option value: $1"
          shift 2 ;;
        --per-class|--verbose) shift ;;
        *) die "unsupported detector option: $1 (the model is selected by the installed runtime)" ;;
      esac
    done
    # First use installs the selected model, then the generation, then runs.
    # The generation child is not exec'd: this shell still has to start the
    # detector. A second entry with the guard set dies instead of looping.
    # Non-interactive callers keep the old refusal and install nothing.
    if ! runtime_ready; then
      if [ "${KILIX_YOLOX_RUN_PREPARED:-0}" = 1 ]; then
        die "installed YOLOX model, agreement or runtime is not ready; run kilix install yolox"
      fi
      { [ -t 0 ] && [ -t 1 ]; } \
        || die "installed YOLOX model, agreement or runtime is not ready; run kilix install yolox"
      "$KILIX_HOME/kilix" models ensure "$KILIX_YOLOX_MODEL" \
        || die "the model was not installed"
      transaction="$KILIX_HOME/scripts/yolox-generation.py"
      config="${KILIX_USER_CONFIG_DIRECTORY:-$GPU_TERMINAL_DATA_HOME/kilix/config}"
      [ -f "$transaction" ] && [ ! -L "$transaction" ] \
        || die "runtime generation installer is unavailable"
      "$KILIX_PYTHON" -I -B "$transaction" "$(realpath -e -- "${BASH_SOURCE[0]}")" \
        "$KILIX_HOME" "$runtime_base" "$requested_source" "$config/kilix.env" \
        "$KILIX_YOLOX_MODEL" "$KILIX_YOLOX_SIZE"
      KILIX_YOLOX_RUN_PREPARED=1 exec "${BASH_SOURCE[0]}" --run "${detector_args[@]}"
    fi
    exec "$wrapper" "${detector_args[@]}" ;;
  --upgrade)
    upgrade_runtime
    exit 0 ;;
  --remove)
    remove_runtime
    exit 0 ;;
  --install)
    if runtime_ready; then
      log "already installed at $yolox_dir"
      resolve_source
      write_wrapper
      runtime_binding record
      record_setting
      printf '%s\n' "$wrapper"
      exit 0
    fi
    if [ "${KILIX_YOLOX_INTERNAL:-0}" != 1 ]; then confirm || exit 1; fi
    install_runtime
    printf '%s\n' "$wrapper"
    exit 0 ;;
  --print-path)
    if ! runtime_ready; then
      confirm || die "the YOLOX runtime is not installed"
      install_runtime
    fi
    printf '%s\n' "$wrapper"
    exit 0 ;;
esac
