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
KILIX_SOUND_REF="${KILIX_SOUND_REF:-a492176c858038741735dda4f235c14df7bdbdba}"
export KILIX_HOME

die() { printf 'kilix yamnet: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix yamnet: %s\n' "$*" >&2; }

have_uv() { command -v "$KILIX_UV" >/dev/null 2>&1; }


usage() {
  cat <<'EOF'
usage: install-yamnet.sh [--print-path|--check|--install] [--yes]
       install-yamnet.sh --run [CLASSIFIER OPTIONS]

  --print-path  install if needed, then print the classifier command path
  --check       report what is present without changing anything
  --run         verify and run the installed provider; never install or select
  --install     install, asking first unless --yes; licence is never skipped
  --upgrade     rebuild using the provider's pinned packages
  --remove      delete the runtime directory

Environment:
  KILIX_YAMNET_DIR   where the self-contained runtime lives
  KILIX_YAMNET_MODEL existing matching model copy (catalog agreement still required)
  KILIX_YAMNET_WHEELHOUSE local wheel directory (optional)
  KILIX_SOUND_REF    exact provider commit (defaults to the selected pin)
  KILIX_SOUND_PYTHON compatible Python on PATH after relocation
  KILIX_UV           package installer; python -m pip when it is not found
EOF
}

action="--print-path"
assume_yes=0
while [ $# -gt 0 ]; do
  case "$1" in
    --run) action=--run; shift; break ;;
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
  local candidate root
  local sources="$GPU_TERMINAL_DATA_HOME/sources"
  local source_home="${GPU_TERMINAL_SOURCE_HOME:-$sources}"
  [[ "$KILIX_SOUND_REF" =~ ^[0-9a-f]{40}$ ]] || die "KILIX_SOUND_REF must be a full commit SHA"

  sound_candidate() {
    local directory="$1" tool="$2" top head status
    [ -f "$directory/tools/$tool" ] && [ ! -L "$directory/tools/$tool" ] || return 1
    # A stale sound checkout must not replace a working runtime with a wrapper
    # that predates the Content gate. Explicit source overrides remain pinned.
    grep -Fq 'print("kilix.sound-runtime/content-v1")' "$directory/tools/kilix_sound_runtime.py" 2>/dev/null || return 1
    top="$(git -C "$directory" rev-parse --show-toplevel 2>/dev/null)" || return 1
    [ "$(realpath -- "$top")" = "$(realpath -- "$directory")" ] || return 1
    head="$(git -C "$directory" rev-parse HEAD 2>/dev/null)" || return 1
    [ "$head" = "$KILIX_SOUND_REF" ] || return 1
    status="$(git -C "$directory" status --porcelain --untracked-files=normal)" || return 1
    [ -z "$status" ] || return 1
    printf '%s\n' "$(realpath -- "$directory/tools/$tool")"
  }

  for candidate in \
      "$source_home/kilix-modules/kilix-sound-detect" \
      "$source_home/kilix-apps/kilix-nvr/third_party/kilix-sound-detect" \
      "$source_home/kilix-modules/kilix-object-detect/third_party/kilix-sound-detect" \
      "$sources/kilix-modules/kilix-sound-detect" \
      "$sources/kilix-apps/kilix-nvr/third_party/kilix-sound-detect" \
      "$sources/kilix-modules/kilix-object-detect/third_party/kilix-sound-detect" \
      "$KILIX_HOME/../kilix-modules/kilix-sound-detect"; do
    if sound_candidate "$candidate" "$1"; then return 0; fi
  done
  root="$(catalog_root)" || return 1
  for candidate in \
      "$root/kilix-nvr/third_party/kilix-sound-detect" \
      "$root/kilix-object-detect/third_party/kilix-sound-detect"; do
    if sound_candidate "$candidate" "$1"; then return 0; fi
  done
  return 1
}

# Integrity is not an agreement. Both the host and the selected wrapper check
# the same Content authority; old wrappers without the capability are rebuilt.
runtime_capable() {
  [ -x "$wrapper" ] && [ -f "$yamnet_dir/runtime.json" ] || return 1
  grep -Fq 'print("kilix.sound-runtime/content-v1")' \
    "$yamnet_dir/lib/kilix_sound_runtime.py" 2>/dev/null
}

runtime_ready() {
  runtime_capable || return 1
  "$KILIX_HOME/kilix" models verify yamnet >/dev/null 2>&1 || return 1
  [ "$("$wrapper" --check-admission 2>/dev/null)" = "kilix.sound-runtime/content-v1" ]
}

catalog_root() {
  local root
  root="$("$KILIX_HOME/kilix" models show yamnet | \
    "$KILIX_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["root"])')" \
    || return 1
  [ -n "$root" ] || return 1
  printf '%s\n' "$root"
}

content_asset() {
  local root
  root="$(catalog_root)" || die "cannot resolve the selected YAMNet catalog asset"
  printf '%s\n' "$root/assets/yamnet/yamnet.tflite"
}

ensure_content() {
  if ! "$KILIX_HOME/kilix" models verify yamnet >/dev/null 2>&1; then
    { [ -t 0 ] && [ -t 2 ]; } || die "YAMNet needs verified model files and a licence receipt; run kilix models install yamnet in a terminal, then retry"
    log "YAMNet needs its model and agreement; the Content licence screen follows"
    "$KILIX_HOME/kilix" models install yamnet >&2 || die "the model was not installed"
    "$KILIX_HOME/kilix" models verify yamnet >/dev/null || die "YAMNet model or agreement verification failed"
  fi
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
  printf 'kilix yamnet: about 60 MB of packages; a verified catalog model and agreement are required. [y/N] ' >&2
  local reply=""
  read -r reply || true
  case "$reply" in y|Y|yes|YES) return 0 ;; *) log "not installing"; return 1 ;; esac
}

# -------------------------------------------------------------- install ----

install_runtime() {
  local interpreter assembler source_model
  local -a arguments
  interpreter="$(command -v "$KILIX_PYTHON" 2>/dev/null)" \
    || die "no $KILIX_PYTHON on PATH"
  interpreter="$(realpath -- "$interpreter")"
  assembler="$(sound_tool kilix-sound-runtime)" \
    || die "no clean sound provider at $KILIX_SOUND_REF; install the catalog Camera Recorder or Image Analyzer and retry"
  # The default bundle already installs the model through Content. Never fetch
  # a second copy or infer agreement from a bare file or --yes.
  ensure_content
  source_model="${KILIX_YAMNET_MODEL:-$(content_asset)}"
  [ -f "$source_model" ] || die "the selected YAMNet model file is missing"
  arguments=(--destination "$yamnet_dir" --model "$source_model" --installer "$KILIX_UV")
  if [ -n "${KILIX_YAMNET_WHEELHOUSE:-}" ]; then
    arguments+=(--wheelhouse "$KILIX_YAMNET_WHEELHOUSE")
  fi
  "$interpreter" "$assembler" "${arguments[@]}" >&2 || die "runtime assembly failed"
  runtime_ready || die "runtime or Content verification failed; check the compatible Python and model agreement"
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
  --run)
    # The guarded wrapper performs the actual integrity and Content checks
    # once before loading the model; avoid repeating them on the audio path.
    runtime_capable || die "sound runtime is missing or outdated; run kilix install yamnet"
    exec "$wrapper" "$@" ;;
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
