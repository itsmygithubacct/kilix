#!/usr/bin/env bash
# Build the forked kitty (clickable chrome) from ./src into per-user storage.
#
# The default build uses the host's signed package-manager dependencies and the
# pinned kitty source checkout directly.  An upstream CI dependency bundle may
# be supplied explicitly for release/offline builds, but its URL and SHA-256
# are both mandatory.  Kilix deliberately has no default for kitty's mutable
# /ci/ bundle URL.
set -euo pipefail
umask 077

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_CONFIG_HOME="${KILIX_CONFIG_HOME:-$KILIX_STORAGE_HOME/config}"
KILIX_CACHE_HOME="${KILIX_CACHE_HOME:-$KILIX_STORAGE_HOME/cache}"
KILIX_STATE_DIRECTORY="${KILIX_STATE_DIRECTORY:-$KILIX_STORAGE_HOME/state}"
KILIX_BUILD_DIRECTORY="${KILIX_BUILD_DIRECTORY:-$KILIX_STORAGE_HOME/build}"
KILIX_SYSDEPS_HOME="${KILIX_SYSDEPS_HOME:-$KILIX_STORAGE_HOME/dependencies/kitty-sysdeps}"
KILIX_BUILD_ENV="${KILIX_BUILD_ENV:-$KILIX_CONFIG_HOME/build.env}"
export GPU_TERMINAL_HOME KILIX_STORAGE_HOME KILIX_CONFIG_HOME KILIX_CACHE_HOME
export KILIX_STATE_DIRECTORY KILIX_BUILD_DIRECTORY KILIX_SYSDEPS_HOME

