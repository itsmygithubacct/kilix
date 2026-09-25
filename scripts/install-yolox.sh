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
KILIX_YOLOX_DEFAULT_REF=3b921c732bf87b98c98c265509b7f2e0628d6101
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
  if have_uv; then
    "$KILIX_UV" pip install --python "$python" "$@" >&2
  else
    "$python" -m pip install --quiet "$@" >&2
  fi
}

usage() {
  cat <<'EOF'
usage: install-yolox.sh [--print-path|--print-ref|--check|--install|--upgrade|--remove] [--yes]

  --print-path  install if needed, then print the detector command path
  --print-ref   print the immutable pinned kilix-yolox commit, changing nothing
  --check       report what is present without changing anything
  --install     install, asking first unless --yes (the licence is never
                skipped: it needs a terminal and the typed agreement)
  --upgrade     bring the installed packages forward
  --remove      delete the runtime directory

Environment:
  KILIX_YOLOX_DIR    where the virtualenv and weights live
  KILIX_YOLOX_SRC    the kilix-yolox checkout that owns the detector script
  KILIX_YOLOX_REF    use this commit instead of the pin
  KILIX_YOLOX_KEEP_EXISTING_CHECKOUT=1
                     work from an existing checkout as it is; the pin is not
                     installed
  KILIX_YOLOX_TRUST_EXISTING_CHECKOUT=1
                     accept a checkout whose origin is not KILIX_YOLOX_REPO
  KILIX_YOLOX_MODEL  yolox_s (default), yolox_tiny or yolox_nano
  KILIX_YOLOX_SIZE   the square the model is cut for, default 320
  KILIX_UV           the uv to use; venv + pip when it is not found
EOF
}

action="--print-path"
assume_yes=0
while [ $# -gt 0 ]; do
  case "$1" in
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
case "$yolox_dir" in
  /|"$HOME"|"$GPU_TERMINAL_DATA_HOME")
    die "refusing broad runtime path: $yolox_dir" ;;
esac
# The model name reaches a path and a python argument; only the three the
# checksum list knows are accepted, so it can carry neither.
case "$KILIX_YOLOX_MODEL" in
  yolox_s|yolox_tiny|yolox_nano) ;;
  *) die "KILIX_YOLOX_MODEL must be yolox_s, yolox_tiny or yolox_nano: $KILIX_YOLOX_MODEL" ;;
esac
case "$KILIX_YOLOX_SIZE" in
  ''|*[!0-9]*|0*) die "KILIX_YOLOX_SIZE must be a positive integer: $KILIX_YOLOX_SIZE" ;;
esac

venv="$yolox_dir/venv"
python="$venv/bin/python"
models="$yolox_dir/models"
weights="$models/${KILIX_YOLOX_MODEL}_${KILIX_YOLOX_SIZE}.onnx"
wrapper="$yolox_dir/bin/kilix-yolox-detect"

# ---------------------------------------------------------------- state ----

