#!/usr/bin/env bash
# Prepare the YAMNet sound-event runtime kilix-listen and kilix-nvr hear with.
#
# The sibling of install-yolo.sh, and it exists for the same reason:
# kilix-sound-detect links no ML runtime, its classifier is a subprocess behind
# a fixed-size pipe, and the cost of that design is a fresh machine that can
# record sound and recognise none of it.
#
# It also exists because the thing it replaces was done by hand.  The setting
# was recorded as an interpreter and a script separated by a space, which the
# Kilix launcher parses correctly and every other reader does not: `set -a; .
# kilix.env` treats the first word as a variable assignment and RUNS the
# second, leaving the variable unset.  Anything started that way - a service,
# a plain shell - got no classifier, fell back to a bundled tool that is not
# installed, and reported a broken pipe seconds later with nothing in the log.
# So this writes a wrapper and records ONE path, exactly as the YOLO installer
# does, and for exactly the reason its comment gives.
#
# Nothing is installed system-wide and nothing is installed as root.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_DATA_HOME="${GPU_TERMINAL_DATA_HOME:-$HOME/.local/gpu_terminal}"
KILIX_YAMNET_DIR="${KILIX_YAMNET_DIR:-$GPU_TERMINAL_DATA_HOME/runtimes/yamnet}"
KILIX_YAMNET_ASSUME_YES="${KILIX_YAMNET_ASSUME_YES:-0}"
KILIX_PYTHON="${KILIX_PYTHON:-python3}"
KILIX_UV="${KILIX_UV:-uv}"

die() { printf 'kilix yamnet: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix yamnet: %s\n' "$*" >&2; }

have_uv() { command -v "$KILIX_UV" >/dev/null 2>&1; }


usage() {
  cat <<'EOF'
usage: install-yamnet.sh [--print-path|--check|--install] [--yes]

  --print-path  install if needed, then print the classifier command path
  --check       report what is present without changing anything
  --install     install, asking first unless --yes
  --upgrade     rebuild using the provider's pinned packages
  --remove      delete the runtime directory

Environment:
  KILIX_YAMNET_DIR   where the self-contained runtime lives
  KILIX_YAMNET_MODEL existing verified model (optional)
  KILIX_YAMNET_WHEELHOUSE local wheel directory (optional)
  KILIX_SOUND_PYTHON compatible Python on PATH after relocation
  KILIX_UV           package installer; python -m pip when it is not found
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
case "$KILIX_YAMNET_ASSUME_YES" in 1|yes|true|on) assume_yes=1 ;; esac
[ "$(id -u)" -ne 0 ] || die "run this as the desktop user, not root"