validate_private_storage_layout() {
  local storage private home source
  storage="$(realpath -m -- "$KILIX_STORAGE_HOME" 2>/dev/null)" || return 1
  home="$(realpath -m -- "$HOME" 2>/dev/null)" || return 1
  source="$(realpath -m -- "$KILIX_HOME" 2>/dev/null)" || return 1
  if [ "$storage" = / ] || [ "$storage" = "$home" ] \
       || [ "$storage" = "$source" ]; then
    echo "kilix: refusing broad or source-tree storage root: $storage" >&2
    return 1
  fi
  case "$storage" in "$source"/*)
    echo "kilix: refusing storage inside the Kilix source checkout" >&2
    return 1 ;;
  esac
  case "$source" in "$storage"/*)
    echo "kilix: refusing storage that contains the Kilix source checkout" >&2
    return 1 ;;
  esac
  for private in "$KILIX_CONFIG_HOME" "$KILIX_CACHE_HOME" \
                 "$KILIX_STATE_DIRECTORY" "$KILIX_BUILD_DIRECTORY" \
                 "$KILIX_SYSDEPS_HOME"; do
    private="$(realpath -m -- "$private" 2>/dev/null)" || return 1
    case "$private" in "$storage"/*) ;;
      *) echo "kilix: writable roots must be strict descendants of Kilix storage: $private" >&2
         return 1 ;;
    esac
  done
}

ensure_private_directory() {
  local path="$1" label="$2" owner mode
  if [ -e "$path" ] || [ -L "$path" ]; then
    if [ ! -d "$path" ] || [ -L "$path" ]; then
      echo "kilix: refusing unsafe $label directory: $path" >&2
      return 1
    fi
  else
    mkdir -p -- "$path" || return 1
  fi
  owner="$(stat -c '%u' -- "$path" 2>/dev/null)" || return 1
  [ "$owner" = "$(id -u)" ] || {
    echo "kilix: $label directory is not owned by the current user: $path" >&2
    return 1
  }
  chmod 0700 -- "$path" || return 1
  mode="$(stat -c '%a' -- "$path" 2>/dev/null)" || return 1
  [ "$mode" = 700 ] || {
    echo "kilix: $label directory is not mode 0700: $path" >&2
    return 1
  }
}

if [ -f "$KILIX_BUILD_ENV" ]; then
  # shellcheck disable=SC1091
  # shellcheck disable=SC1090
  . "$KILIX_BUILD_ENV"
fi
validate_private_storage_layout
ensure_private_directory "$KILIX_STORAGE_HOME" storage
ensure_private_directory "$KILIX_CONFIG_HOME" config
ensure_private_directory "$KILIX_CACHE_HOME" cache
ensure_private_directory "$KILIX_STATE_DIRECTORY" state
ensure_private_directory "$KILIX_BUILD_DIRECTORY" build
ensure_private_directory "$(dirname "$KILIX_SYSDEPS_HOME")" dependencies
validate_private_storage_layout
if [ -d "$KILIX_SYSDEPS_HOME/usr" ]; then
  _sysdeps_lib="$KILIX_SYSDEPS_HOME/usr/lib/x86_64-linux-gnu"
  export PATH="$KILIX_SYSDEPS_HOME/usr/bin:$PATH"
  export PKG_CONFIG_PATH="$_sysdeps_lib/pkgconfig:$KILIX_SYSDEPS_HOME/usr/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
  export LIBRARY_PATH="$_sysdeps_lib:$KILIX_SYSDEPS_HOME/usr/lib:${LIBRARY_PATH:-}"
fi

acquire_transaction_lock() {
  local lock_root lock_path fd fd_path path_identity fd_identity lock_was_present=0
  if ! command -v flock >/dev/null 2>&1; then
    echo "kilix: flock not found; cannot serialize build/update" >&2
    return 1
  fi
  ensure_private_directory "$KILIX_STATE_DIRECTORY" state || return 1
  lock_root="$(cd "$KILIX_STATE_DIRECTORY" && pwd -P)" || return 1
  lock_path="$lock_root/build-update.lock"
  if [ -e "$lock_path" ] || [ -L "$lock_path" ]; then
    lock_was_present=1
    if [ ! -f "$lock_path" ] || [ -L "$lock_path" ] \
         || [ "$(stat -c '%u:%a:%h' -- "$lock_path" 2>/dev/null)" \
              != "$(id -u):600:1" ]; then
      echo "kilix: refusing unsafe transaction lock: $lock_path" >&2
      return 1
    fi
  fi
  if [ -n "${KILIX_TRANSACTION_LOCK_FD:-}" ]; then
    fd="$KILIX_TRANSACTION_LOCK_FD"
    case "$fd" in ''|*[!0-9]*)
      echo "kilix: invalid inherited KILIX_TRANSACTION_LOCK_FD" >&2
      return 1 ;;
    esac
    fd_path="/proc/$$/fd/$fd"
    [ -e "$fd_path" ] || {
      echo "kilix: inherited transaction-lock FD is not open" >&2
      return 1
    }
    [ "$lock_was_present" = 1 ] || {
      echo "kilix: canonical transaction lock does not exist" >&2
      return 1
    }
  else
    exec {_kilix_transaction_lock_fd}>"$lock_path" || return 1
    fd="$_kilix_transaction_lock_fd"
    KILIX_TRANSACTION_LOCK_FD="$fd"
    export KILIX_TRANSACTION_LOCK_FD
    fd_path="/proc/$$/fd/$fd"
  fi
  [ "$lock_was_present" = 1 ] || chmod 0600 "$lock_path" 2>/dev/null || return 1
  if [ ! -f "$lock_path" ] || [ -L "$lock_path" ] \
       || [ "$(stat -c '%u:%a:%h' -- "$lock_path" 2>/dev/null)" \
            != "$(id -u):600:1" ]; then
    echo "kilix: transaction lock is not a private regular file" >&2
    return 1
  fi
  path_identity="$(stat -c '%d:%i' -- "$lock_path" 2>/dev/null)" || return 1
  fd_identity="$(stat -Lc '%d:%i' -- "$fd_path" 2>/dev/null)" || return 1
  if [ "$fd_identity" != "$path_identity" ]; then
    echo "kilix: inherited transaction-lock FD points to the wrong file" >&2
    return 1
  fi
  flock -x "$fd" || return 1
  KILIX_TRANSACTION_LOCK_PATH="$lock_path"
  export KILIX_TRANSACTION_LOCK_PATH
}

acquire_transaction_lock
command -v timeout >/dev/null 2>&1 \
  || { echo "kilix: timeout not found; cannot verify built launchers" >&2; exit 1; }

case "$(uname -s):$(uname -m)" in
  Linux:x86_64|Linux:amd64) ;;
  Linux:*)
    echo "kilix: fork builds currently support Linux x86_64 only (this is $(uname -m))" >&2
    exit 1 ;;
  *)
    echo "kilix: fork builds currently support Linux x86_64 only" >&2
    exit 1 ;;
esac

if [ ! -f "$KILIX_HOME/src/go.mod" ] || [ ! -f "$KILIX_HOME/src/setup.py" ]; then
  echo "kilix: ./src is missing kitty sources" >&2
  exit 1
fi

# Force the exact toolchain declared by the pinned fork. Go downloads named
# toolchains through its checksum-verified module mechanism when necessary.
_go_toolchain="$(awk '/^toolchain go[0-9]/{print $2; exit}' "$KILIX_HOME/src/go.mod")"
_go_toolchain="${KILIX_GO_TOOLCHAIN:-${_go_toolchain:-go1.26.4}}"
[[ "$_go_toolchain" =~ ^go[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] \
  || { echo "kilix: invalid exact KILIX_GO_TOOLCHAIN=$_go_toolchain" >&2; exit 2; }
export GOTOOLCHAIN="$_go_toolchain"

# Go's default package parallelism follows GOMAXPROCS. Large generated packages
# in the pinned kitty fork can require well over 1 GiB per compiler process, so
# an unconstrained build can OOM an otherwise supported small installation.
# Default to one package compiler at a time; operators with more memory can opt
# into higher parallelism explicitly.
_build_jobs="${KILIX_BUILD_JOBS:-${GOMAXPROCS:-1}}"
case "$_build_jobs" in
  ''|*[!0-9]*|0) echo "kilix: invalid KILIX_BUILD_JOBS/GOMAXPROCS=$_build_jobs (expected a positive integer)" >&2; exit 2 ;;
esac
export GOMAXPROCS="$_build_jobs"
# The build snapshot intentionally has no .git directory. Newer Go toolchains
# otherwise walk above it and fail while trying to stamp unrelated VCS state.
export GOFLAGS="${GOFLAGS:+$GOFLAGS }-buildvcs=false"

CACHE_DIR="$KILIX_CACHE_HOME/build"
STATE_DIR="$KILIX_STATE_DIRECTORY"
export GOCACHE="${GOCACHE:-$CACHE_DIR/go-build}"
export GOMODCACHE="${GOMODCACHE:-$CACHE_DIR/go-mod}"
font_archive="$CACHE_DIR/NerdFontsSymbolsOnly-v3.4.0.tar.xz"
font_url="${KILIX_NERD_FONT_URL:-https://github.com/ryanoasis/nerd-fonts/releases/download/v3.4.0/NerdFontsSymbolsOnly.tar.xz}"
font_sha="${KILIX_NERD_FONT_SHA256:-7f8c090da3b0eaa7108646bf34cbbb6ed13d5358a72460522108b06c7ecd716a}"
font_file_sha="${KILIX_NERD_FONT_FILE_SHA256:-f0f624d9b474bea1662cf7e862d44aebe1ae1f6c7f9cb7a0ca5d0e5ac9561c60}"
font_notice_source="$KILIX_HOME/third_party/nerd-fonts/LICENSE"
font_provenance_source="$KILIX_HOME/third_party/nerd-fonts/PROVENANCE"
font_notice_sha="bede0739eb2bf948765623a7a134360a6320240f4a9e29a5a68f31e191b0f8d0"

verify_file() {
  local path="$1" expected="$2" label="$3"
  [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] \
    || { echo "kilix: invalid SHA-256 for $label" >&2; return 1; }
  [ -f "$path" ] || { echo "kilix: missing $label: $path" >&2; return 1; }
  command -v sha256sum >/dev/null 2>&1 \
    || { echo "kilix: sha256sum is required to verify $label" >&2; return 1; }
  printf '%s  %s\n' "$expected" "$path" | sha256sum -c --status \
    || { echo "kilix: checksum mismatch for $label" >&2; return 1; }
}

fetch_verified() {
  local url="$1" path="$2" expected="$3" label="$4" tmp
  [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] \
    || { echo "kilix: invalid SHA-256 for $label" >&2; return 1; }
  mkdir -p "$(dirname "$path")"
  if [ -f "$path" ] && verify_file "$path" "$expected" "$label"; then
    return 0
  fi
  [ ! -e "$path" ] || {
    echo "kilix: replacing stale/corrupt cached $label" >&2
    rm -f "$path"
  }
  command -v curl >/dev/null 2>&1 \
    || { echo "kilix: curl is required to fetch $label" >&2; return 1; }
  tmp="$(mktemp "${path}.partial.XXXXXX")"
  echo "kilix: fetching verified $label"
  if ! curl -fL --retry 3 --max-time 300 -o "$tmp" "$url"; then
    rm -f "$tmp"
    return 1
  fi
  if ! verify_file "$tmp" "$expected" "$label"; then
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$path"
}

stage_font_notices() {
  local font_dir license_target provenance_target source tmpfile
  font_dir="$(dirname "$font_file")"
  license_target="$font_dir/SymbolsNerdFontMono-LICENSE.txt"
  provenance_target="$font_dir/SymbolsNerdFontMono-PROVENANCE.txt"
  for source in "$font_notice_source" "$font_provenance_source"; do
    if [ ! -f "$source" ] || [ -L "$source" ]; then
      echo "kilix: missing or unsafe Nerd Font notice: $source" >&2
      return 1
    fi
  done
  verify_file "$font_notice_source" "$font_notice_sha" \
    "Nerd Font license"
  mkdir -p "$font_dir"
  install -m 0644 -- "$font_notice_source" "$license_target"
  tmpfile="$(mktemp "$font_dir/.font-provenance.partial.XXXXXX")"
  {
    cat "$font_provenance_source"
    printf '\nEffective build input:\n'
    printf 'Source URL: %s\n' "$font_url"
    printf 'Archive SHA-256: %s\n' "${font_sha,,}"
    printf 'Extracted font SHA-256: %s\n' "${font_file_sha,,}"
  } >"$tmpfile"
  chmod 0644 "$tmpfile"
  mv -f -- "$tmpfile" "$provenance_target"
}

# The notices are a redistribution obligation of the bundled font, not a build
# convenience: OFL 1.1 requires the license to travel with every copy. Staging
# them is therefore not enough — anything that runs between preparation and
# promotion (the engine's own build regenerates parts of this tree) could drop
# them, and the generation would ship a font with no terms attached. Refuse to
# promote such a generation, and check the artifact rather than the source.
assert_font_notices_installed() {
  local font_dir license_target provenance_target target field
  font_dir="$(dirname "$font_file")"
  license_target="$font_dir/SymbolsNerdFontMono-LICENSE.txt"
  provenance_target="$font_dir/SymbolsNerdFontMono-PROVENANCE.txt"
  if [ ! -f "$font_file" ] || [ -L "$font_file" ]; then
    echo "kilix: generation has no bundled font to publish: $font_file" >&2
    return 1
  fi
  for target in "$license_target" "$provenance_target"; do
    if [ ! -f "$target" ] || [ -L "$target" ]; then
      echo "kilix: refusing to publish the bundled font without its notice: $target" >&2
      return 1
    fi
  done
  verify_file "$license_target" "$font_notice_sha" \
    "published Nerd Font license" || return 1
  # Provenance that does not name where the font came from, which release it
  # came from, and what this build actually fetched is not provenance.
  for field in "Upstream repository" "Upstream release" "Source URL"; do
    if ! grep -q "^$field: ." "$provenance_target"; then
      echo "kilix: published font provenance does not record its $field" >&2
      return 1
    fi
  done
}

prepare_font() {
  local tmpdir extracted tmpfile
  if verify_file "$font_file" "$font_file_sha" "extracted Nerd Font" 2>/dev/null; then
    stage_font_notices
    return
  fi
  fetch_verified "$font_url" "$font_archive" "$font_sha" "Nerd Font archive"
  mkdir -p "$(dirname "$font_file")"
  tmpdir="$(mktemp -d "$BUILD_SRC/fonts/.extract.XXXXXX")"
  if ! tar -xf "$font_archive" -C "$tmpdir" SymbolsNerdFontMono-Regular.ttf; then
    rm -rf "$tmpdir"
    return 1
  fi
  extracted="$tmpdir/SymbolsNerdFontMono-Regular.ttf"
  verify_file "$extracted" "$font_file_sha" "extracted Nerd Font" \
    || { rm -rf "$tmpdir"; return 1; }
  tmpfile="$(mktemp "$(dirname "$font_file")/.font.partial.XXXXXX")"
  rm -f "$tmpfile"
  mv "$extracted" "$tmpfile"
  mv "$tmpfile" "$font_file"
  rm -rf "$tmpdir"
  stage_font_notices
}

prepare_dependency_bundle() {
  local url="$1" expected="$2" archive deps_root stamp wanted tmp old
  case "$url" in
    http://download.calibre-ebook.com/ci/kitty/*|https://download.calibre-ebook.com/ci/kitty/*)
      echo "kilix: refusing mutable kitty CI dependency URL: $url" >&2
      echo "kilix: use an immutable/versioned artifact with its SHA-256" >&2
      return 1 ;;
  esac
  [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] \
    || { echo "kilix: bundle mode requires KILIX_KITTY_DEPS_SHA256" >&2; return 2; }
  archive="$CACHE_DIR/kitty-dependencies-${expected,,}.tar.xz"
  deps_root="$BUILD_SRC/dependencies/linux-amd64"
  stamp="$deps_root/.kilix-prepared-bundle"
  wanted="$deps_root"$'\t'"${expected,,}"
  fetch_verified "$url" "$archive" "$expected" "kitty dependency bundle"
  if [ -x "$deps_root/bin/python" ] \
       && [ "$(cat "$stamp" 2>/dev/null || true)" = "$wanted" ]; then
    return 0
  fi

  mkdir -p "$BUILD_SRC/dependencies"
  tmp="$(mktemp -d "$BUILD_SRC/dependencies/.extract.XXXXXX")"
  # Reject lexical traversal before invoking tar. The verified upstream bundle
  # legitimately contains symlinks, so extraction otherwise follows upstream.
  if tar -tf "$archive" | awk '
      /(^|\/)\.\.($|\/)/ || /^\// { bad=1 }
      END { exit bad ? 1 : 0 }
    '; then :; else
    echo "kilix: unsafe path in kitty dependency bundle" >&2
    rm -rf "$tmp"
    return 1
  fi
  if ! tar -xf "$archive" -C "$tmp"; then
    rm -rf "$tmp"
    return 1
  fi

  # Mirror bypy/devenv.go's Linux preparation: relocate every pkg-config and
  # Python sysconfig reference from the CI prefix, and force system fontconfig.
  python3 - "$tmp" "$deps_root" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
new_prefix = sys.argv[2].encode()
old_prefix = b"/sw/sw"
for path in root.rglob("*"):
    if path.is_file() and (path.suffix == ".pc" or path.name.startswith("_sysconfigdata_")):
        data = path.read_bytes().replace(old_prefix, new_prefix)
        path.write_bytes(data)
        path.chmod(0o644)
for path in (root / "lib").glob("libfontconfig.so*"):
    path.unlink()
remaining = []
for path in root.rglob("*"):
    if path.is_file() and (path.suffix == ".pc" or path.name.startswith("_sysconfigdata_")):
        if old_prefix in path.read_bytes():
            remaining.append(str(path))
if remaining:
    raise SystemExit("unrelocated dependency metadata: " + ", ".join(remaining[:3]))
PY
  [ -x "$tmp/bin/python" ] \
    || { echo "kilix: dependency bundle has no bin/python" >&2; rm -rf "$tmp"; return 1; }
  printf '%s\n' "$wanted" >"$tmp/.kilix-prepared-bundle"

  old="$deps_root.old.$$"
  [ ! -e "$old" ] || rm -rf "$old"
  [ ! -e "$deps_root" ] || mv "$deps_root" "$old"
  if ! mv "$tmp" "$deps_root"; then
    [ ! -e "$old" ] || mv "$old" "$deps_root"
    return 1
  fi
  rm -rf "$old"
}

mode="${KILIX_BUILD_MODE:-}"
if [ -z "$mode" ]; then
  if [ -n "${KILIX_KITTY_DEPS_URL:-}${KILIX_KITTY_DEPS_SHA256:-}" ]; then
    mode=bundle
  else
    mode=system
  fi
fi
case "$mode" in
  system)
    [ -z "${KILIX_KITTY_DEPS_URL:-}${KILIX_KITTY_DEPS_SHA256:-}" ] \
      || { echo "kilix: dependency bundle variables require KILIX_BUILD_MODE=bundle" >&2; exit 2; } ;;
  bundle)
    [ -n "${KILIX_KITTY_DEPS_URL:-}" ] \
      || { echo "kilix: bundle mode requires KILIX_KITTY_DEPS_URL" >&2; exit 2; } ;;
  *) echo "kilix: invalid KILIX_BUILD_MODE=$mode (use system or bundle)" >&2; exit 2 ;;
esac

select_system_python() {
  local candidate version
  local -a candidates
  if [ -n "${KILIX_PYTHON:-}" ]; then
    candidates=("$KILIX_PYTHON")
  else
    candidates=(python3.14 python3.13 python3.12 python3)
  fi
  for candidate in "${candidates[@]}"; do
    if [[ "$candidate" == */* ]]; then
      [ -x "$candidate" ] || continue
    else
      candidate="$(command -v "$candidate" 2>/dev/null || true)"
      [ -n "$candidate" ] || continue
    fi
    version="$("$candidate" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || true)"
    if [ -n "$version" ] && [ "$(printf '%s\n%s\n' "$version" 3.12 | sort -V | head -1)" = 3.12 ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  echo "kilix: current kitty source requires Python >= 3.12 to build" >&2
  echo "kilix: install a newer Python or set KILIX_PYTHON=/path/to/python3.12+" >&2
  return 1
}

