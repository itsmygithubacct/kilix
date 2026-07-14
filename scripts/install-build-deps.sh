#!/usr/bin/env bash
# kilix — installer for the FORK BUILD + desktop prerequisites.
#
# The prebuilt engine (bootstrap.sh) needs only git/curl/tar. This script adds
# what the *clickable-chrome fork build* (kilix --build) and the pixel desktop
# need: a C toolchain, Go, kitty's X11 dev libraries, Python + Pillow, and
# kilix-amp's SDL/libsndfile/FluidSynth build/runtime libraries.
#
# Distro backends, auto-detected (system-wide, uses sudo):
#   Fedora/RHEL  : dnf, via pkgconfig(...) virtual provides
#   Debian/Ubuntu: apt-get, -dev packages
#   Arch         : pacman
#   openSUSE     : zypper
#
# Go: the fork's go.mod pins a Go version newer than some distros ship. Rather
# than install Go by hand, this enables Go's own toolchain auto-download
# (an exact `GOTOOLCHAIN=goX.Y.Z+auto` in Kilix's private build.env, which
# build.sh sources)
# whenever the system Go is older than required — so `go build` fetches the
# exact toolchain named by go.mod (and verifies it through Go's module checksum
# mechanism) rather than resolving an open-ended "latest" toolchain.
#
# Usage:  scripts/install-build-deps.sh            # install
#         scripts/install-build-deps.sh --verify   # re-check + print status
set -euo pipefail
umask 077

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_CONFIG_HOME="${KILIX_CONFIG_HOME:-$KILIX_STORAGE_HOME/config}"
BUILDENV="${KILIX_BUILD_ENV:-$KILIX_CONFIG_HOME/build.env}"
mkdir -p "$KILIX_CONFIG_HOME"
chmod 0700 "$KILIX_STORAGE_HOME" "$KILIX_CONFIG_HOME" 2>/dev/null || true

# pkg-config modules the fork links against (see build.sh).
PC_DEPS="x11 xrandr xinerama xcursor xi xkbcommon xkbcommon-x11 x11-xcb dbus-1 gl fontconfig libpng lcms2 cairo-fc harfbuzz libcrypto libxxhash wayland-client wayland-cursor wayland-egl wayland-protocols"
AMP_PC_DEPS="sdl2 SDL2_image sndfile zlib fluidsynth"

log(){ printf 'kilix: %s\n' "$*" >&2; }

# Exact Go toolchain the fork requires, read from go.mod (falls back to its
# language version, then a conservative project default).
language_go(){ awk '/^go [0-9]/{print $2; found=1; exit} END{if (!found) print "1.26.0"}' \
  "$KILIX_HOME/src/go.mod" 2>/dev/null; }
pinned_go(){ awk '/^toolchain go[0-9]/{sub(/^go/, "", $2); print $2; found=1; exit} \
  END{if (!found) exit 1}' "$KILIX_HOME/src/go.mod" 2>/dev/null || language_go; }
required_go(){ pinned_go; }

