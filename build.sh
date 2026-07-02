#!/usr/bin/env bash
# Build the forked kitty (clickable-chrome) that lives in ./src.
#
# Requirements:
#   - Go >= 1.26, a C compiler (gcc/clang), pkg-config
#   - kitty's X11 build deps: x11 xrandr xinerama xcursor xi xkbcommon
#     xkbcommon-x11 x11-xcb dbus-1 gl, plus fontconfig
#   (kitty's first build also downloads a prebuilt deps bundle for the rest.)
#
# If a machine-specific toolchain env file exists at ~/.kitty-fork-buildenv it is
# sourced first (handy when Go / dev headers are installed under $HOME rather than
# system-wide). The runnable binary lands at ./src/kitty/launcher/kitty.
set -euo pipefail

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HOME/.kitty-fork-buildenv" ] && . "$HOME/.kitty-fork-buildenv"

[ -x "$KILIX_HOME/src/dev.sh" ] || { echo "kilix: ./src is missing kitty sources" >&2; exit 1; }
cd "$KILIX_HOME/src"
echo "kilix: building forked kitty in $KILIX_HOME/src ..."
./dev.sh build "$@"
launcher="$KILIX_HOME/src/kitty/launcher/kitty"
[ -x "$launcher" ] || { echo "kilix: build finished but $launcher is missing" >&2; exit 1; }
echo "kilix: built -> $launcher"