_python=""
if [ "$mode" = system ]; then
  _python="$(select_system_python)"
fi

# Upstream kitty writes launchers, extensions, generated protocols and Go
# objects beside its sources. Build a tracked-file snapshot outside the
# checkout so ./src remains physically pristine after every build.
src_head=""
if git -C "$KILIX_HOME/src" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  src_head="$(git -C "$KILIX_HOME/src" rev-parse --verify HEAD)"
  if [ -n "$(git -C "$KILIX_HOME/src" status --porcelain=v1 \
      --untracked-files=all --ignore-submodules=none)" ]; then
    echo "kilix: refusing to build from a modified ./src checkout" >&2
    echo "kilix: commit/stash the kitty fork changes so source-id names exact bytes" >&2
    exit 1
  fi
fi
ensure_private_directory "$CACHE_DIR" build-cache
ensure_private_directory "$STATE_DIR" state
ensure_private_directory "$KILIX_BUILD_DIRECTORY" build
ensure_private_directory "$KILIX_BUILD_DIRECTORY/generations" generations
stage="$(mktemp -d "$KILIX_BUILD_DIRECTORY/generations/build.XXXXXX")"
stamp_tmp=""
cleanup_stage() {
  local rc=$?
  trap - EXIT
  [ -z "${stage:-}" ] || rm -rf -- "$stage"
  [ -z "${stamp_tmp:-}" ] || rm -f -- "$stamp_tmp"
  exit "$rc"
}
trap cleanup_stage EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
BUILD_SRC="$stage/src"
mkdir -p "$BUILD_SRC"
if [ -n "$src_head" ]; then
  git -C "$KILIX_HOME/src" archive --format=tar "$src_head" \
    | tar -C "$BUILD_SRC" -xf -
