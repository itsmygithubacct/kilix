#!/usr/bin/env bash
# Prepare the exact kilix-chawan text browser selected by this Kilix checkout.
#
# Chawan is written in Nim, and needs a newer Nim than most distributions ship,
# so this installer bootstraps a pinned Nim toolchain of its own rather than
# asking the user to install one system-wide. Nothing here needs root.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$GPU_TERMINAL_HOME/sources}"
KILIX_CHAWAN_DIR="${KILIX_CHAWAN_DIR:-$GPU_TERMINAL_SOURCE_HOME/kilix-apps/kilix-chawan}"
KILIX_CHAWAN_REPO="${KILIX_CHAWAN_REPO:-https://github.com/itsmygithubacct/kilix-chawan.git}"
KILIX_CHAWAN_AUTO_INSTALL="${KILIX_CHAWAN_AUTO_INSTALL:-1}"
KILIX_CHAWAN_TRUST_EXISTING_CHECKOUT="${KILIX_CHAWAN_TRUST_EXISTING_CHECKOUT:-0}"
KILIX_CHAWAN_ALLOW_MUTABLE_REF="${KILIX_CHAWAN_ALLOW_MUTABLE_REF:-0}"
KILIX_CHAWAN_TOOLCHAIN_HOME="${KILIX_CHAWAN_TOOLCHAIN_HOME:-$GPU_TERMINAL_HOME/toolchains}"

# This full commit is part of Kilix's transitive source closure. An existing
# sibling checkout remains a development checkout unless KILIX_CHAWAN_REF is
# explicitly set; a first-use download always resolves this immutable default.
KILIX_CHAWAN_DEFAULT_REF=b2b2932453b1348be1ca841aaefd9258acdda0c1

# Pinned Nim toolchain. Upstream Chawan asks for 2.0.0 or newer and recommends
# 2.2.10; Debian bookworm ships 1.6.10, which upstream advises against. The
# checksums are the ones nim-lang.org publishes next to each tarball.
KILIX_CHAWAN_NIM_VERSION=2.2.10
nim_sha256_linux_x64=0a3a38752e97e9d44aa479b3a7b37336dfe0176daf22ee5b5218ad0991ecd211
nim_sha256_linux_arm64=cd86a6e2bcbf029c4870aa51df5c0169345dbf9959889112fd15d403c13ae33a
nim_sha256_linux_x32=7e018e66e570943c8e079e5cf78898444fc627bc0d47b7a5c17dc97cbc12083e

# Fallback libssh2, built only when the system has no development files and the
# user opts in. libssh2.org signs releases with GPG but publishes no checksum
# file, so this SHA-256 was recorded from the signed release at integration
# time; the packaged libssh2-1-dev remains the preferred source.
KILIX_CHAWAN_LIBSSH2_VERSION=1.11.1
libssh2_sha256=d9ec76cbe34db98eec3539fe2c899d26b0c837cb3eb466a56b0f109cabf658f7

die() { printf 'kilix chawan: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix chawan: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-kilix-chawan.sh [--print-path|--print-installed|--print-ref]

  --print-path       clone/build as needed, then print the executable path
  --print-installed  print the path only if it is already built, else fail
                     quietly; never downloads or builds anything
  --print-ref        print the immutable first-install commit, changing nothing

Environment:
  KILIX_CHAWAN_DIR              checkout location
  KILIX_CHAWAN_REF              build this commit instead of the pinned default
  KILIX_CHAWAN_AUTO_INSTALL=0   refuse to download anything
  KILIX_CHAWAN_NIM              use this Nim binary instead of bootstrapping one
  KILIX_CHAWAN_BUILD_LIBSSH2    1 build libssh2 for SFTP, 0 never, ask (default)
EOF
}

action="${1:---print-path}"
[ $# -eq 0 ] || shift
case "$action" in
  --print-path|--print-installed) ;;
  --print-ref)
    [ $# -eq 0 ] || { usage >&2; exit 2; }
    printf '%s\n' "${KILIX_CHAWAN_REF:-$KILIX_CHAWAN_DEFAULT_REF}"
    exit 0 ;;
  -h|--help)
    usage
    exit 0 ;;
  *)
    usage >&2
    exit 2 ;;
