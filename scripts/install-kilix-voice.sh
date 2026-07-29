#!/usr/bin/env bash
# Install the exact Kilix Voice closure selected by this Kilix checkout.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_STATE_DIRECTORY="${KILIX_STATE_DIRECTORY:-$KILIX_STORAGE_HOME/state}"
KILIX_DATA_HOME="${KILIX_DATA_HOME:-$KILIX_STORAGE_HOME/data}"
KILIX_VOICE_PREFIX="${KILIX_VOICE_PREFIX:-$HOME/.local}"

# Kilix Voice is part of Kilix's source closure. Plebian-OS pins the parent
# Kilix commit, so every network-fetched voice input is transitive and immutable
# without adding another independently coordinated release ref.
#
# The repository is not published yet, so the commit, the prebuilt release tag,
# and that prebuilt's digest are the literal placeholder `unset`. A branch name
# would be the tempting default and is the wrong one: it installs whatever HEAD
# happened to be at install time, and it does so silently. Refusing to install
# is the louder and cheaper failure.
KILIX_VOICE_REPO="${KILIX_VOICE_REPO:-https://github.com/itsmygithubacct/kilix-voice.git}"
KILIX_VOICE_REF="${KILIX_VOICE_REF:-unset}"
KILIX_VOICE_LIB_VERSION="${KILIX_VOICE_LIB_VERSION:-unset}"
KILIX_VOICE_LIB_SHA256="${KILIX_VOICE_LIB_SHA256:-unset}"
KILIX_VOICE_LIB_URL="${KILIX_VOICE_LIB_URL:-${KILIX_VOICE_REPO%.git}/releases/download/$KILIX_VOICE_LIB_VERSION/libvosk.so}"
# The acoustic model is upstream's, published with no signature and no checksum
# file; this digest comes from two independent fetches a week apart that agreed.
# A mismatch therefore means upstream replaced the artifact in place, which is a
# finding rather than a reason to install it anyway.
KILIX_VOICE_MODEL_URL="${KILIX_VOICE_MODEL_URL:-https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip}"
KILIX_VOICE_MODEL_SHA256="${KILIX_VOICE_MODEL_SHA256:-30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498}"
# The catalog id is the shared vocabulary (KILIX_VOICE_STT_MODEL); the archive
# name is upstream's. Only the default model is installed here — the larger
# tiers are one-click downloads inside kilix-stt, which owns the full catalog.
model_id=small-en-us
model_archive_directory=vosk-model-small-en-us-0.15

die() { printf 'kilix voice: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix voice: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-kilix-voice.sh [--force] [--without-dictation] [--print-refs]

  --force              reinstall even when the verified closure is current
  --without-dictation  install read-aloud only, skipping libvosk and the model
  --print-refs         print the immutable source closure without changing anything
EOF
}

force=0
without_dictation=0
while [ $# -gt 0 ]; do
  case "$1" in
    --force) force=1; shift ;;
    --without-dictation) without_dictation=1; shift ;;
    --print-refs)
      # Deliberately before validation, so a release closure can read the pins
      # back while they are still placeholders.
      printf '%s\n' \
        "kilix-voice=$KILIX_VOICE_REF" \
        "libvosk=$KILIX_VOICE_LIB_VERSION" \
        "libvosk-sha256=$KILIX_VOICE_LIB_SHA256" \
        "model-$model_id=$KILIX_VOICE_MODEL_SHA256"
      exit 0 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[ "$(id -u)" -ne 0 ] || die "run this installer as the desktop user, not root"

[ "$KILIX_VOICE_REF" != unset ] \
  || die "KILIX_VOICE_REF is unset: kilix-voice has no published commit to pin yet"
[[ "$KILIX_VOICE_REF" =~ ^[0-9a-fA-F]{40}$ ]] \
  || die "KILIX_VOICE_REF must be a full 40-character commit SHA"