else
  # Packaging/test source trees need not contain Git metadata.
  cp -a "$KILIX_HOME/src/." "$BUILD_SRC/"
fi
source_id="$src_head"
if [ -z "$source_id" ]; then
  source_id="tree-sha256:$(python3 - "$BUILD_SRC" <<'PY'
import hashlib
import os
import stat
import sys

root = os.path.abspath(sys.argv[1])
digest = hashlib.sha256()
for parent, dirs, files in os.walk(root):
    dirs.sort()
    files.sort()
    for name in files:
        path = os.path.join(parent, name)
        rel = os.path.relpath(path, root).encode("utf-8", "surrogateescape")
        info = os.lstat(path)
        digest.update(rel + b"\0" + oct(stat.S_IMODE(info.st_mode)).encode() + b"\0")
        if stat.S_ISLNK(info.st_mode):
            digest.update(os.readlink(path).encode("utf-8", "surrogateescape"))
        else:
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        digest.update(b"\0")
print(digest.hexdigest())
PY
)"
fi
font_file="$BUILD_SRC/fonts/SymbolsNerdFontMono-Regular.ttf"

generation_target_syntax_is_safe() {
  local target="$1" suffix
  case "$target" in generations/build.*) ;; *) return 1 ;; esac
  suffix="${target#generations/build.}"
  case "$suffix" in ''|*[!A-Za-z0-9]*) return 1 ;; esac
}