# Compare dotted versions: ver_ge A B  -> true if A >= B.
ver_ge(){ [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$2" ]; }

build_python() {
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
    if [ -n "$version" ] && ver_ge "$version" 3.12; then
      printf '%s\t%s\n' "$candidate" "$version"
      return 0
    fi
  done
  return 1
}

# ---- verify ------------------------------------------------------------------
verify() {
  local ok=1 m py_info
  echo "==> verifying build prerequisites:"
  for m in $PC_DEPS $AMP_PC_DEPS; do
    if pkg-config --exists "$m" 2>/dev/null; then
      echo "   pkg-config $m: yes"
    else
      echo "   pkg-config $m: MISSING"; ok=0
    fi
  done
  for tool in gcc make pkg-config git curl; do
    if command -v "$tool" >/dev/null 2>&1; then
      echo "   $tool: $(command -v "$tool")"
    else
      echo "   $tool: MISSING"; ok=0
    fi
  done
  if printf '%s\n' '#include <simde/x86/avx2.h>' | gcc -E -x c - >/dev/null 2>&1; then
    echo "   SIMDe headers: yes"
  else
    echo "   SIMDe headers: MISSING"; ok=0
  fi
  if py_info="$(build_python)"; then
    echo "   build Python: ${py_info#*$'\t'} (${py_info%%$'\t'*})"
  else
    echo "   build Python: MISSING (need >= 3.12; set KILIX_PYTHON if installed elsewhere)"
    ok=0
  fi
  local req; req="$(required_go)"
  if command -v go >/dev/null 2>&1; then
    local gv; gv="$(go version | awk '{print $3}' | sed 's/^go//')"
    if ver_ge "$gv" "$req"; then
      echo "   go: $gv (>= $req)"
    elif grep -q '^export GOTOOLCHAIN=go[0-9].*[+]auto$' "$BUILDENV" 2>/dev/null; then
      echo "   go: $gv (< $req, but exact toolchain $(pinned_go) is configured)"
    else
      echo "   go: $gv (< $req — run this installer to pin the required toolchain)"; ok=0
    fi
  else
    echo "   go: MISSING (need $req or a Go that can auto-download it)"; ok=0
  fi
  python3 -c "import PIL; print('   Pillow:', PIL.__version__)" 2>/dev/null \
    || { echo "   Pillow: MISSING (the desktop/browse pixel planes need it)"; ok=0; }
  if [ "$ok" = 1 ]; then
    echo "==> OK — fork build + desktop prerequisites ready."
  else
    echo "==> INCOMPLETE — see above."
    return 1
  fi
}

# ---- enable Go toolchain auto-download when the system Go is too old ----------
ensure_go_toolchain() {
  local req pin; req="$(required_go)"; pin="$(pinned_go)"
  command -v go >/dev/null 2>&1 || return 0
  local gv; gv="$(go version | awk '{print $3}' | sed 's/^go//')"
  if ver_ge "$gv" "$req"; then
    log "system Go $gv satisfies the fork's requirement ($req)"
    return 0
  fi
  if grep -q "^export GOTOOLCHAIN=go${pin}+auto$" "$BUILDENV" 2>/dev/null; then
    return 0
  elif grep -q '^export GOTOOLCHAIN=' "$BUILDENV" 2>/dev/null; then
    local tmp; tmp="$(mktemp "$KILIX_CONFIG_HOME/.build.env.XXXXXX")"
    awk -v value="export GOTOOLCHAIN=go${pin}+auto" \
      '/^export GOTOOLCHAIN=/{if (!done) print value; done=1; next} {print}' \
      "$BUILDENV" >"$tmp"
    mv "$tmp" "$BUILDENV"
    log "replaced mutable GOTOOLCHAIN setting with go${pin}+auto"
  else
    {
      echo "# kilix fork build env — sourced by build.sh. Auto-generated."
      echo "# System Go ($gv) is older than the fork needs ($req); let Go fetch"
      echo "# the exact go.mod toolchain on demand instead of resolving latest."
      echo "export GOTOOLCHAIN=go${pin}+auto"
    } >> "$BUILDENV"
    chmod 0600 "$BUILDENV"
    log "system Go $gv < $req — pinned GOTOOLCHAIN=go${pin}+auto in $BUILDENV"
  fi
}

# ---- per-distro installs -----------------------------------------------------
fedora_install() {
  local pc pkgs="gcc make pkgconf-pkg-config git curl golang python3 python3-devel python3-pillow simde-devel wayland-devel wayland-protocols-devel SDL2-devel SDL2_image-devel libsndfile-devel zlib-devel fluidsynth fluidsynth-devel fluid-soundfont-gm"
  local -a packages
  for pc in $PC_DEPS; do pkgs="$pkgs pkgconfig($pc)"; done
  echo "==> Fedora/RHEL detected — installing system-wide via dnf"
  read -r -a packages <<<"$pkgs"
  sudo dnf install -y "${packages[@]}"
}

debian_install() {
  local pkgs="build-essential pkg-config git curl golang-go python3 python3-dev python3-pil \
    libx11-dev libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev libxkbcommon-dev \
    libxkbcommon-x11-dev libx11-xcb-dev libdbus-1-dev libgl1-mesa-dev libfontconfig-dev \
    libpng-dev liblcms2-dev libcairo2-dev libharfbuzz-dev libssl-dev libxxhash-dev \
    libsimde-dev libwayland-dev wayland-protocols \
    libsdl2-dev libsdl2-image-dev libsndfile1-dev zlib1g-dev libfluidsynth-dev fluidsynth fluid-soundfont-gm"
  local -a packages
  echo "==> Debian/Ubuntu detected — installing system-wide via apt-get"
  sudo apt-get update
  read -r -a packages <<<"$pkgs"
  sudo apt-get install -y "${packages[@]}"
}

arch_install() {
  local pkgs="base-devel pkgconf git curl go python python-pillow \
    libx11 libxrandr libxinerama libxcursor libxi libxkbcommon mesa dbus fontconfig \
    libpng lcms2 cairo harfbuzz openssl xxhash simde wayland wayland-protocols \
    sdl2 sdl2_image libsndfile zlib fluidsynth soundfont-fluid"
  local -a packages
  echo "==> Arch detected — installing system-wide via pacman"
  read -r -a packages <<<"$pkgs"
  sudo pacman -S --needed --noconfirm "${packages[@]}"
}

suse_install() {
  local pkgs="gcc make pkg-config git curl go python3 python3-devel python3-Pillow \
    libX11-devel libXrandr-devel libXinerama-devel libXcursor-devel libXi-devel \
    libxkbcommon-devel libxkbcommon-x11-devel dbus-1-devel Mesa-libGL-devel fontconfig-devel \
    libpng16-devel liblcms2-devel cairo-devel harfbuzz-devel libopenssl-devel libxxhash-devel \
    simde-devel wayland-devel wayland-protocols-devel \
    libSDL2-devel libSDL2_image-devel libsndfile-devel zlib-devel fluidsynth-devel fluidsynth fluid-soundfont-gm"
  local -a packages
  echo "==> openSUSE detected — installing system-wide via zypper"
  read -r -a packages <<<"$pkgs"
  sudo zypper --non-interactive install "${packages[@]}"
}

# ---- dispatch ----------------------------------------------------------------
if [ "${1:-}" = "--verify" ]; then verify; exit 0; fi

if command -v dnf >/dev/null 2>&1 && command -v rpm >/dev/null 2>&1; then
  fedora_install
elif command -v apt-get >/dev/null 2>&1; then
  debian_install
elif command -v pacman >/dev/null 2>&1; then
  arch_install
elif command -v zypper >/dev/null 2>&1; then
  suse_install
else
  log "unsupported distro — need one of: dnf, apt-get, pacman, zypper."
  log "install manually: a C compiler, make, pkg-config, Go, git, curl,"
  log "Python 3 + Pillow, and the dev libs for: $PC_DEPS $AMP_PC_DEPS"
  exit 1
fi

ensure_go_toolchain
echo
verify
echo
echo "==> Done. Build the clickable-chrome fork with:  ./kilix --build"
