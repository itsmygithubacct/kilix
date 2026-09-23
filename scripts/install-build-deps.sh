#!/usr/bin/env bash
# kilix — installer for the FORK BUILD + desktop prerequisites.
#
# The prebuilt engine (bootstrap.sh) needs only git/curl/tar. This script adds
# what the *clickable-chrome fork build* (kilix --build) and the pixel desktop
# need: a C toolchain, Go, kitty's X11 dev libraries, Python + Pillow, Python's
# venv/ensurepip support for PDF Conversion, zstd for bounded session-log
# storage, and kilix-amp's SDL/libsndfile/FluidSynth build/runtime libraries.
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

# Every pkg-config module the fork's build asks for and stops without: the
# fatal pkg_config / pkg_version / at_least_version requests in src/setup.py
# and src/glfw/glfw.py. This list is not trusted, it is checked: egl and libdrm
# were missing from it for as long as it existed, covered only because sdl2's
# -dev package happened to depend on both, and the build failed without either
# while --verify said OK. tests/test_build_deps_install.py reads the requests
# from the pinned fork's own build files and fails if verify() passes with any
# one of them missing, so a module the build starts asking for fails the suite
# until it is added here.
FORK_PC_DEPS="cairo-fc dbus-1 egl gl harfbuzz lcms2 libcrypto libdrm libpng libxxhash wayland-client wayland-cursor wayland-protocols wayland-scanner x11 x11-xcb xcursor xinerama xkbcommon xkbcommon-x11 xrandr"
# Also required, though the build never asks pkg-config for them, so no
# derivation can find them: it links -lz directly, includes
# <X11/extensions/XInput2.h> and <fontconfig/fontconfig.h>, and loads
# libwayland-egl at run time.
FORK_UNASKED_PC_DEPS="zlib xi fontconfig wayland-egl"
PC_DEPS="$FORK_PC_DEPS $FORK_UNASKED_PC_DEPS"
# Installed, reported, and never required. These are kilix-amp's, the desktop
# Media Player, which the desktop clones and builds on first use; build.sh
# links none of them. Requiring them made a machine that can build the fork
# fail --verify, and pleb installs whenever --verify fails, so the terminal's
# own gate pulled in Amp's packages -- on Debian that means libfluidsynth-dev,
# the only package with fluidsynth.pc, which hard-depends on the fluidsynth
# player.
AMP_PC_DEPS="sdl2 SDL2_image sndfile fluidsynth"
# Wanted, but never required. These belong to the text browser (`kilix chawan`),
# which is built on first use rather than with the fork, and which can build
# libssh2 for itself or drop SFTP entirely when it is absent. Verifying them
# alongside PC_DEPS would let a missing optional browser feature fail the whole
# prerequisite gate — and pleb treats that gate as fatal.
OPTIONAL_PC_DEPS="libssh2 libbrotlidec"

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

# The header an interpreter's extensions compile against. The fork's first
# compilation unit is `#include <Python.h>`, so an interpreter without it cannot
# build the engine, however new it is.
python_header() {
  local include
  include="$("$1" -c 'import sysconfig; print(sysconfig.get_paths()["include"])' 2>/dev/null || true)"
  [ -n "$include" ] && [ -f "$include/Python.h" ] || return 1
  printf '%s\n' "$include/Python.h"
}

