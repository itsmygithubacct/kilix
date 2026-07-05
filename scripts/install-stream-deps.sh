#!/usr/bin/env bash
# kilix — no-sudo installer for the streaming (pixel-plane) dependencies.
#
# `kilix serve/run --serve/desktop` need Xvfb, an Xvnc (TigerVNC), vncpasswd,
# and python3-xlib. On a box where you have no root, this fetches those packages
# (and any of their library/data deps that aren't already installed) with
# `apt-get download`, unpacks them with `dpkg -x` into a private prefix under
# ~/.local/share/kilix/deps, and writes stream-env.sh with the
# PATH/LD_LIBRARY_PATH/PYTHONPATH/XKB/font settings the kilix launcher sources.
#
# Needs: apt sources + network (for apt-get download), dpkg, apt-cache. No root.
# Idempotent: re-running only fetches what's still missing.
#
# Usage:  scripts/install-stream-deps.sh            # install
#         scripts/install-stream-deps.sh --verify   # just re-check + print env
set -euo pipefail

DATA="${XDG_DATA_HOME:-$HOME/.local/share}/kilix"
PREFIX="$DATA/deps"
ENVFILE="$DATA/stream-env.sh"
MARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
# Debian multiarch triplet (e.g. x86_64-linux-gnu) for the lib dir.
TRIPLET="$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo x86_64-linux-gnu)"

# Packages that provide the tools we need. Their recursive deps are resolved
# below; anything already installed system-wide is skipped.
TARGETS="xvfb tigervnc-standalone-server tigervnc-common python3-xlib x11-xkb-utils xfonts-base xauth"

write_env() {
  mkdir -p "$DATA"
  local xkb="" fonts=""
  [ -d "$PREFIX/usr/share/X11/xkb" ] && xkb="$PREFIX/usr/share/X11/xkb"
  for d in "$PREFIX/usr/share/fonts/X11/misc" "$PREFIX/usr/share/fonts/X11/75dpi" \
           "$PREFIX/usr/share/fonts/X11/100dpi" "$PREFIX/usr/share/fonts/X11/Type1"; do
    [ -d "$d" ] && fonts="${fonts:+$fonts,}$d"
  done
  {
    echo "# kilix streaming deps — sourced by the kilix launcher. Auto-generated."
    # APPEND, not prepend: the unpacked prefix ships a partial python3 that would
    # otherwise shadow the system one (and its websockets). System tools win;
    # the prefix only fills genuine gaps (Xvfb/Xvnc/xkbcomp).
    echo "export PATH=\"\$PATH:$PREFIX/usr/bin\""
    echo "export LD_LIBRARY_PATH=\"$PREFIX/usr/lib/$TRIPLET:$PREFIX/usr/lib:\${LD_LIBRARY_PATH:-}\""
    echo "export PYTHONPATH=\"\${PYTHONPATH:-}:$PREFIX/usr/lib/python3/dist-packages\""
    [ -n "$xkb" ]   && echo "export XKB_CONFIG_ROOT=\"$xkb\""
    [ -n "$fonts" ] && echo "export KILIX_XFONTS=\"$fonts\""
  } > "$ENVFILE"
  echo "==> wrote $ENVFILE"
}

verify() {
  # shellcheck disable=SC1090
  [ -f "$ENVFILE" ] && . "$ENVFILE"
  local ok=1
  echo "==> verifying:"
  for t in Xvfb; do
    if command -v "$t" >/dev/null 2>&1; then echo "   $t: $(command -v "$t")"; else echo "   $t: MISSING"; ok=0; fi
  done
  echo "   vncpasswd: not required (kilix generates VNC passwords itself)"
  local xvnc; xvnc="$(command -v Xvnc || command -v Xtigervnc || true)"
  [ -n "$xvnc" ] && echo "   Xvnc: $xvnc" || { echo "   Xvnc: MISSING"; ok=0; }
  python3 -c "import Xlib; print('   python-xlib:', Xlib.__version__)" 2>/dev/null || { echo "   python-xlib: MISSING"; ok=0; }
  python3 -c "import websockets; print('   websockets:', websockets.__version__)" 2>/dev/null || echo "   websockets: MISSING (pip install --user websockets)"
  # a real smoke: can the unpacked Xvnc actually print its version (i.e. its libs resolve)?
  if [ -n "$xvnc" ]; then
    if "$xvnc" -version >/dev/null 2>&1 || "$xvnc" -help >/dev/null 2>&1; then
      echo "   Xvnc runs: yes ($("$xvnc" -version 2>&1 | head -1))"
    else
      echo "   Xvnc runs: NO — missing libraries. Missing shared libs:"
      ldd "$xvnc" 2>/dev/null | grep -i "not found" | sed 's/^/     /' || true
      ok=0
    fi
  fi
  [ "$ok" = 1 ] && echo "==> OK — streaming deps ready." || echo "==> INCOMPLETE — see above."
  return 0
}

if [ "${1:-}" = "--verify" ]; then write_env; verify; exit 0; fi

command -v apt-get >/dev/null || { echo "need apt-get (Debian/Ubuntu)"; exit 1; }
command -v dpkg    >/dev/null || { echo "need dpkg"; exit 1; }
mkdir -p "$PREFIX"

echo "==> resolving dependency closure for: $TARGETS"
closure="$(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
             --no-breaks --no-replaces --no-enhances $TARGETS 2>/dev/null \
           | grep -E '^[a-zA-Z0-9]' | grep -v '^<' | sort -u)"

need=""
for p in $closure; do
  if ! dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed"; then
    need="$need $p"
  fi
done
need="$(echo "$need" | xargs -n1 2>/dev/null | sort -u | xargs)"
echo "==> $(echo "$need" | wc -w) package(s) not installed system-wide; fetching those."

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
got=0
for p in $need; do
  if apt-get download "$p" >/dev/null 2>&1; then got=$((got+1)); else echo "   (skip unavailable: $p)"; fi
done
echo "==> downloaded $got .deb(s); unpacking into $PREFIX"
shopt -s nullglob
for deb in *.deb; do dpkg -x "$deb" "$PREFIX"; done
shopt -u nullglob

write_env
verify
echo
echo "==> Done. The kilix launcher auto-sources $ENVFILE, so:"
echo "      kilix run --serve <app>     kilix desktop     kilix serve"
echo "    now have their Xvfb/Xvnc/vncpasswd/python-xlib."