# Every reference the collector must honor before reclaiming a generation.
#
# Besides the engine's own entries (current/previous/prepared and the
# .current./.prepared./.previous. temporaries a promotion moves through), an
# outside updater that runs `kilix --build` inside its own transaction needs
# somewhere to park the outgoing generation. Its rollback restores that entry,
# so a collector that reclaims the parked target turns a failed update into a
# `previous` pointing at a deleted directory — and every later update refuses to
# start against it. The parking contract is therefore part of this function:
#
#   $KILIX_BUILD_DIRECTORY/.update-rollback.<suffix>/<name>.entry
#
# a private directory named `.update-rollback.*` holding generation symlinks
# named `*.entry`. It is deliberately shaped so the engine never has to learn
# the name of any individual updater. Pleb (lib/update.sh) and Plebian-OS
# (provision/plebian-os-update.sh) park there, and pin the same literal shape in
# their own suites; tests/test_build_behavior.py and tests/test_update_behavior.py
# pin this side of it.
#
# `.pleb-update.*/previous` and `.plebian-os-update.*/previous` are the shapes
# those two updaters used up to 0.1.8, before they moved onto the contract
# above. Honor them so an installation still carrying a pre-fix updater survives
# the very update that replaces it; nothing writes them any more.
generation_target_is_referenced() {
  local target="$1" ref
  for ref in "$KILIX_BUILD_DIRECTORY/current" \
             "$KILIX_BUILD_DIRECTORY/previous" \
             "$KILIX_BUILD_DIRECTORY/prepared" \
             "$KILIX_BUILD_DIRECTORY"/.current.* \
             "$KILIX_BUILD_DIRECTORY"/.prepared.* \
             "$KILIX_BUILD_DIRECTORY"/.previous.* \
             "$KILIX_BUILD_DIRECTORY"/.update-rollback.*/*.entry \
             "$KILIX_BUILD_DIRECTORY"/.pleb-update.*/previous \
             "$KILIX_BUILD_DIRECTORY"/.plebian-os-update.*/previous; do
    if [ -L "$ref" ] && [ "$(readlink -- "$ref" 2>/dev/null || true)" = "$target" ]; then
      return 0
    fi
  done
  return 1
}