if [ "$without_dictation" = 0 ]; then
  { [ "$KILIX_VOICE_LIB_VERSION" != unset ] && [ "$KILIX_VOICE_LIB_SHA256" != unset ]; } \
    || die "KILIX_VOICE_LIB_VERSION and KILIX_VOICE_LIB_SHA256 are unset: kilix-voice has published no verified libvosk yet (--without-dictation installs read-aloud alone)"
  [[ "$KILIX_VOICE_LIB_VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] \
    || die "KILIX_VOICE_LIB_VERSION must be a plain release tag"
  [[ "$KILIX_VOICE_LIB_SHA256" =~ ^[0-9a-fA-F]{64}$ ]] \
    || die "KILIX_VOICE_LIB_SHA256 must be a full SHA-256 digest"
  [[ "$KILIX_VOICE_MODEL_SHA256" =~ ^[0-9a-fA-F]{64}$ ]] \
    || die "KILIX_VOICE_MODEL_SHA256 must be a full SHA-256 digest"
fi

normalize_absolute() {
  local value="$1" normalized
  case "$value" in /*) ;; *) return 1 ;; esac
  normalized="$(realpath -m -- "$value" 2>/dev/null)" || return 1
  [ "$normalized" = "$value" ] || return 1
  printf '%s\n' "$normalized"
}

source_home="$(normalize_absolute "$GPU_TERMINAL_SOURCE_HOME")" \
  || die "GPU_TERMINAL_SOURCE_HOME must be a normalized absolute path"
prefix="$(normalize_absolute "$KILIX_VOICE_PREFIX")" \
  || die "KILIX_VOICE_PREFIX must be a normalized absolute path"
state_dir="$(normalize_absolute "$KILIX_STATE_DIRECTORY")" \
  || die "KILIX_STATE_DIRECTORY must be a normalized absolute path"
data_dir="$(normalize_absolute "$KILIX_DATA_HOME")" \
  || die "KILIX_DATA_HOME must be a normalized absolute path"
case "$source_home" in /|"$HOME") die "refusing broad source root: $source_home" ;; esac
case "$prefix" in /|"$HOME") die "refusing broad install prefix: $prefix" ;; esac
case "$data_dir" in /|"$HOME") die "refusing broad data root: $data_dir" ;; esac
# The model and the library are generated inputs, so they land under the Kilix
# data root and never in the source checkout.
case "$data_dir" in "$KILIX_HOME"|"$KILIX_HOME"/*)
  die "refusing to place voice data inside the Kilix source checkout: $data_dir" ;;
esac

voice_data="$data_dir/voice"
library_root="$voice_data/lib"
models_root="$voice_data/models"
mkdir -p -- "$source_home" "$state_dir" "$library_root" "$models_root"
chmod 0700 -- "$state_dir" "$voice_data" "$library_root" "$models_root" 2>/dev/null || true
for protected in "$source_home" "$state_dir" "$voice_data"; do
  [ -d "$protected" ] && [ ! -L "$protected" ] \
    && [ "$(stat -c '%u' -- "$protected" 2>/dev/null)" = "$(id -u)" ] \
    || die "source/state/data directories must be real directories owned by the current user: $protected"
done
if command -v flock >/dev/null 2>&1; then
  exec 9>"$state_dir/kilix-voice-install.lock"
  flock 9
fi

# Keep installer-owned exact checkouts separate from editable sibling projects.
# Versioned paths make an update rollback-safe without resetting or replacing a
# developer's checkout, and an older closure remains available after rollback.
managed_sources="$source_home/.kilix-voice-sources"
mkdir -p -- "$managed_sources"
chmod 0700 -- "$managed_sources" 2>/dev/null || true
[ -d "$managed_sources" ] && [ ! -L "$managed_sources" ] \
  && [ "$(stat -c '%u:%a' -- "$managed_sources" 2>/dev/null)" = "$(id -u):700" ] \
  || die "managed source directory must be owned by the current user with mode 0700"
voice_dir="$managed_sources/kilix-voice-$KILIX_VOICE_REF"
library="$library_root/current/libvosk.so"
model="$models_root/$model_id"
stamp="$state_dir/kilix-voice-install.refs"
if [ "$without_dictation" = 1 ]; then
  library_pin=skipped
  model_pin=skipped
else
  library_pin="$KILIX_VOICE_LIB_VERSION+$KILIX_VOICE_LIB_SHA256"
  model_pin="$KILIX_VOICE_MODEL_SHA256"
fi
expected_refs="$(printf '%s\n' \
  "kilix-voice=$KILIX_VOICE_REF" \
  "libvosk=$library_pin" \
  "model-$model_id=$model_pin")"

voice_runtime_works() {
  local tool
  for tool in kilix-tts kilix-stt kilix-voiced; do
    [ -x "$prefix/bin/$tool" ] || return 1
  done
  [ "$without_dictation" = 0 ] || return 0
  [ -f "$library" ] && [ -d "$model" ]
}

if [ "$force" = 0 ] && [ -f "$stamp" ] \
     && printf '%s\n' "$expected_refs" | cmp -s - "$stamp" \
     && voice_runtime_works; then
  log "verified voice closure already installed at $prefix/bin"
  exit 0
fi

for command in git make python3 install; do
  command -v "$command" >/dev/null 2>&1 \
    || die "$command is required (install build-essential, git, and python3)"
done
if [ "$without_dictation" = 0 ]; then
  for command in curl sha256sum unzip; do
    command -v "$command" >/dev/null 2>&1 \
      || die "$command is required to install the pinned speech library and model"
  done
fi

clone_tmp=""
download_tmp=""
cleanup() {
  [ -z "$clone_tmp" ] || rm -rf -- "$clone_tmp"
  [ -z "$download_tmp" ] || rm -f -- "$download_tmp"
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

# Nothing unverified is ever kept: the download lands on a temporary path and is
# only moved into place once its digest matches the pin.
fetch_verified() {
  local url="$1" destination="$2" expected="$3" label="$4"
  download_tmp="$(mktemp "$destination.partial.XXXXXX")" \
    || die "could not allocate a download for $label"
  log "fetching verified $label"
  curl -fL --retry 3 --max-time 900 -o "$download_tmp" -- "$url" \
    || die "could not download $label from $url"
  printf '%s  %s\n' "$expected" "$download_tmp" | sha256sum -c --status \
    || die "checksum mismatch for $label; refusing to install an unverified download"
  chmod 0600 -- "$download_tmp" || die "could not secure the downloaded $label"
  mv -f -- "$download_tmp" "$destination" || die "could not publish $label"
  download_tmp=""
}

# Promotion is a symlink swap so an interrupted install leaves the previous
# generation live, and so a bad upgrade is one relink back rather than a refetch.
promote() {
  local target="$1" link="$2" staged
  staged="$(mktemp -u "$(dirname "$link")/.promote.XXXXXX")" \
    || die "could not allocate a promotion link for $link"
  ln -s -- "$target" "$staged" || die "could not stage $link"
  mv -fT -- "$staged" "$link" || { rm -f -- "$staged"; die "could not promote $link"; }
}

ensure_checkout "Kilix Voice" "$voice_dir" "$KILIX_VOICE_REPO" "$KILIX_VOICE_REF"

log "installing the pinned voice engine"
make -B -C "$voice_dir" install PREFIX="$prefix"
for tool in kilix-tts kilix-stt kilix-voiced; do
  [ -x "$prefix/bin/$tool" ] \
    || die "the voice engine did not install $prefix/bin/$tool"
done

if [ "$without_dictation" = 0 ]; then
  library_generation="$library_root/vosk-$KILIX_VOICE_LIB_VERSION"
  if [ "$force" = 1 ] || [ ! -f "$library_generation/libvosk.so" ]; then
    mkdir -p -- "$library_generation"
    chmod 0700 -- "$library_generation"
    fetch_verified "$KILIX_VOICE_LIB_URL" "$library_generation/libvosk.so" \
      "$KILIX_VOICE_LIB_SHA256" "libvosk.so $KILIX_VOICE_LIB_VERSION"
  fi
  promote "vosk-$KILIX_VOICE_LIB_VERSION" "$library_root/current"

  if [ "$force" = 1 ] || [ ! -d "$models_root/$model_archive_directory" ]; then
    archive="$models_root/.$model_archive_directory.zip"
    fetch_verified "$KILIX_VOICE_MODEL_URL" "$archive" \
      "$KILIX_VOICE_MODEL_SHA256" "the $model_id speech model"
    staging="$(mktemp -d "$models_root/.extract.XXXXXX")" \
      || die "could not allocate model staging"
    unzip -q -d "$staging" -- "$archive" \
      || { rm -rf -- "$staging" "$archive"; die "could not extract the speech model"; }
    [ -d "$staging/$model_archive_directory" ] \
      || { rm -rf -- "$staging" "$archive"
           die "the speech model archive did not contain $model_archive_directory"; }
    rm -rf -- "${models_root:?}/$model_archive_directory"
    mv -- "$staging/$model_archive_directory" "$models_root/$model_archive_directory" \
      || { rm -rf -- "$staging" "$archive"; die "could not publish the speech model"; }
    # The extracted model is the artifact worth keeping; the 41 MB archive is
    # re-fetchable and checksum-pinned, so it is not worth doubling the footprint.
    rm -rf -- "$staging" "$archive"
  fi
  # Catalog id -> archive directory, so switching tiers in kilix-stt is a relink
  # and the model it replaced stays on disk until the user removes it.
  promote "$model_archive_directory" "$model"
fi

stamp_tmp="$(mktemp "$state_dir/.kilix-voice-refs.XXXXXX")" \
  || die "could not create install stamp"
printf '%s\n' "$expected_refs" >"$stamp_tmp"
chmod 0600 "$stamp_tmp"
mv -fT -- "$stamp_tmp" "$stamp"
if [ "$without_dictation" = 1 ]; then
  log "installed read-aloud only; dictation stays unavailable until libvosk is pinned"
fi
log "installed and verified $prefix/bin/kilix-tts, $prefix/bin/kilix-stt"
