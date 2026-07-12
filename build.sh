#!/usr/bin/env bash
# Build the forked kitty (clickable chrome) that lives in ./src.
#
# The default build uses the host's signed package-manager dependencies and the
# pinned kitty source checkout directly.  An upstream CI dependency bundle may
# be supplied explicitly for release/offline builds, but its URL and SHA-256
# are both mandatory.  Kilix deliberately has no default for kitty's mutable
# /ci/ bundle URL.
set -euo pipefail

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$HOME/.kitty-fork-buildenv" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.kitty-fork-buildenv"
fi

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

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/kilix/build"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/kilix"
font_archive="$CACHE_DIR/NerdFontsSymbolsOnly-v3.4.0.tar.xz"
font_file="$KILIX_HOME/src/fonts/SymbolsNerdFontMono-Regular.ttf"
font_url="${KILIX_NERD_FONT_URL:-https://github.com/ryanoasis/nerd-fonts/releases/download/v3.4.0/NerdFontsSymbolsOnly.tar.xz}"
font_sha="${KILIX_NERD_FONT_SHA256:-7f8c090da3b0eaa7108646bf34cbbb6ed13d5358a72460522108b06c7ecd716a}"
font_file_sha="${KILIX_NERD_FONT_FILE_SHA256:-f0f624d9b474bea1662cf7e862d44aebe1ae1f6c7f9cb7a0ca5d0e5ac9561c60}"

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

prepare_font() {
  local tmpdir extracted tmpfile
  if verify_file "$font_file" "$font_file_sha" "extracted Nerd Font" 2>/dev/null; then
    return 0
  fi
  fetch_verified "$font_url" "$font_archive" "$font_sha" "Nerd Font archive"
  mkdir -p "$(dirname "$font_file")"
  tmpdir="$(mktemp -d "$KILIX_HOME/src/fonts/.extract.XXXXXX")"
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
  deps_root="$KILIX_HOME/src/dependencies/linux-amd64"
  stamp="$deps_root/.kilix-prepared-bundle"
  wanted="$deps_root"$'\t'"${expected,,}"
  fetch_verified "$url" "$archive" "$expected" "kitty dependency bundle"
  if [ -x "$deps_root/bin/python" ] \
       && [ "$(cat "$stamp" 2>/dev/null || true)" = "$wanted" ]; then
    return 0
  fi

  mkdir -p "$KILIX_HOME/src/dependencies"
  tmp="$(mktemp -d "$KILIX_HOME/src/dependencies/.extract.XXXXXX")"
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
      || { echo "kilix: bundle mode requires KILIX_KITTY_DEPS_URL" >&2; exit 2; }
    prepare_dependency_bundle "$KILIX_KITTY_DEPS_URL" "${KILIX_KITTY_DEPS_SHA256:-}" ;;
  *) echo "kilix: invalid KILIX_BUILD_MODE=$mode (use system or bundle)" >&2; exit 2 ;;
esac
prepare_font

if [ "${KILIX_BUILD_PREPARE_ONLY:-0}" = 1 ]; then
  echo "kilix: dependency preparation complete"
  exit 0
fi

cd "$KILIX_HOME/src"
echo "kilix: building forked kitty in $KILIX_HOME/src ($mode dependencies, $GOMAXPROCS Go package job(s)) ..."
if [ "$mode" = bundle ]; then
  ./dev.sh build "$@"
else
  command -v python3 >/dev/null 2>&1 \
    || { echo "kilix: python3 is required for a system-dependency build" >&2; exit 1; }
  # The `develop` action assumes bypy's DEVELOP_ROOT bundle. The ordinary
  # source `build` action is the upstream path that links host dependencies.
  python3 setup.py build "$@"
fi

launcher="$KILIX_HOME/src/kitty/launcher/kitty"
[ -x "$launcher" ] || { echo "kilix: build finished but $launcher is missing" >&2; exit 1; }
head="$(git -C "$KILIX_HOME/src" rev-parse HEAD 2>/dev/null || true)"
if [ -n "$head" ]; then
  mkdir -p "$STATE_DIR"
  stamp_tmp="$(mktemp "$STATE_DIR/fork-built-ref.tmp.XXXXXX")"
  printf '%s\t%s\n' "$KILIX_HOME" "$head" >"$stamp_tmp"
  mv "$stamp_tmp" "$STATE_DIR/fork-built-ref"
fi
echo "kilix: built -> $launcher"