generation_has_live_executable() {
  local candidate="$1" proc_exe executable inspected=0
  # /proc is the runtime lease registry for build generations. If it is not
  # available (for example in a restricted container), retain the generation:
  # reclaiming disk is less important than invalidating a running terminal.
  [ -d /proc ] || return 0
  for proc_exe in /proc/[0-9]*/exe; do
    [ -L "$proc_exe" ] || continue
    executable="$(readlink -f -- "$proc_exe" 2>/dev/null || true)"
    [ -n "$executable" ] || continue
    inspected=1
    case "$executable" in
      "$candidate"|"$candidate"/*) return 0 ;;
    esac
  done
  [ "$inspected" = 1 ] || return 0
  return 1
}

retire_build_entry() {
  local entry="$1"
  if [ -L "$entry" ]; then
    rm -f -- "$entry" || return 1
  elif [ -e "$entry" ]; then
    rm -rf -- "$entry" || return 1
  fi
}

garbage_collect_generations() {
  local candidate target build_root candidate_root
  build_root="$(cd "$KILIX_BUILD_DIRECTORY" && pwd -P)" || return 1
  for candidate in "$KILIX_BUILD_DIRECTORY"/generations/build.*; do
    if [ ! -d "$candidate" ] || [ -L "$candidate" ]; then
      continue
    fi
    target="generations/${candidate##*/}"
    generation_target_syntax_is_safe "$target" || continue
    generation_target_is_referenced "$target" && continue
    candidate_root="$(cd "$candidate" && pwd -P)" || continue
    [ "$candidate_root" = "$build_root/$target" ] || continue
    if generation_has_live_executable "$candidate_root"; then
      echo "kilix: retaining live build generation: $candidate_root" >&2
      continue
    fi
    rm -rf -- "$candidate" || return 1
  done
}

