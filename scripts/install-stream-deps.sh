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
# The Python side is a virtualenv rather than a system install, because the
# Debian path is deliberately root-free and `pip install --user` has not
# worked on Debian since it started marking its interpreter externally
# managed.  uv builds it when it is there, venv and pip when it is not.
#
# Usage:  scripts/install-stream-deps.sh                # install
#         scripts/install-stream-deps.sh --verify       # re-check + status
#         scripts/install-stream-deps.sh --python-deps  # just the venv
set -euo pipefail
umask 077

GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
DATA="${KILIX_DATA_HOME:-$KILIX_STORAGE_HOME/data}"
SESSION="${KILIX_SESSION_HOME:-$KILIX_STORAGE_HOME/session}"
PREFIX="$DATA/deps"
ENVFILE="$DATA/stream-env.sh"
# Pure-Python dependencies only.  Their site-packages goes on PYTHONPATH for
# the system interpreter, which is the same trick this script already plays
# with the unpacked dist-packages - and it is only safe because nothing
# compiled lives here.  Anything with a binary extension belongs in $PREFIX.
PYDEPS="$DATA/stream-python"
KILIX_UV="${KILIX_UV:-uv}"
KILIX_PYTHON="${KILIX_PYTHON:-python3}"

have_uv() { command -v "$KILIX_UV" >/dev/null 2>&1; }

pydeps_site() {
  local site
  for site in "$PYDEPS"/lib/python3*/site-packages; do
    [ -d "$site" ] && { printf '%s\n' "$site"; return 0; }
  done
  return 1
}

# websockets, for `kilix run --serve` and `kilix share`.  Skipped entirely
# when the system already has it: a second copy on PYTHONPATH is a second
# version to be surprised by.
python_deps() {
  local site
  # Probed with the interpreter that will do the importing, which is the
  # one KILIX_PYTHON names - asking a different python whether the import
  # works answers a different question.
  if "$KILIX_PYTHON" -c 'import websockets' >/dev/null 2>&1; then
    echo "==> websockets: already available to $KILIX_PYTHON"
    return 0
  fi
  if site="$(pydeps_site)" && \
     PYTHONPATH="$site" "$KILIX_PYTHON" -c 'import websockets' >/dev/null 2>&1; then
    echo "==> websockets: already in $site"
    return 0
  fi
  mkdir -p "$DATA"
  chmod 0700 "$DATA" 2>/dev/null || true
  if have_uv; then
    local interpreter
    # Resolved to a path first: `uv venv --python python3` is a request uv
    # may satisfy from an interpreter it downloads itself, and a venv built
    # on a different Python than the one that will import from it is a
    # PYTHONPATH that does nothing.
    interpreter="$(command -v "$KILIX_PYTHON" 2>/dev/null || true)"
    [ -n "$interpreter" ] || { echo "   no interpreter called $KILIX_PYTHON"; return 1; }
    echo "==> python deps: uv venv on $interpreter"
    "$KILIX_UV" venv --python "$interpreter" "$PYDEPS" >/dev/null || return 1
    "$KILIX_UV" pip install --python "$PYDEPS" websockets >/dev/null || return 1
  else
    echo "==> python deps: venv + pip (uv is not installed)"
    "$KILIX_PYTHON" -m venv "$PYDEPS" >/dev/null 2>&1 || {
      echo "   could not create a virtualenv (install python3-venv or uv)"
      return 1
    }
    "$PYDEPS/bin/pip" install --quiet websockets >/dev/null 2>&1 || return 1
  fi
  site="$(pydeps_site)" || { echo "   the virtualenv has no site-packages"; return 1; }
  echo "==> websockets: $site"
}
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
  python3 -c "import websockets; print('   websockets:', websockets.__version__)" 2>/dev/null \
    || echo "   websockets: MISSING (scripts/install-stream-deps.sh --python-deps)"
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
  local xkb="" fonts="" d site=""
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
    if site="$(pydeps_site)"; then
      echo "export PYTHONPATH=\"\${PYTHONPATH:-}:$PREFIX/usr/lib/python3/dist-packages:$site\""
    else
      echo "export PYTHONPATH=\"\${PYTHONPATH:-}:$PREFIX/usr/lib/python3/dist-packages\""
    fi
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
  cd - >/dev/null || true
  python_deps || echo "==> websockets not installed; `kilix run --serve` will refuse"
  write_env
  verify
}

# ---- dispatch ----------------------------------------------------------------
if [ "${1:-}" = "--verify" ]; then verify; exit 0; fi
if [ "${1:-}" = "--python-deps" ]; then python_deps && write_env && verify; exit 0; fi

if command -v dnf >/dev/null 2>&1 && command -v rpm >/dev/null 2>&1; then
  fedora_install
elif command -v apt-get >/dev/null 2>&1; then
  debian_install
else
  echo "kilix: unsupported distro (need dnf or apt-get)"; exit 1
fi
echo
echo "==> Done. kilix run --serve / --hls / --audio and kilix share now have their deps."
