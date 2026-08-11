#!/usr/bin/env bash
# Prepare the YOLO object-detection runtime kilix-nvr detects with.
#
# Unlike the other installers here, this one does not install a Kilix
# component: it builds the *virtualenv* the detector subprocess runs in, and
# fetches the model weights into it.  That is a different kind of thing and it
# is worth being clear about why it exists at all.
#
# kilix-nvr deliberately links no ML runtime.  Its detector is a subprocess
# behind a fixed-size pipe, so where inference happens is a launch detail -
# this machine, a virtualenv, a box with a GPU over ssh.  The cost of that
# design is that a fresh machine has a recorder which cannot detect anything
# and no obvious way to fix it.  This is the obvious way: one command that
# builds the environment, fetches the weights, and tells Kilix where they are.
#
# Nothing is installed system-wide and nothing is installed as root.  The
# virtualenv is the user's, under the GPU Terminal data root, and removing the
# directory removes the runtime.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_DATA_HOME="${GPU_TERMINAL_DATA_HOME:-$HOME/.local/gpu_terminal}"
KILIX_YOLO_DIR="${KILIX_YOLO_DIR:-$GPU_TERMINAL_DATA_HOME/runtimes/yolo}"
KILIX_YOLO_MODEL="${KILIX_YOLO_MODEL:-yolo26s.pt}"
KILIX_YOLO_ASSUME_YES="${KILIX_YOLO_ASSUME_YES:-0}"
# CUDA wheels are roughly 2.5 GB against 200 MB for the cpu-only build, so the
# default follows the hardware rather than the optimism: a machine with no
# NVIDIA display device gains nothing but the download.
KILIX_YOLO_CUDA="${KILIX_YOLO_CUDA:-auto}"
KILIX_PYTHON="${KILIX_PYTHON:-python3}"

die() { printf 'kilix yolo: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix yolo: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-yolo.sh [--print-path|--check|--install] [--yes]

  --print-path  install if needed, then print the detector command path
  --check       report what is present without changing anything
  --install     install, asking first unless --yes
  --upgrade     bring the installed packages and weights up to date
  --remove      delete the runtime directory

Environment:
  KILIX_YOLO_DIR     where the virtualenv and weights live
  KILIX_YOLO_MODEL   weights to fetch, default yolo26s.pt
  KILIX_YOLO_CUDA    auto | 1 | 0 — auto follows the hardware
EOF
}

action="--print-path"
assume_yes=0
while [ $# -gt 0 ]; do
  case "$1" in
    --print-path|--check|--install|--upgrade|--remove) action="$1" ;;
    --yes|-y) assume_yes=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done
case "$KILIX_YOLO_ASSUME_YES" in 1|yes|true|on) assume_yes=1 ;; esac
[ "$(id -u)" -ne 0 ] || die "run this as the desktop user, not root"