runtime_ready() {
  [ -x "$python" ] && [ -f "$weights" ] && [ -x "$wrapper" ] \
    && "$python" -c 'import onnxruntime, numpy' >/dev/null 2>&1
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
  checkout_ref "$directory" "$install_ref"
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

  if git -C "$src" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    [ "$(realpath -m -- "$(git -C "$src" rev-parse --show-toplevel)")" = "$src" ] \
      || die "$src is nested inside a different Git checkout"
    origin="$(git -C "$src" remote get-url origin 2>/dev/null || true)"
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
  local tool cut
  resolve_source
  tool="$(module_tool kilix-yolox-detect)"
  cut="$(module_tool kilix-yolox-cut)"

  # The licence comes first: declining it leaves no virtualenv behind.
  mkdir -p "$models" "$yolox_dir/bin"
  if [ ! -f "$models/${KILIX_YOLOX_MODEL}.onnx" ]; then
    local asset
    asset="$(content_asset)"
    if [ ! -f "$asset/${KILIX_YOLOX_MODEL}.onnx" ]; then
      # Interactive by design: the authority refuses piped or --yes consent.
      { [ -t 0 ] && [ -t 2 ]; } \
        || die "the YOLOX licence needs your typed agreement: run \`kilix models install $KILIX_YOLOX_MODEL\` in a terminal, then re-run this"
      log "the weights come from kilix-content; its licence screen follows"
      "$KILIX_HOME/kilix" models install "$KILIX_YOLOX_MODEL" >&2 \
        || die "the model was not installed (declined, or the download failed)"
    fi
    [ -f "$asset/${KILIX_YOLOX_MODEL}.onnx" ] \
      || die "kilix models did not leave $KILIX_YOLOX_MODEL at $asset"
    # Content verified every byte against the pinned manifest; the module's own
    # list is a second witness for the same file.
    local want got
    want="$(awk -v f="${KILIX_YOLOX_MODEL}.onnx" '$2==f {print $1}' "$KILIX_YOLOX_SRC/models/SHA256SUMS")"
    got="$(sha256sum -- "$asset/${KILIX_YOLOX_MODEL}.onnx" | cut -d' ' -f1)"
    [ -n "$want" ] && [ "$want" = "$got" ] \
      || die "$KILIX_YOLOX_MODEL does not match the kilix-yolox checksum list"
    cp -- "$asset/${KILIX_YOLOX_MODEL}.onnx" "$models/${KILIX_YOLOX_MODEL}.onnx"
    [ ! -f "$asset/notices/LICENSE-apache-2.0.txt" ] \
      || cp -- "$asset/notices/LICENSE-apache-2.0.txt" "$models/LICENSE.yolox"
    printf 'YOLOX weights, Megvii-BaseDetection/YOLOX.\nLicensed under the Apache License, Version 2.0; see LICENSE.yolox.\n' \
      > "$models/NOTICE"
  fi
  if [ ! -x "$python" ]; then
    if have_uv; then
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
  have_uv || "$python" -m pip install --quiet --upgrade pip >&2 || true
  python_install onnxruntime numpy onnx \
    || die "could not install onnxruntime"

  if [ ! -f "$weights" ]; then
    log "cutting $KILIX_YOLOX_MODEL for a ${KILIX_YOLOX_SIZE}-pixel square"
    "$python" "$cut" "$models/${KILIX_YOLOX_MODEL}.onnx" \
      --size "$KILIX_YOLOX_SIZE" >&2 \
      || die "could not cut $KILIX_YOLOX_MODEL"
  fi
  [ -f "$weights" ] || die "the cut did not produce $weights"

  # A wrapper rather than an environment variable holding a command line:
  # KILIX_OBJECT_DETECTOR is split on spaces with no quoting. It exports the
  # runtime directory so a relocated runtime finds its own weights, and names
  # the model bare so the detector can still choose the cut that matches the
  # frame it is sent.
  cat > "$wrapper" <<EOF
#!/bin/sh
# Written by kilix install yolox. Re-run it to repoint this at a moved
# checkout; delete $yolox_dir to remove the runtime entirely.
KILIX_YOLOX_DIR="$yolox_dir"
export KILIX_YOLOX_DIR
exec "$python" "$tool" --model "$KILIX_YOLOX_MODEL" "\$@"
EOF
  chmod 700 "$wrapper"
  record_setting
  log "installed"
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
  log "upgrading onnxruntime, numpy and onnx"
  python_install --upgrade onnxruntime numpy onnx \
    || die "the upgrade failed; the previous runtime is still in place"
  log "upgraded"
}

case "$action" in
  --print-ref)
    printf '%s\n' "$install_ref"
    exit 0 ;;
  --check)
    report
    runtime_ready && exit 0 || exit 1 ;;
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
      record_setting
      printf '%s\n' "$wrapper"
      exit 0
    fi
    confirm || exit 1
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