esac
[ $# -eq 0 ] || { usage >&2; exit 2; }
[ "$(id -u)" -ne 0 ] || die "run this installer as the desktop user, not root"

case "$KILIX_CHAWAN_DIR" in
  /*) ;;
  *) die "KILIX_CHAWAN_DIR must be a normalized absolute path: $KILIX_CHAWAN_DIR" ;;
esac
chawan_dir="$(realpath -m -- "$KILIX_CHAWAN_DIR" 2>/dev/null)" \
  || die "could not normalize KILIX_CHAWAN_DIR=$KILIX_CHAWAN_DIR"
[ "$chawan_dir" = "$KILIX_CHAWAN_DIR" ] \
  || die "KILIX_CHAWAN_DIR must be normalized and contain no symlink components: $KILIX_CHAWAN_DIR"
case "$chawan_dir" in
  /|"$HOME"|"$GPU_TERMINAL_SOURCE_HOME")
    die "refusing broad kilix-chawan checkout path: $chawan_dir" ;;
esac

# Answer "is it already there?" without a toolchain, a network, or a build, so
# callers that merely prefer Chawan can ask cheaply and fall back when it is
# absent instead of blocking on a first-run compile.
if [ "$action" = --print-installed ]; then
  chawan_binary="$chawan_dir/target/release/bin/cha"
  [ -f "$chawan_binary" ] && [ ! -L "$chawan_binary" ] && [ -x "$chawan_binary" ] \
    || exit 1
  printf '%s\n' "$chawan_binary"
  exit 0
fi

for command in git make cc pkg-config tar xz sha256sum; do
  command -v "$command" >/dev/null 2>&1 \
    || die "$command is required (install git, make, a C compiler, pkg-config, xz-utils, and coreutils)"
done
for module in libssl libcrypto libbrotlidec libbrotlicommon; do
  pkg-config --exists "$module" \
    || die "$module development files are required (on Debian: apt install libssl-dev libbrotli-dev)"
done

explicit_ref="${KILIX_CHAWAN_REF:-}"
install_ref="${explicit_ref:-$KILIX_CHAWAN_DEFAULT_REF}"
if ! [[ "$install_ref" =~ ^[0-9a-fA-F]{40}$ ]] \
     && [ "$KILIX_CHAWAN_ALLOW_MUTABLE_REF" != 1 ]; then
  die "KILIX_CHAWAN_REF must be a full 40-character commit SHA (set KILIX_CHAWAN_ALLOW_MUTABLE_REF=1 only to trust a mutable tag/branch)"
fi

download_allowed() {
  case "$KILIX_CHAWAN_AUTO_INSTALL" in
    1|yes|true|on) return 0 ;;
    *) return 1 ;;
  esac
}

fetch_verified() {
  # fetch_verified URL SHA256 DESTINATION — download to a private temporary
  # file, verify, and only then publish it under the caller's name.
  local url="$1" want="$2" destination="$3" temporary
  command -v curl >/dev/null 2>&1 || die "curl is required to download $url"
  temporary="$destination.partial.$$"
  curl -fsSL --proto '=https' --tlsv1.2 -o "$temporary" -- "$url" \
    || { rm -f -- "$temporary"; die "download failed: $url"; }
  local got
  got="$(sha256sum -- "$temporary" | cut -d' ' -f1)"
  if [ "$got" != "$want" ]; then
    rm -f -- "$temporary"
    die "checksum mismatch for $url (expected $want, got $got)"
  fi
  mv -- "$temporary" "$destination" || die "could not store $destination"
}

# ---------------------------------------------------------------- Nim toolchain

nim_is_new_enough() {
  # Chawan needs 2.0.0 or newer; anything older is rejected outright.
  local candidate="$1" version major
  [ -n "$candidate" ] && [ -x "$candidate" ] || return 1
  version="$("$candidate" --version 2>/dev/null | head -n 1 \
             | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n 1)" || return 1
  [ -n "$version" ] || return 1
  major="${version%%.*}"
  [ "$major" -ge 2 ] 2>/dev/null
}

ensure_nim() {
  # Sets NIM_BIN to a Nim 2.x compiler, bootstrapping the pinned release when
  # this platform has a published tarball.
  if [ -n "${KILIX_CHAWAN_NIM:-}" ]; then
    nim_is_new_enough "$KILIX_CHAWAN_NIM" \
      || die "KILIX_CHAWAN_NIM=$KILIX_CHAWAN_NIM is not a Nim 2.0 or newer compiler"
    NIM_BIN="$KILIX_CHAWAN_NIM"
    return 0
  fi

  local arch tarball_arch want
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) tarball_arch=linux_x64; want="$nim_sha256_linux_x64" ;;
    aarch64|arm64) tarball_arch=linux_arm64; want="$nim_sha256_linux_arm64" ;;
    i386|i486|i586|i686) tarball_arch=linux_x32; want="$nim_sha256_linux_x32" ;;
    *) tarball_arch= ;;
  esac

  local root="$KILIX_CHAWAN_TOOLCHAIN_HOME/nim-$KILIX_CHAWAN_NIM_VERSION"
  if [ -x "$root/bin/nim" ] && nim_is_new_enough "$root/bin/nim"; then
    NIM_BIN="$root/bin/nim"
    return 0
  fi

  if [ -z "$tarball_arch" ]; then
    # No published build for this machine, so a system Nim is the only option.
    local system
    system="$(command -v nim 2>/dev/null || true)"
    if nim_is_new_enough "$system"; then
      log "no pinned Nim for $arch; using the system Nim at $system"
      NIM_BIN="$system"
      return 0
    fi
    die "no pinned Nim toolchain for $arch; install Nim 2.0 or newer and set KILIX_CHAWAN_NIM"
  fi

  download_allowed \
    || die "Nim $KILIX_CHAWAN_NIM_VERSION is not installed at $root; set KILIX_CHAWAN_AUTO_INSTALL=1 to download it"

  mkdir -p -- "$KILIX_CHAWAN_TOOLCHAIN_HOME" \
    || die "could not create the toolchain directory: $KILIX_CHAWAN_TOOLCHAIN_HOME"
  local staging
  staging="$(mktemp -d "$KILIX_CHAWAN_TOOLCHAIN_HOME/.nim.XXXXXX")" \
    || die "could not allocate a temporary toolchain directory"

  local url="https://nim-lang.org/download/nim-$KILIX_CHAWAN_NIM_VERSION-$tarball_arch.tar.xz"
  log "downloading pinned Nim $KILIX_CHAWAN_NIM_VERSION -> $root"
  if ! (
    fetch_verified "$url" "$want" "$staging/nim.tar.xz"
    tar -xJf "$staging/nim.tar.xz" -C "$staging" \
      || die "could not unpack the Nim toolchain"
    [ -x "$staging/nim-$KILIX_CHAWAN_NIM_VERSION/bin/nim" ] \
      || die "the Nim tarball did not contain bin/nim"
  ); then
    rm -rf -- "$staging"
    exit 1
  fi

  if [ -e "$root" ] || [ -L "$root" ]; then
    rm -rf -- "$staging"
    die "toolchain path appeared while Nim was being prepared: $root"
  fi
  mv -- "$staging/nim-$KILIX_CHAWAN_NIM_VERSION" "$root" \
    || { rm -rf -- "$staging"; die "could not publish the Nim toolchain"; }
  rm -rf -- "$staging"

  nim_is_new_enough "$root/bin/nim" \
    || die "the bootstrapped Nim at $root does not run on this machine"
  NIM_BIN="$root/bin/nim"
}

# -------------------------------------------------------------------- libssh2

build_libssh2() {
  # Build a static libssh2 into the toolchain prefix. Static keeps it out of
  # the runtime linker's way: nothing outside this build ever sees it.
  local prefix="$KILIX_CHAWAN_TOOLCHAIN_HOME/libssh2-$KILIX_CHAWAN_LIBSSH2_VERSION"
  if [ -f "$prefix/lib/pkgconfig/libssh2.pc" ]; then
    printf '%s\n' "$prefix/lib/pkgconfig"
    return 0
  fi
  command -v cmake >/dev/null 2>&1 || { log "cmake is required to build libssh2"; return 1; }
  download_allowed || { log "downloads are disabled, so libssh2 cannot be built"; return 1; }

  mkdir -p -- "$KILIX_CHAWAN_TOOLCHAIN_HOME" || return 1
  local staging
  staging="$(mktemp -d "$KILIX_CHAWAN_TOOLCHAIN_HOME/.libssh2.XXXXXX")" || return 1

  local url="https://libssh2.org/download/libssh2-$KILIX_CHAWAN_LIBSSH2_VERSION.tar.gz"
  log "building libssh2 $KILIX_CHAWAN_LIBSSH2_VERSION for SFTP support"
  if ! (
    set -e
    fetch_verified "$url" "$libssh2_sha256" "$staging/libssh2.tar.gz"
    tar -xzf "$staging/libssh2.tar.gz" -C "$staging"
    cd "$staging/libssh2-$KILIX_CHAWAN_LIBSSH2_VERSION"
    cmake -S . -B build \
      -DCMAKE_INSTALL_PREFIX="$staging/prefix" \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
      -DBUILD_SHARED_LIBS=OFF \
      -DBUILD_EXAMPLES=OFF \
      -DBUILD_TESTING=OFF \
      -DCRYPTO_BACKEND=OpenSSL >/dev/null
    cmake --build build --parallel "$(nproc 2>/dev/null || echo 2)" >/dev/null
    cmake --install build >/dev/null
    [ -f "$staging/prefix/lib/pkgconfig/libssh2.pc" ]
  ) >&2; then
    rm -rf -- "$staging"
    log "libssh2 build failed"
    return 1
  fi

  if [ -e "$prefix" ] || [ -L "$prefix" ]; then
    rm -rf -- "$staging"
    log "libssh2 prefix appeared while it was being prepared: $prefix"
    return 1
  fi
  mv -- "$staging/prefix" "$prefix" || { rm -rf -- "$staging"; return 1; }
  rm -rf -- "$staging"
  printf '%s\n' "$prefix/lib/pkgconfig"
}

resolve_libssh2() {
  # Sets CHA_SFTP and, when a private libssh2 is used, PKG_CONFIG_PATH.
  # Order of preference: the packaged system library, then a locally built
  # one, and only then a build with SFTP compiled out.
  local prefix="$KILIX_CHAWAN_TOOLCHAIN_HOME/libssh2-$KILIX_CHAWAN_LIBSSH2_VERSION"
  if pkg-config --exists libssh2 2>/dev/null; then
    CHA_SFTP=1
    return 0
  fi
  if [ -f "$prefix/lib/pkgconfig/libssh2.pc" ]; then
    PKG_CONFIG_PATH="$prefix/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    export PKG_CONFIG_PATH
    CHA_SFTP=1
    return 0
  fi

  log "libssh2 development files were not found."
  log "  They are what Chawan needs to serve sftp:// URLs. Everything else —"
  log "  HTTP(S), Gopher, Gemini, Finger, Spartan, FTP — works without them."
  log "  The packaged library is the best source: apt install libssh2-1-dev"

  local answer="${KILIX_CHAWAN_BUILD_LIBSSH2:-ask}"
  if [ "$answer" = ask ]; then
    if [ -t 0 ] && [ -t 2 ]; then
      local reply=
      printf 'kilix chawan: download and build libssh2 here instead? [y/N] ' >&2
      IFS= read -r reply || reply=
      case "$reply" in
        y|Y|yes|YES|Yes) answer=1 ;;
        *) answer=0 ;;
      esac
    else
      # Nothing to prompt, so take the option that installs the least.
      answer=0
    fi
  fi

  case "$answer" in
    1|yes|true|on)
      local pkgdir
      if pkgdir="$(build_libssh2)"; then
        PKG_CONFIG_PATH="$pkgdir${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
        export PKG_CONFIG_PATH
        CHA_SFTP=1
        return 0
      fi
      log "falling back to a build without SFTP" ;;
    *)
      log "building without SFTP (set KILIX_CHAWAN_BUILD_LIBSSH2=1 to build libssh2)" ;;
  esac
  CHA_SFTP=0
}

# ---------------------------------------------------------------------- build

build_checkout() {
  local directory="$1" binary stamp previous
  binary="$directory/target/release/bin/cha"
  if [ ! -f "$directory/Makefile" ] || [ -L "$directory/Makefile" ]; then
    die "missing or unsafe kilix-chawan Makefile: $directory/Makefile"
  fi

  ensure_nim
  resolve_libssh2

  # The SFTP switch changes which libraries the ssl binary links, and make
  # cannot see that in a timestamp, so a changed mode forces a clean rebuild.
  stamp="$directory/.kilix-build-mode"
  previous=""
  [ ! -f "$stamp" ] || previous="$(cat -- "$stamp" 2>/dev/null || true)"
  if [ -n "$previous" ] && [ "$previous" != "sftp=$CHA_SFTP" ]; then
    log "SFTP support changed ($previous -> sftp=$CHA_SFTP); rebuilding from scratch"
    rm -rf -- "$directory/target" "$directory/.obj"
  fi

  [ "$CHA_SFTP" = 1 ] || log "building without SFTP support"
  log "building $directory"
  PATH="$(dirname "$NIM_BIN"):$PATH" \
  make --no-print-directory -C "$directory" \
       NIM="$NIM_BIN" CHA_SFTP="$CHA_SFTP" \
       -j "$(nproc 2>/dev/null || echo 2)" >&2 \
    || die "build failed"

  if [ ! -f "$binary" ] || [ -L "$binary" ] || [ ! -x "$binary" ]; then
    die "build did not produce a regular executable: $binary"
  fi
  printf 'sftp=%s\n' "$CHA_SFTP" > "$stamp" 2>/dev/null || true
}

checkout_ref() {
  local directory="$1" ref="$2" require_clean="${3:-1}" target
  if [ "$require_clean" = 1 ] \
       && [ -n "$(git -C "$directory" status --porcelain --untracked-files=normal 2>/dev/null)" ]; then
    die "ref checkout refused because $directory has local modifications"
  fi
  git -C "$directory" fetch --no-tags origin "$ref" >&2 \
    || die "could not fetch KILIX_CHAWAN_REF=$ref"
  target="$(git -C "$directory" rev-parse --verify 'FETCH_HEAD^{commit}' 2>/dev/null)" \
    || die "KILIX_CHAWAN_REF did not resolve to a commit"
  git -C "$directory" checkout --detach "$target" >&2 \
    || die "could not check out KILIX_CHAWAN_REF=$ref"
  [ "$(git -C "$directory" rev-parse --verify HEAD 2>/dev/null)" = "$target" ] \
    || die "kilix-chawan checkout verification failed"
}

if git -C "$chawan_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  checkout_root="$(git -C "$chawan_dir" rev-parse --show-toplevel 2>/dev/null)" \
    || die "could not resolve the kilix-chawan checkout root"
  checkout_root="$(realpath -m -- "$checkout_root")"
  [ "$checkout_root" = "$chawan_dir" ] \
    || die "$chawan_dir is nested inside a different Git checkout: $checkout_root"
  origin="$(git -C "$chawan_dir" remote get-url origin 2>/dev/null || true)"
  if [ "$origin" != "$KILIX_CHAWAN_REPO" ] \
       && [ "$KILIX_CHAWAN_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$chawan_dir has origin '${origin:-missing}', expected '$KILIX_CHAWAN_REPO' (set KILIX_CHAWAN_TRUST_EXISTING_CHECKOUT=1 only for a trusted checkout)"
  fi
  if [ -n "$explicit_ref" ]; then
    checkout_ref "$chawan_dir" "$explicit_ref"
  else
    log "using existing checkout at $(git -C "$chawan_dir" rev-parse --short HEAD)"
  fi
  build_checkout "$chawan_dir"
  printf '%s\n' "$chawan_dir/target/release/bin/cha"
  exit 0
fi

if [ -e "$chawan_dir" ] || [ -L "$chawan_dir" ]; then
  if [ "$KILIX_CHAWAN_TRUST_EXISTING_CHECKOUT" != 1 ]; then
    die "$chawan_dir exists but is not a Git checkout"
  fi
  log "using trusted packaged source at $chawan_dir"
  build_checkout "$chawan_dir"
  printf '%s\n' "$chawan_dir/target/release/bin/cha"
  exit 0
fi

download_allowed \
  || die "kilix-chawan is not installed at $chawan_dir; set KILIX_CHAWAN_AUTO_INSTALL=1 to download it"

parent="$(dirname "$chawan_dir")"
mkdir -p -- "$parent" || die "could not create checkout parent: $parent"
if [ ! -d "$parent" ] || [ -L "$parent" ]; then
  die "checkout parent must be a real directory: $parent"
fi

clone_tmp="$(mktemp -d "$parent/.kilix-chawan.clone.XXXXXX")" \
  || die "could not allocate a temporary clone directory"
cleanup() {
  [ -z "${clone_tmp:-}" ] || rm -rf -- "$clone_tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

checkout="$clone_tmp/checkout"
log "downloading pinned kilix-chawan $install_ref -> $chawan_dir"
git clone --no-checkout -- "$KILIX_CHAWAN_REPO" "$checkout" >&2 \
  || die "git clone failed ($KILIX_CHAWAN_REPO)"
checkout_ref "$checkout" "$install_ref" 0
build_checkout "$checkout"
if [ -e "$chawan_dir" ] || [ -L "$chawan_dir" ]; then
  die "checkout path appeared while kilix-chawan was being prepared: $chawan_dir"
fi
mv -- "$checkout" "$chawan_dir" || die "could not publish the prepared checkout"
rm -rf -- "$clone_tmp"
clone_tmp=""

printf '%s\n' "$chawan_dir/target/release/bin/cha"
