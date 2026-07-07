#!/usr/bin/env bash
# kilix — installer for the FORK BUILD + desktop prerequisites.
#
# The prebuilt engine (bootstrap.sh) needs only git/curl/tar. This script adds
# what the *clickable-chrome fork build* (kilix --build) and the pixel desktop
# need: a C toolchain, Go, kitty's X11 dev libraries, and Python + Pillow.
#
# Distro backends, auto-detected (system-wide, uses sudo):
#   Fedora/RHEL  : dnf, via pkgconfig(...) virtual provides
#   Debian/Ubuntu: apt-get, -dev packages
#   Arch         : pacman
#   openSUSE     : zypper
#
# Go: the fork's go.mod pins a Go version newer than some distros ship. Rather
# than install Go by hand, this enables Go's own toolchain auto-download
# (GOTOOLCHAIN=auto, written to ~/.kitty-fork-buildenv, which build.sh sources)
# whenever the system Go is older than required — so `go build` fetches the
# pinned toolchain on demand.
#
# Usage:  scripts/install-build-deps.sh            # install
#         scripts/install-build-deps.sh --verify   # re-check + print status
set -euo pipefail

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILDENV="$HOME/.kitty-fork-buildenv"

# pkg-config modules the fork links against (see build.sh).
PC_DEPS="x11 xrandr xinerama xcursor xi xkbcommon xkbcommon-x11 x11-xcb dbus-1 gl fontconfig"

log(){ printf 'kilix: %s\n' "$*" >&2; }

# Go version the fork requires, read from its go.mod (falls back to 1.26).
required_go(){ awk '/^go [0-9]/{print $2; exit}' "$KILIX_HOME/src/go.mod" 2>/dev/null || echo "1.26.0"; }

# Compare dotted versions: ver_ge A B  -> true if A >= B.
ver_ge(){ [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$2" ]; }

# ---- verify ------------------------------------------------------------------
verify() {
  local ok=1 m
  echo "==> verifying build prerequisites:"
  for m in $PC_DEPS; do
    if pkg-config --exists "$m" 2>/dev/null; then
      echo "   pkg-config $m: yes"
    else
      echo "   pkg-config $m: MISSING"; ok=0
    fi
  done
  for tool in gcc make pkg-config git curl; do
    command -v "$tool" >/dev/null 2>&1 && echo "   $tool: $(command -v "$tool")" \
      || { echo "   $tool: MISSING"; ok=0; }
  done
  local req; req="$(required_go)"
  if command -v go >/dev/null 2>&1; then
    local gv; gv="$(go version | awk '{print $3}' | sed 's/^go//')"
    if ver_ge "$gv" "$req"; then
      echo "   go: $gv (>= $req)"
    elif [ "$(go env GOTOOLCHAIN 2>/dev/null)" != "local" ] || grep -q GOTOOLCHAIN "$BUILDENV" 2>/dev/null; then
      echo "   go: $gv (< $req, but toolchain auto-download is enabled)"
    else
      echo "   go: $gv (< $req — the fork needs $req; enable GOTOOLCHAIN=auto)"; ok=0
    fi
  else
    echo "   go: MISSING (need $req or a Go that can auto-download it)"; ok=0
  fi
  python3 -c "import PIL; print('   Pillow:', PIL.__version__)" 2>/dev/null \
    || { echo "   Pillow: MISSING (the desktop/browse pixel planes need it)"; ok=0; }
  [ "$ok" = 1 ] && echo "==> OK — fork build + desktop prerequisites ready." \
                || echo "==> INCOMPLETE — see above."
  return 0
}

# ---- enable Go toolchain auto-download when the system Go is too old ----------
ensure_go_toolchain() {
  local req; req="$(required_go)"
  command -v go >/dev/null 2>&1 || return 0
  local gv; gv="$(go version | awk '{print $3}' | sed 's/^go//')"
  if ver_ge "$gv" "$req"; then
    log "system Go $gv satisfies the fork's requirement ($req)"
    return 0
  fi
  if ! grep -q 'GOTOOLCHAIN' "$BUILDENV" 2>/dev/null; then
    {
      echo "# kilix fork build env — sourced by build.sh. Auto-generated."
      echo "# System Go ($gv) is older than the fork needs ($req); let Go fetch"
      echo "# the pinned toolchain on demand instead of installing it by hand."
      echo "export GOTOOLCHAIN=auto"
    } >> "$BUILDENV"
    log "system Go $gv < $req — enabled GOTOOLCHAIN=auto in $BUILDENV"
  fi
}

# ---- per-distro installs -----------------------------------------------------
fedora_install() {
  local pc pkgs="gcc make pkgconf-pkg-config git curl golang python3 python3-devel python3-pillow"
  for pc in $PC_DEPS; do pkgs="$pkgs pkgconfig($pc)"; done
  echo "==> Fedora/RHEL detected — installing system-wide via dnf"
  sudo dnf install -y $pkgs
}

debian_install() {
  local pkgs="build-essential pkg-config git curl golang-go python3 python3-dev python3-pil \
    libx11-dev libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev libxkbcommon-dev \
    libxkbcommon-x11-dev libx11-xcb-dev libdbus-1-dev libgl1-mesa-dev libfontconfig-dev"
  echo "==> Debian/Ubuntu detected — installing system-wide via apt-get"
  sudo apt-get update
  sudo apt-get install -y $pkgs
}

arch_install() {
  local pkgs="base-devel pkgconf git curl go python python-pillow \
    libx11 libxrandr libxinerama libxcursor libxi libxkbcommon mesa dbus fontconfig"
  echo "==> Arch detected — installing system-wide via pacman"
  sudo pacman -S --needed --noconfirm $pkgs
}

suse_install() {
  local pkgs="gcc make pkg-config git curl go python3 python3-devel python3-Pillow \
    libX11-devel libXrandr-devel libXinerama-devel libXcursor-devel libXi-devel \
    libxkbcommon-devel libxkbcommon-x11-devel dbus-1-devel Mesa-libGL-devel fontconfig-devel"
  echo "==> openSUSE detected — installing system-wide via zypper"
  sudo zypper --non-interactive install $pkgs
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
  log "Python 3 + Pillow, and the dev libs for: $PC_DEPS"
  exit 1
fi

ensure_go_toolchain
echo
verify
echo
echo "==> Done. Build the clickable-chrome fork with:  ./kilix --build"