case "$KILIX_YAMNET_DIR" in
  /*) ;;
  *) die "KILIX_YAMNET_DIR must be an absolute path: $KILIX_YAMNET_DIR" ;;
esac
yamnet_dir="$(realpath -m -- "$KILIX_YAMNET_DIR" 2>/dev/null)" \
  || die "could not normalize KILIX_YAMNET_DIR=$KILIX_YAMNET_DIR"
case "$yamnet_dir" in
  /|"$HOME"|"$GPU_TERMINAL_DATA_HOME")
    die "refusing broad runtime path: $yamnet_dir" ;;
esac

[[ "$yamnet_dir" =~ ^/[a-zA-Z0-9_./-]+$ ]] \
  || die "runtime path is not safe for the single-word selection contract"

model="$yamnet_dir/models/yamnet.tflite"
wrapper="$yamnet_dir/bin/kilix-listen-classify"

# ---------------------------------------------------------------- state ----

# Where the classifier and the model fetcher come from.
#
# They ship with kilix-sound-detect, which is vendored into several things
# rather than installed on its own, so this looks in the places it actually
# lands rather than insisting on one.  First match wins, and a development
# tree is last so an installed copy is preferred over whatever happens to be
# checked out beside this script.
sound_tool() {
  local candidate
  local sources="$GPU_TERMINAL_DATA_HOME/sources"

  for candidate in \
      "$sources/kilix-modules/kilix-sound-detect" \
      "$sources/kilix-apps/kilix-nvr/third_party/kilix-sound-detect" \
      "$sources/kilix-modules/kilix-object-detect/third_party/kilix-sound-detect" \
      "$KILIX_HOME/../kilix-modules/kilix-sound-detect"; do
    if [ -f "$candidate/tools/$1" ]; then
      printf '%s\n' "$(realpath -- "$candidate/tools/$1")"
      return 0
    fi
  done
  return 1
}

runtime_ready() {
  [ -x "$wrapper" ] && [ -f "$yamnet_dir/runtime.json" ] \
    && "$wrapper" --check-runtime >/dev/null 2>&1
}

report() {
  printf 'runtime:    %s\n' "$yamnet_dir"
  if runtime_ready; then
    "$wrapper" --check-runtime
  else
    printf 'state:      not installed, missing or invalid; rebuild with --install\n'
  fi
}

confirm() {
  [ "$assume_yes" -eq 0 ] || return 0
  [ -t 0 ] || die "not a terminal; re-run with --yes to install unattended"
  # A tenth of what YOLO pulls down, and worth saying anyway: an install that
  # surprises somebody on a metered connection is an install done wrong.
  printf 'kilix yamnet: install the sound-event runtime into %s?\n' "$yamnet_dir" >&2
  printf 'kilix yamnet: about 60 MB of packages plus a 4 MB model. [y/N] ' >&2
  local reply=""
  read -r reply || true
  case "$reply" in y|Y|yes|YES) return 0 ;; *) log "not installing"; return 1 ;; esac
}

# -------------------------------------------------------------- install ----

install_runtime() {
  local interpreter assembler fetcher source_model
  local -a arguments
  interpreter="$(command -v "$KILIX_PYTHON" 2>/dev/null)" \
    || die "no $KILIX_PYTHON on PATH"
  interpreter="$(realpath -- "$interpreter")"
  assembler="$(sound_tool kilix-sound-runtime)" \
    || die "the sound provider needs kilix-sound-runtime; update that provider first"
  source_model="${KILIX_YAMNET_MODEL:-$model}"
  if [ ! -f "$source_model" ]; then
    fetcher="$(sound_tool kilix-sound-fetch-model)" || die "model fetcher unavailable"
    # Keep the existing acquisition path and its provenance, outside staging.
    KILIX_SOUND_HOME="$yamnet_dir.acquisition" "$interpreter" "$fetcher" >&2
    source_model="$yamnet_dir.acquisition/models/yamnet.tflite"
  fi
  arguments=(--destination "$yamnet_dir" --model "$source_model" --installer "$KILIX_UV")
  if [ -n "${KILIX_YAMNET_WHEELHOUSE:-}" ]; then
    arguments+=(--wheelhouse "$KILIX_YAMNET_WHEELHOUSE")
  fi
  "$interpreter" "$assembler" "${arguments[@]}" >&2 || die "runtime assembly failed"
  runtime_ready || die "runtime cannot launch with KILIX_SOUND_PYTHON (default python3)"
  record_setting
  log "installed verified runtime; any replaced tree remains in $yamnet_dir.previous"
}

# The launcher exports allowlisted KILIX_* keys from this file into every pane,
# which is how a classifier installed once is found by every later session.
record_setting() {
  local config="${KILIX_USER_CONFIG_DIRECTORY:-$GPU_TERMINAL_DATA_HOME/kilix/config}"
  local env_file="$config/kilix.env"
  mkdir -p "$config"
  [ -f "$env_file" ] || : > "$env_file"
  local temporary
  # Rewritten rather than appended: two assignments of one key in one file is
  # a file whose meaning depends on read order.  KILIX_NVR_LISTEN goes too -
  # it was this setting's name before the classifier moved into its own
  # module, and a stale line pointing at a deleted script is worse than none.
  temporary="$(mktemp "$config/.kilix.env.XXXXXX")"
  grep -v -e '^KILIX_SOUND_CLASSIFIER=' -e '^KILIX_NVR_LISTEN=' "$env_file" \
    > "$temporary" || true
  printf 'KILIX_SOUND_CLASSIFIER=%s\n' "$wrapper" >> "$temporary"
  mv -- "$temporary" "$env_file"
  chmod 600 "$env_file"
  log "recorded KILIX_SOUND_CLASSIFIER in $env_file"
}

remove_runtime() {
  [ -d "$yamnet_dir" ] || { log "nothing to remove at $yamnet_dir"; return 0; }
  rm -rf -- "$yamnet_dir"
  log "removed $yamnet_dir"
}

upgrade_runtime() {
  confirm || return 1
  install_runtime
}

case "$action" in
  --check)
    report
    runtime_ready || exit 1
    exit 0 ;;
  --upgrade)
    upgrade_runtime
    exit 0 ;;
  --remove)
    remove_runtime
    exit 0 ;;
  --install)
    if runtime_ready; then
      log "already installed at $yamnet_dir"
      # Recorded again anyway: re-running the install is how somebody repairs
      # a setting that has gone stale, and refusing to touch it because the
      # packages happen to be present makes that impossible.
      record_setting
      printf '%s\n' "$wrapper"
      exit 0
    fi
    confirm || exit 1
    install_runtime
    printf '%s\n' "$wrapper"
    exit 0 ;;
  --print-path)
    runtime_ready || { confirm || exit 1; install_runtime; }
    record_setting
    printf '%s\n' "$wrapper"
    exit 0 ;;
esac