# Which interpreter builds the fork. build.sh's select_system_python makes the
# same choice by the same rule -- two passes over one ordered list, the first
# accepting only an interpreter whose headers are present -- and
# tests/test_build_behavior.py runs both over one fixture and requires them to
# agree, because an installer that verifies one interpreter while the build
# picks another is the defect this replaced: the newest interpreter was chosen
# while only the distro default's headers were installed, and verify() said OK.
build_python() {
  local candidate version pass
  local -a candidates
  if [ -n "${KILIX_PYTHON:-}" ]; then
    candidates=("$KILIX_PYTHON")
  else
    candidates=(python3.14 python3.13 python3.12 python3)
  fi
  for pass in headers any; do
    for candidate in "${candidates[@]}"; do
      if [[ "$candidate" == */* ]]; then
        [ -x "$candidate" ] || continue
      else
        candidate="$(command -v "$candidate" 2>/dev/null || true)"
        [ -n "$candidate" ] || continue
      fi
      version="$("$candidate" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || true)"
      [ -n "$version" ] && ver_ge "$version" 3.12 || continue
      [ "$pass" = any ] || python_header "$candidate" >/dev/null || continue
      printf '%s\t%s\n' "$candidate" "$version"
      return 0
    done
  done
  return 1
}

# ---- verify ------------------------------------------------------------------
verify() {
  local ok=1 m py_info
  echo "==> verifying build prerequisites:"
  for m in $PC_DEPS; do
    if pkg-config --exists "$m" 2>/dev/null; then
      echo "   pkg-config $m: yes"
    else
      echo "   pkg-config $m: MISSING"; ok=0
    fi
  done
  for m in $AMP_PC_DEPS; do
    if pkg-config --exists "$m" 2>/dev/null; then
      echo "   pkg-config $m: yes (Media Player)"
    else
      echo "   pkg-config $m: missing (Media Player only — the desktop builds kilix-amp"
      echo "                 on first use and needs it then; run this installer to add it)"
    fi
  done
  for m in $OPTIONAL_PC_DEPS; do
    if pkg-config --exists "$m" 2>/dev/null; then
      echo "   pkg-config $m: yes (optional)"
    else
      echo "   pkg-config $m: missing (optional — 'kilix chawan' builds it or"
      echo "                 goes without; on Debian: apt install libssh2-1-dev libbrotli-dev)"
    fi
  done
  for tool in gcc make pkg-config git curl zstd; do
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
    local py_path="${py_info%%$'\t'*}" py_version="${py_info#*$'\t'}" header
    echo "   build Python: $py_version ($py_path)"
    # Reporting the interpreter is not reporting that the build can use it:
    # this line used to be the whole check, and it printed OK over a build
    # that died on its first `#include <Python.h>`.
    if header="$(python_header "$py_path")"; then
      echo "   build Python headers: yes ($header)"
    else
      echo "   build Python headers: MISSING — $py_path has no Python.h"
      echo "                 (on Debian/Ubuntu: apt install python${py_version%.*}-dev,"
      echo "                 or set KILIX_PYTHON to an interpreter that has its headers)"
      ok=0
    fi
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
  python3 -c 'import ensurepip, sys, venv; assert sys.version_info >= (3, 11)' \
    2>/dev/null \
    && echo "   PDF runtime Python: yes (>= 3.11 with venv/ensurepip)" \
    || { echo "   PDF runtime Python: MISSING (need >= 3.11 plus python3-venv)"; ok=0; }
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
  local pc pkgs="gcc make pkgconf-pkg-config git curl zstd golang python3 python3-devel python3-pillow simde-devel wayland-devel wayland-protocols-devel SDL2-devel SDL2_image-devel libsndfile-devel zlib-devel fluidsynth-devel fluid-soundfont-gm libssh2-devel brotli-devel"
  local -a packages
  for pc in $PC_DEPS; do pkgs="$pkgs pkgconfig($pc)"; done
  echo "==> Fedora/RHEL detected — installing system-wide via dnf"
  read -r -a packages <<<"$pkgs"
  sudo dnf install -y "${packages[@]}"
}

# Which JACK development package to name, decided by the JACK runtime that is
# already installed.
#
# libfluidsynth-dev depends on `libjack-dev | libjack-jackd2-dev`, and the two
# runtimes behind them (jack1's libjack0, jack2's libjack-jackd2-0) conflict.
# Left to itself apt takes the first alternative, so on a jack2 machine the
# install REMOVES libjack-jackd2-0 and breaks everything linked to it (mpv,
# libavdevice, libasound2-plugins, ...). Naming jack2's package unconditionally
# only moves the damage: on a jack1 machine that removes libjack0 instead. So
# follow what is there, and only choose when nothing is. Whatever this prints,
# the invariant is that installing build dependencies removes nothing, and
# that is what tests/test_build_deps_install.py asserts.
debian_jack_dev_package() {
  local runtime state
  for runtime in libjack-jackd2-0 libjack0; do
    while IFS= read -r state; do
      case "$state" in
        installed|unpacked|half-configured|half-installed|triggers-awaited|triggers-pending)
          case "$runtime" in
            libjack0) echo libjack-dev ;;
            *) echo libjack-jackd2-dev ;;
          esac
          return 0 ;;
      esac
    done < <(dpkg-query -W -f='${db:Status-Status}\n' "$runtime" 2>/dev/null || true)
  done
  # No JACK runtime at all: nothing can conflict, so either removes nothing.
  echo libjack-jackd2-dev
}

# Where machine-wide user-unit enablement lives. Assigned, never read from the
# environment; the tests source this file and point it at a fixture root.
SYSTEMD_USER_CONF=/etc/systemd/user

# User units that a package this backend installs can enable for every login,
# and that hold the default sound card, which dictation records from.
#
# fluidsynth.service: Kilix Amp links libfluidsynth and never runs the player,
# but on Debian the only package with fluidsynth.pc is libfluidsynth-dev, and it
# depends on `fluidsynth (= <same version>)`, the player. So leaving `fluidsynth`
# out of the list does not keep the player off: it arrives anyway, and its
# postinst enables fluidsynth.service machine-wide on first install.
DEBIAN_AUDIO_HOLDOFF_UNITS="fluidsynth.service"

# Every machine-wide enablement link of those units, one per line.
debian_audio_enablements() {
  local unit link
  for unit in $DEBIAN_AUDIO_HOLDOFF_UNITS; do
    for link in "$SYSTEMD_USER_CONF"/*.wants/"$unit" "$SYSTEMD_USER_CONF"/*.requires/"$unit"; do
      if [ -L "$link" ]; then printf '%s\n' "$link"; fi
    done
  done
}

# Remove the enablement links this install created, and say so loudly. $1 is
# debian_audio_enablements from before the install: any link in it existed
# already, whoever made it, and is never touched. Only the machine-wide
# directory is read, so a user's own `systemctl --user enable` is out of reach.
# The package's record of the link is left in place, and Debian's postinst
# re-enables a unit on upgrade only while every recorded link still exists.
debian_audio_holdoff() {
  local before="$1" link unit
  while IFS= read -r link; do
    [ -n "$link" ] || continue
    if printf '%s\n' "$before" | grep -qxF -- "$link"; then continue; fi
    unit="${link##*/}"
    if ! sudo rm -f -- "$link"; then
      log "WARNING: could not remove $link: $unit will start at every login"
      log "and hold the default sound card; remove it with: sudo rm $link"
      return 1
    fi
    log "=================================================================="
    log "WARNING: the FluidSynth development package Kilix Amp builds against"
    log "pulled in the fluidsynth player, whose package enabled $unit"
    log "for every login ($link)."
    log "That daemon holds the default sound card, which dictation records"
    log "from. Kilix Amp never runs it, so this installer removed that link."
    log "The player stays installed. To have the daemon anyway:"
    log "    sudo systemctl --global enable $unit"
    log "or, for one account only:  systemctl --user enable $unit"
    log "=================================================================="
  done < <(debian_audio_enablements)
}

debian_install() {
  local jack_dev; jack_dev="$(debian_jack_dev_package)"
  local pkgs="build-essential cmake pkg-config git curl zstd golang-go python3 python3-dev python3-pil python3-venv \
    libx11-dev libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev libxkbcommon-dev \
    libxkbcommon-x11-dev libx11-xcb-dev libdbus-1-dev libgl1-mesa-dev libegl-dev libdrm-dev libfontconfig-dev \
    libpng-dev liblcms2-dev libcairo2-dev libharfbuzz-dev libssl-dev libxxhash-dev \
    libsimde-dev libwayland-dev wayland-protocols \
    libsdl2-dev libsdl2-image-dev libsndfile1-dev zlib1g-dev libfluidsynth-dev $jack_dev fluid-soundfont-gm \
    libssh2-1-dev libbrotli-dev"
  local -a packages
  echo "==> Debian/Ubuntu detected — installing system-wide via apt-get"
  sudo apt-get update
  read -r -a packages <<<"$pkgs"
  local audio_before; audio_before="$(debian_audio_enablements)"
  sudo apt-get install -y "${packages[@]}"
  debian_audio_holdoff "$audio_before"
}

arch_install() {
  local pkgs="base-devel pkgconf git curl zstd go python python-pillow \
    libx11 libxrandr libxinerama libxcursor libxi libxkbcommon mesa dbus fontconfig \
    libpng lcms2 cairo harfbuzz openssl xxhash simde wayland wayland-protocols \
    sdl2 sdl2_image libsndfile zlib fluidsynth soundfont-fluid \
    libssh2 brotli"
  local -a packages
  echo "==> Arch detected — installing system-wide via pacman"
  read -r -a packages <<<"$pkgs"
  sudo pacman -S --needed --noconfirm "${packages[@]}"
}

suse_install() {
  local pkgs="gcc make pkg-config git curl zstd go python3 python3-devel python3-Pillow \
    libX11-devel libXrandr-devel libXinerama-devel libXcursor-devel libXi-devel \
    libxkbcommon-devel libxkbcommon-x11-devel dbus-1-devel Mesa-libGL-devel fontconfig-devel \
    libpng16-devel liblcms2-devel cairo-devel harfbuzz-devel libopenssl-devel libxxhash-devel \
    simde-devel wayland-devel wayland-protocols-devel \
    libSDL2-devel libSDL2_image-devel libsndfile-devel zlib-devel fluidsynth-devel fluid-soundfont-gm \
    libssh2-devel libbrotli-devel"
  local -a packages
  echo "==> openSUSE detected — installing system-wide via zypper"
  read -r -a packages <<<"$pkgs"
  sudo zypper --non-interactive install "${packages[@]}"
}

# ---- dispatch ----------------------------------------------------------------
# Sourced (the tests do this to drive one backend against fixture roots): stop
# here, having defined the functions and changed nothing. This keys on how the
# file was entered, not on a variable, so no environment setting can make an
# executed run skip its work and still exit 0.
if [ "${BASH_SOURCE[0]}" != "$0" ]; then return 0; fi

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
  log "install manually: a C compiler, make, pkg-config, Go, git, curl, zstd,"
  log "Python 3.11+ with venv + Pillow, and the dev libs for: $PC_DEPS $AMP_PC_DEPS"
  exit 1
fi

ensure_go_toolchain
echo
verify
echo
echo "==> Done. Build the clickable-chrome fork with:  ./kilix --build"