promote_current() {
  local current="$KILIX_BUILD_DIRECTORY/current"
  local old="$KILIX_BUILD_DIRECTORY/previous"
  local parked="$KILIX_BUILD_DIRECTORY/.previous.$$"
  local target link_tmp promotion_ok=1 rollback_ok=1
  local previous_parked=0 current_moved=0 new_installed=0
  if [ -e "$parked" ] || [ -L "$parked" ]; then
    echo "kilix: refusing stale previous-generation transaction: $parked" >&2
    return 1
  fi
  # Do not let handled termination signals split the short generation+stamp
  # commit. Failures below restore current explicitly; SIGKILL remains outside
  # the guarantees of a shell transaction.
  trap '' HUP INT TERM
  if [ -e "$old" ] || [ -L "$old" ]; then
    if mv -- "$old" "$parked"; then
      previous_parked=1
    else
      promotion_ok=0
    fi
  fi
  if [ "$promotion_ok" = 1 ] && { [ -e "$current" ] || [ -L "$current" ]; }; then
    if mv -- "$current" "$old"; then
      current_moved=1
    else
      promotion_ok=0
    fi
  fi
  target="generations/${stage##*/}"
  link_tmp="$KILIX_BUILD_DIRECTORY/.current.$$"
  if [ "$promotion_ok" = 1 ]; then
    if ln -s -- "$target" "$link_tmp" \
         && mv -Tf -- "$link_tmp" "$current"; then
      new_installed=1
    else
      rm -f -- "$link_tmp" || rollback_ok=0
      promotion_ok=0
    fi
  fi
  if [ "$promotion_ok" = 1 ] && [ -n "$stamp_tmp" ]; then
    if ! mv -Tf -- "$stamp_tmp" "$STATE_DIR/fork-built-ref"; then
      promotion_ok=0
    else
      stamp_tmp=""
    fi
  fi
  if [ "$promotion_ok" = 1 ] && [ -z "$head" ]; then
    if [ -d "$STATE_DIR/fork-built-ref" ] \
         && [ ! -L "$STATE_DIR/fork-built-ref" ]; then
      promotion_ok=0
    elif ! rm -f -- "$STATE_DIR/fork-built-ref"; then
      promotion_ok=0
    fi
  fi
  if [ "$promotion_ok" = 1 ]; then
    stage=""
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    if [ "$previous_parked" = 1 ] \
         && ! retire_build_entry "$parked"; then
      echo "kilix: WARNING: committed build but old previous generation remains at $parked" >&2
    fi
    return 0
  fi

  if [ "$new_installed" = 1 ]; then
    if ! rm -f -- "$current"; then
      # Keep the generation alive if its live link could not be removed.
      stage=""
      rollback_ok=0
    fi
  fi
  if [ "$current_moved" = 1 ] && ! mv -- "$old" "$current"; then
    rollback_ok=0
  fi
  if [ "$previous_parked" = 1 ] && ! mv -- "$parked" "$old"; then
    rollback_ok=0
  fi
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  [ "$rollback_ok" = 1 ] \
    || echo "kilix: promotion rollback was incomplete" >&2
  return 1
}

install_terminfo() {
  # Every Kilix pane advertises TERM=xterm-kitty, and nothing else on a
  # provisioned machine installs that terminfo entry — so every strict
  # ncurses program reported an unknown terminal until someone exported
  # TERM=linux by hand (0.1.7 review). The engine tree ships the compiled
  # entry; promotion is the moment it becomes the user's. Installing into
  # ~/.terminfo is part of the promotion transaction: when the tree carries
  # terminfo and it cannot be installed, the engine is not promoted.
  local source_dir="$BUILD_SRC/terminfo" target="$HOME/.terminfo"
  local subdir entry installed=0
  if [ ! -d "$source_dir" ]; then
    # A minimal or fixture tree; there is nothing to install from.
    echo "kilix: WARNING: built tree ships no terminfo directory: $source_dir" >&2
    return 0
  fi
  # kitty ships the entry precompiled, under both the first-letter and the
  # hex-coded directory ncurses variants look in.
  for subdir in x 78; do
    entry="$source_dir/$subdir/xterm-kitty"
    [ -f "$entry" ] && [ ! -L "$entry" ] || continue
    mkdir -p -- "$target/$subdir" || return 1
    install -m 0644 -- "$entry" "$target/$subdir/xterm-kitty" || return 1
    installed=1
  done
  if [ "$installed" = 1 ]; then
    echo "kilix: installed xterm-kitty terminfo -> $target"
    return 0
  fi
  # No compiled entry in the tree: compile the shipped source.
  if [ ! -f "$source_dir/kitty.terminfo" ]; then
    echo "kilix: WARNING: built tree ships neither compiled nor source terminfo" >&2
    return 0
  fi
  command -v tic >/dev/null 2>&1 || {
    echo "kilix: tic is required to compile the engine's terminfo (install ncurses-bin)" >&2
    return 1
  }
  mkdir -p -- "$target" || return 1
  tic -x -o "$target" "$source_dir/kitty.terminfo" || return 1
  echo "kilix: compiled xterm-kitty terminfo -> $target"
}

