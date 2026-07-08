#!/usr/bin/env bash
# kilix bootstrap — ensure a local, up-to-date PREBUILT kitty binary bundle.
#
# Downloads the official prebuilt kitty release (no compilation, no build deps)
# into $KILIX_HOME/kitty.app. Re-pulls only when kitty is missing or older than
# the latest published release. Release builders can pin the bundle with
# KILIX_PREBUILT_VERSION and verify it with KILIX_PREBUILT_SHA256.
#
#   ./bootstrap.sh              # install/update if missing or outdated
#   ./bootstrap.sh --force      # always re-download the latest
#   ./bootstrap.sh --if-stale   # only check the network at most once per 24h
#   KILIX_PREBUILT_VERSION=0.47.0 KILIX_PREBUILT_SHA256=... ./bootstrap.sh
set -euo pipefail

KILIX_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$KILIX_HOME/kitty.app"
BIN="$APP/bin/kitty"
SHA_STAMP="$APP/.kitty.txz.sha256"
STAMP="$KILIX_HOME/.last-update-check"
VERSION_FEED="https://sw.kovidgoyal.net/kitty/current-version.txt"
PINNED_VERSION="${KILIX_PREBUILT_VERSION:-}"
PINNED_SHA256="${KILIX_PREBUILT_SHA256:-}"

FORCE=0; IF_STALE=0
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    --if-stale) IF_STALE=1 ;;
    *) printf 'kilix: unknown option: %s\n' "$a" >&2; exit 2 ;;
  esac
done
log(){ printf 'kilix: %s\n' "$*" >&2; }

# --if-stale: if we have a binary and checked within the last day, do nothing.
if [ "$IF_STALE" = 1 ] && [ "$FORCE" = 0 ] && [ -x "$BIN" ] && [ -f "$STAMP" ]; then
  if find "$STAMP" -mtime -1 2>/dev/null | grep -q .; then exit 0; fi
fi

case "$(uname -m)" in
  x86_64|amd64)  KARCH=x86_64 ;;
  aarch64|arm64) KARCH=arm64 ;;
  *) log "unsupported arch: $(uname -m)"; exit 1 ;;
esac

have=""
[ -x "$BIN" ] && have="$("$BIN" --version 2>/dev/null | awk 'NR==1{print $2}')" || true

if [ -n "$PINNED_VERSION" ]; then
  latest="${PINNED_VERSION#v}"
else
  latest="$(curl -fsSL --max-time 15 "$VERSION_FEED" 2>/dev/null || true)"
fi

if [ -z "$latest" ]; then
  if [ -x "$BIN" ]; then log "offline — keeping existing kitty ${have:-?}"; exit 0; fi
  log "cannot reach $VERSION_FEED and no local kitty is present"; exit 1
fi
[ -z "$PINNED_VERSION" ] && touch "$STAMP" 2>/dev/null || true   # record a *successful* version check

if [ "$FORCE" = 0 ] && [ -n "$have" ] && [ "$have" = "$latest" ]; then
  if [ -n "$PINNED_SHA256" ] && [ "$(cat "$SHA_STAMP" 2>/dev/null || true)" != "$PINNED_SHA256" ]; then
    log "kitty $have is present but checksum stamp is missing/mismatched; re-downloading"
  else
    log "kitty $have is current"; exit 0
  fi
fi

url="https://github.com/kovidgoyal/kitty/releases/download/v${latest}/kitty-${latest}-${KARCH}.txz"
log "fetching kitty $latest ($KARCH)${have:+ — replacing $have}"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fL --retry 3 --max-time 300 -o "$tmp/kitty.txz" "$url"
if [ -n "$PINNED_SHA256" ]; then
  command -v sha256sum >/dev/null 2>&1 || { log "KILIX_PREBUILT_SHA256 set but sha256sum is missing"; exit 1; }
  printf '%s  %s\n' "$PINNED_SHA256" "$tmp/kitty.txz" | sha256sum -c --status \
    || { log "checksum mismatch for $url"; exit 1; }
  log "verified kitty bundle sha256"
fi
mkdir -p "$tmp/app"
tar -xJf "$tmp/kitty.txz" -C "$tmp/app"
[ -x "$tmp/app/bin/kitty" ] || { log "unexpected bundle layout:"; ls -la "$tmp/app" >&2; exit 1; }

# Swap in the new app dir, restoring the previous one if the move fails
# (mktemp is often on another filesystem, so this mv is a copy+unlink, not atomic).
rm -rf "$APP.old"
[ -e "$APP" ] && mv "$APP" "$APP.old"
if ! mv "$tmp/app" "$APP"; then
  log "install failed; restoring previous kitty.app"
  [ -e "$APP.old" ] && mv "$APP.old" "$APP"
  exit 1
fi
rm -rf "$APP.old"
[ -n "$PINNED_SHA256" ] && printf '%s\n' "$PINNED_SHA256" > "$SHA_STAMP"
log "installed kitty $("$BIN" --version 2>/dev/null | awk 'NR==1{print $2}') -> $APP"
