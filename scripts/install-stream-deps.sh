#!/usr/bin/env bash
# kilix — installer for the streaming (pixel-plane) dependencies: an Xvnc
# (TigerVNC), Xvfb, and python3-xlib (kilix generates VNC passwords itself, so
# vncpasswd is not required). Two backends, auto-detected:
#
#   Debian/Ubuntu : NO ROOT — apt-get download + dpkg -x into a private prefix
#                   (~/.local/gpu_terminal/kilix/data/deps) + a stream-env.sh
#                   sources. Also fetches any missing library/data deps.
#   Fedora/RHEL   : sudo dnf install (system-wide).
#
# Usage:  scripts/install-stream-deps.sh            # install
#         scripts/install-stream-deps.sh --verify   # re-check + print status
set -euo pipefail
umask 077

GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
DATA="${KILIX_DATA_HOME:-$KILIX_STORAGE_HOME/data}"
SESSION="${KILIX_SESSION_HOME:-$KILIX_STORAGE_HOME/session}"
PREFIX="$DATA/deps"
ENVFILE="$DATA/stream-env.sh"
TRIPLET="$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo x86_64-linux-gnu)"

# ---- shared verify -----------------------------------------------------------
verify() {
  # shellcheck disable=SC1090
  [ -f "$ENVFILE" ] && . "$ENVFILE"
  local ok=1
  echo "==> verifying:"
  command -v Xvfb >/dev/null 2>&1 && echo "   Xvfb: $(command -v Xvfb)" || { echo "   Xvfb: MISSING"; ok=0; }
  local xvnc; xvnc="$(command -v Xvnc || command -v Xtigervnc || true)"
  [ -n "$xvnc" ] && echo "   Xvnc: $xvnc" || { echo "   Xvnc: MISSING"; ok=0; }
  echo "   vncpasswd: not required (kilix generates VNC passwords itself)"
  python3 -c "import Xlib; print('   python-xlib:', Xlib.__version__)" 2>/dev/null || { echo "   python-xlib: MISSING"; ok=0; }
  python3 -c "import websockets; print('   websockets:', websockets.__version__)" 2>/dev/null || echo "   websockets: MISSING (pip install --user websockets)"
  command -v pactl >/dev/null 2>&1 && echo "   pactl (audio): $(command -v pactl)" || echo "   pactl (audio): none (video-only)"
  if [ -n "$xvnc" ]; then
    if "$xvnc" -version >/dev/null 2>&1 || "$xvnc" -help >/dev/null 2>&1; then
      echo "   Xvnc runs: yes ($("$xvnc" -version 2>&1 | head -1))"
    else
      echo "   Xvnc runs: NO — missing libs:"; ldd "$xvnc" 2>/dev/null | grep -i "not found" | sed 's/^/     /'; ok=0
    fi
  fi
  [ "$ok" = 1 ] && echo "==> OK — streaming deps ready." || echo "==> INCOMPLETE — see above."
  return 0
}

# ---- Fedora / dnf (system-wide, needs sudo) ----------------------------------
fedora_install() {
  local pkgs="tigervnc-server xorg-x11-server-Xvfb python3-xlib python3-pillow python3-websockets"
  echo "==> Fedora/RHEL detected — installing system-wide via dnf: $pkgs"
  sudo dnf install -y $pkgs
  rm -f "$ENVFILE"          # system-wide install: launcher needs no prefix env
  verify
}

# ---- Debian / apt (no root, unpack into a prefix) ----------------------------
write_env() {
  mkdir -p "$DATA"
  chmod 0700 "$KILIX_STORAGE_HOME" "$DATA" 2>/dev/null || true
  local xkb="" fonts="" d
  [ -d "$PREFIX/usr/share/X11/xkb" ] && xkb="$PREFIX/usr/share/X11/xkb"
  for d in "$PREFIX/usr/share/fonts/X11/misc" "$PREFIX/usr/share/fonts/X11/75dpi" \
           "$PREFIX/usr/share/fonts/X11/100dpi" "$PREFIX/usr/share/fonts/X11/Type1"; do
    [ -d "$d" ] && fonts="${fonts:+$fonts,}$d"
  done
  {
    echo "# kilix streaming deps — sourced by the kilix launcher. Auto-generated."
    # APPEND, not prepend: the prefix ships a partial python3 that would shadow
    # the system one (and its websockets). System tools win; prefix fills gaps.
    echo "export PATH=\"\$PATH:$PREFIX/usr/bin\""
    echo "export LD_LIBRARY_PATH=\"$PREFIX/usr/lib/$TRIPLET:$PREFIX/usr/lib:\${LD_LIBRARY_PATH:-}\""
    echo "export PYTHONPATH=\"\${PYTHONPATH:-}:$PREFIX/usr/lib/python3/dist-packages\""
    [ -n "$xkb" ]   && echo "export XKB_CONFIG_ROOT=\"$xkb\""
    [ -n "$fonts" ] && echo "export KILIX_XFONTS=\"$fonts\""
  } > "$ENVFILE"
  echo "==> wrote $ENVFILE"
}

debian_install() {
  command -v dpkg >/dev/null || { echo "need dpkg"; exit 1; }
  mkdir -p "$PREFIX"
  local targets="xvfb tigervnc-standalone-server tigervnc-common python3-xlib x11-xkb-utils xfonts-base xauth"
  echo "==> Debian/Ubuntu detected — no-root install into $PREFIX"
  echo "==> resolving dependency closure for: $targets"
  local closure need="" p
  closure="$(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
               --no-breaks --no-replaces --no-enhances $targets 2>/dev/null \
             | grep -E '^[a-zA-Z0-9]' | grep -v '^<' | sort -u)"
  for p in $closure; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || need="$need $p"
  done
  need="$(echo "$need" | xargs -n1 2>/dev/null | sort -u | xargs)"
  echo "==> $(echo "$need" | wc -w) package(s) not installed system-wide; fetching those."
  mkdir -p "$SESSION"
  chmod 0700 "$SESSION" 2>/dev/null || true
  local WORK; WORK="$(mktemp -d "$SESSION/stream-deps.XXXXXX")"; trap 'rm -rf "$WORK"' EXIT
  cd "$WORK"; local got=0
  for p in $need; do apt-get download "$p" >/dev/null 2>&1 && got=$((got+1)) || echo "   (skip: $p)"; done
  echo "==> downloaded $got .deb(s); unpacking into $PREFIX"
  shopt -s nullglob; for deb in *.deb; do dpkg -x "$deb" "$PREFIX"; done; shopt -u nullglob
  write_env
  verify
}

# ---- dispatch ----------------------------------------------------------------
if [ "${1:-}" = "--verify" ]; then verify; exit 0; fi

if command -v dnf >/dev/null 2>&1 && command -v rpm >/dev/null 2>&1; then
  fedora_install
elif command -v apt-get >/dev/null 2>&1; then
  debian_install
else
  echo "kilix: unsupported distro (need dnf or apt-get)"; exit 1
fi
echo
echo "==> Done. kilix run --serve / --hls / --audio and kilix share now have their deps."