promote_prepared() {
  local target link_tmp
  retire_build_entry "$KILIX_BUILD_DIRECTORY/prepared" || return 1
  target="generations/${stage##*/}"
  link_tmp="$KILIX_BUILD_DIRECTORY/.prepared.$$"
  ln -s -- "$target" "$link_tmp"
  mv -Tf -- "$link_tmp" "$KILIX_BUILD_DIRECTORY/prepared"
  stage=""
}

if [ "$mode" = bundle ]; then
  prepare_dependency_bundle "$KILIX_KITTY_DEPS_URL" "${KILIX_KITTY_DEPS_SHA256:-}"
fi
prepare_font

if [ "${KILIX_BUILD_PREPARE_ONLY:-0}" = 1 ]; then
  assert_font_notices_installed
  promote_prepared
  garbage_collect_generations \
    || echo "kilix: WARNING: dependency preparation committed but old build cleanup was incomplete" >&2
  echo "kilix: dependency preparation complete -> $KILIX_BUILD_DIRECTORY/prepared/src"
  exit 0
fi

cd "$BUILD_SRC"
echo "kilix: building forked kitty in $BUILD_SRC ($mode dependencies, $GOMAXPROCS Go package job(s)) ..."
if [ "$mode" = bundle ]; then
  ./dev.sh build "$@"
else
  _python_libdir="$("$_python" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR") or "")')"
  if [ -n "$_python_libdir" ] && [ -d "$_python_libdir" ]; then
    # The build runs its newly linked launcher for code generation. Make a
    # non-system Python visible immediately and retain that location as the
    # launcher's runtime search path.
    export LD_LIBRARY_PATH="$_python_libdir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export LD_RUN_PATH="$_python_libdir${LD_RUN_PATH:+:$LD_RUN_PATH}"
  fi
  # The `develop` action assumes bypy's DEVELOP_ROOT bundle. The ordinary
  # source `build` action is the upstream path that links host dependencies.
  "$_python" setup.py build "$@"
fi

probe_launcher() {
  timeout --kill-after=2 15 "$1" --version >/dev/null 2>&1
}

launcher="$BUILD_SRC/kitty/launcher/kitty"
kitten="$BUILD_SRC/kitty/launcher/kitten"
for built_launcher in "$launcher" "$kitten"; do
  if [ ! -f "$built_launcher" ] || [ -L "$built_launcher" ] \
       || [ ! -x "$built_launcher" ]; then
    echo "kilix: build finished but launcher is missing or unsafe: $built_launcher" >&2
    exit 1
  fi
  if ! probe_launcher "$built_launcher"; then
    echo "kilix: built launcher failed its version probe: $built_launcher" >&2
    exit 1
  fi
done
assert_font_notices_installed

assert_display_backends_built() {
  # A missing display backend does not fail the engine's build. When the host
  # lacks a protocol definition the engine wants, the backend is dropped with
  # a notice on stdout and the build still succeeds, so the only thing that
  # catches it is an explicit check before promotion.
  #
  # The engine already knows which backends it must produce and says so in its
  # own build check. Run that rather than restating the list here, so the two
  # cannot drift apart.
  #
  # Applies only to a real engine tree, identified by the backend manifest the
  # engine's build reads. That file is part of the engine's sources, so a tree
  # that has been built as an engine always has it; a harness that stands in a
  # stub for the engine never does. The condition is therefore structural
  # rather than a tolerance, and cannot quietly excuse a real build.
  [ -f "$BUILD_SRC/glfw/source-info.json" ] || return 0

  # CI is cleared deliberately: that variable makes the engine's check skip
  # Wayland, which is exactly the backend most likely to have been dropped.
  # Running this under CI would reproduce the silence at the last point left
  # to catch it.
  local out
  if out="$(env -u CI PYTHONPATH="$BUILD_SRC" "$_python" -m unittest \
      kitty_tests.check_build.TestBuild.test_glfw_modules 2>&1)"; then
    return 0
  fi
  printf '%s\n' "$out" >&2
  echo "kilix: the built engine is missing a display backend; not promoting this build" >&2
  return 1
}
assert_display_backends_built || exit 1
head="$src_head"
printf '%s\n' "$source_id" >"$stage/source-id"
if [ -n "$head" ]; then
  mkdir -p "$STATE_DIR"
  chmod 0700 "$STATE_DIR" 2>/dev/null || true
  stamp_tmp="$(mktemp "$STATE_DIR/fork-built-ref.tmp.XXXXXX")"
  printf '%s\t%s\n' "$KILIX_HOME" "$head" >"$stamp_tmp"
  chmod 0600 "$stamp_tmp"
fi
install_terminfo || {
  echo "kilix: could not install the engine's xterm-kitty terminfo into $HOME/.terminfo; not promoting this build" >&2
  exit 1
}
promote_current
garbage_collect_generations \
  || echo "kilix: WARNING: build committed but old generation cleanup was incomplete" >&2
launcher="$KILIX_BUILD_DIRECTORY/current/src/kitty/launcher/kitty"
echo "kilix: built -> $launcher"