case "$KILIX_YOLO_DIR" in
  /*) ;;
  *) die "KILIX_YOLO_DIR must be an absolute path: $KILIX_YOLO_DIR" ;;
esac
yolo_dir="$(realpath -m -- "$KILIX_YOLO_DIR" 2>/dev/null)" \
  || die "could not normalize KILIX_YOLO_DIR=$KILIX_YOLO_DIR"
case "$yolo_dir" in
  /|"$HOME"|"$GPU_TERMINAL_DATA_HOME")
    die "refusing broad runtime path: $yolo_dir" ;;
esac

venv="$yolo_dir/venv"
python="$venv/bin/python"
weights="$yolo_dir/models/$KILIX_YOLO_MODEL"
wrapper="$yolo_dir/bin/kilix-nvr-detect"

# ---------------------------------------------------------------- state ----

runtime_ready() {
  [ -x "$python" ] && [ -f "$weights" ] && [ -x "$wrapper" ] \
    && "$python" -c 'import ultralytics' >/dev/null 2>&1
}

detector_tool() {
  # The detector script belongs to kilix-nvr, so kilix-nvr is what knows where
  # it is.  Asking its installer both resolves the path and installs the
  # recorder when it is missing - which is the right order: a detection
  # runtime with nothing to detect for is not worth building.
  local nvr_binary
  nvr_binary="$("$KILIX_HOME/scripts/install-kilix-nvr.sh" --print-path)" \
    || die "kilix-nvr is needed for the detector script and could not be prepared"
  local tool="${nvr_binary%/build/kilix-nvr}/tools/kilix-nvr-detect"
  [ -f "$tool" ] || die "the kilix-nvr checkout has no tools/kilix-nvr-detect"
  printf '%s\n' "$tool"
}

want_cuda() {
  case "$KILIX_YOLO_CUDA" in
    1|yes|true|on) return 0 ;;
    0|no|false|off) return 1 ;;
  esac
  # The card existing is not the question - the proprietary driver being
  # *loaded* is.  Without it the CUDA wheels are two extra gigabytes that
  # cannot reach the GPU, and torch quietly runs on the CPU anyway.  Install
  # the driver first (`kilix install nvidia-driver`) and re-run this.
  grep -q '^nvidia ' /proc/modules 2>/dev/null || return 1
  command -v lspci >/dev/null 2>&1 || return 1
  # Matched on the display class specifically, and in either order: lspci
  # prints "VGA compatible controller: NVIDIA Corporation ...", so a pattern
  # expecting the vendor first finds nothing and quietly installs the wrong
  # build.  An NVIDIA GPU also presents an audio function for HDMI sound,
  # which reports the same vendor and is not a GPU.
  lspci 2>/dev/null \
    | grep -iE 'vga compatible controller|3d controller' \
    | grep -qi nvidia
}

report() {
  printf 'runtime:   %s\n' "$yolo_dir"
  if [ -x "$python" ]; then
    printf 'python:    %s\n' "$("$python" --version 2>&1)"
  else
    printf 'python:    not installed\n'
  fi
  if [ -x "$python" ] && "$python" -c 'import ultralytics' >/dev/null 2>&1; then
    printf 'ultralytics: %s\n' \
      "$("$python" -c 'import ultralytics; print(ultralytics.__version__)' 2>/dev/null)"
    printf 'torch:     %s\n' \
      "$("$python" -c 'import torch; print(torch.__version__, "cuda" if torch.cuda.is_available() else "cpu")' 2>/dev/null || echo unknown)"
  else
    printf 'ultralytics: not installed\n'
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
  local size="about 1.5 GB on disk (cpu build)"
  want_cuda && size="about 5 GB on disk (CUDA build)"
  cat >&2 <<EOF
kilix yolo installs the object detector's runtime into
    $yolo_dir
It creates a virtualenv, pip installs ultralytics and torch ($size),
and fetches $KILIX_YOLO_MODEL. Nothing is installed system-wide and
nothing runs as root; deleting that directory removes all of it.
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
  local tool index=()
  tool="$(detector_tool)"

  mkdir -p "$yolo_dir/models" "$yolo_dir/bin"
  if [ ! -x "$python" ]; then
    log "creating the virtualenv"
    "$KILIX_PYTHON" -m venv "$venv" \
      || die "could not create a virtualenv (install python3-venv)"
  fi
  if want_cuda; then
    log "NVIDIA display device found: installing the CUDA build"
  else
    log "no loaded NVIDIA driver: installing the cpu-only build"
    log "(install the driver and re-run to get the CUDA one)"
    index=(--index-url https://download.pytorch.org/whl/cpu)
  fi
  "$python" -m pip install --quiet --upgrade pip >&2 || true
  # torch first and explicitly, so the index choice above applies to it: pulled
  # in as an ultralytics dependency it would come from PyPI and be the CUDA
  # build regardless.
  "$python" -m pip install --quiet "${index[@]}" torch torchvision >&2 \
    || die "could not install torch"
  "$python" -m pip install --quiet ultralytics >&2 \
    || die "could not install ultralytics"

  if [ ! -f "$weights" ]; then
    log "fetching $KILIX_YOLO_MODEL"
    # Fetched by loading it once, in the models directory, which is also the
    # only honest check that the runtime works: an install that imports but
    # cannot load a model is an install that fails later, on a camera.
    ( cd "$yolo_dir/models" && \
      YOLO_CONFIG_DIR="$yolo_dir/config" \
      "$python" -c "from ultralytics import YOLO; YOLO('$KILIX_YOLO_MODEL')" >&2 ) \
      || die "could not fetch $KILIX_YOLO_MODEL"
  fi
  [ -f "$weights" ] || die "$KILIX_YOLO_MODEL did not land in $yolo_dir/models"

  # A wrapper rather than an environment variable holding a command line:
  # kilix-nvr splits KILIX_NVR_DETECT on spaces with no quoting, and a path
  # with a space in it would silently become two arguments.
  cat > "$wrapper" <<EOF
#!/bin/sh
# Written by kilix install yolo. Re-run it to repoint this at a moved
# checkout; delete $yolo_dir to remove the runtime entirely.
exec "$python" "$tool" --model "$weights" "\$@"
EOF
  chmod 700 "$wrapper"
  record_setting
  log "installed"
}

# The launcher exports allowlisted KILIX_* keys from this file into every pane,
# which is how a detector installed once is found by every later session.
record_setting() {
  local config="${KILIX_USER_CONFIG_DIRECTORY:-$GPU_TERMINAL_DATA_HOME/kilix/config}"
  local env_file="$config/kilix.env"
  mkdir -p "$config"
  [ -f "$env_file" ] || : > "$env_file"
  local existing
  existing="$(grep -c '^KILIX_NVR_DETECT=' "$env_file" 2>/dev/null || true)"
  if [ "${existing:-0}" -gt 0 ]; then
    # Rewritten in place rather than appended: two assignments in one file is
    # a file whose meaning depends on read order.
    local temporary
    temporary="$(mktemp "$config/.kilix.env.XXXXXX")"
    grep -v '^KILIX_NVR_DETECT=' "$env_file" > "$temporary"
    printf 'KILIX_NVR_DETECT=%s\n' "$wrapper" >> "$temporary"
    mv -- "$temporary" "$env_file"
  else
    printf 'KILIX_NVR_DETECT=%s\n' "$wrapper" >> "$env_file"
  fi
  chmod 600 "$env_file"
  log "recorded KILIX_NVR_DETECT in $env_file"
}

remove_runtime() {
  [ -d "$yolo_dir" ] || { log "nothing to remove at $yolo_dir"; return 0; }
  rm -rf -- "$yolo_dir"
  log "removed $yolo_dir"
}

upgrade_runtime() {
  runtime_ready || die "nothing installed at $yolo_dir yet"
  log "upgrading ultralytics and torch"
  "$python" -m pip install --quiet --upgrade ultralytics torch torchvision >&2 \
    || die "the upgrade failed; the previous runtime is still in place"
  # The weights are pinned to a name rather than a version, so a newer
  # ultralytics can want a different file.  Fetch on demand rather than
  # assuming what is on disk is still what the library asks for.
  ( cd "$yolo_dir/models" && YOLO_CONFIG_DIR="$yolo_dir/config" \
    "$python" -c "from ultralytics import YOLO; YOLO('$KILIX_YOLO_MODEL')" >&2 ) \
    || die "the upgraded runtime cannot load $KILIX_YOLO_MODEL"
  log "upgraded"
}

case "$action" in
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
      log "already installed at $yolo_dir"
      printf '%s\n' "$wrapper"
      exit 0
    fi
    confirm || exit 1
    install_runtime
    printf '%s\n' "$wrapper"
    exit 0 ;;
  --print-path)
    if ! runtime_ready; then
      confirm || die "the YOLO runtime is not installed"
      install_runtime
    fi
    printf '%s\n' "$wrapper"
    exit 0 ;;
esac
