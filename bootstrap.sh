#!/usr/bin/env bash
# kilix bootstrap — ensure a local, up-to-date PREBUILT kitty binary bundle.
#
# Downloads the official prebuilt kitty release (no compilation, no build deps)
# into Kilix's per-user storage. Re-pulls only when kitty is missing or older than
# the latest published release. Release builders can pin the bundle with
# KILIX_PREBUILT_VERSION and verify it with KILIX_PREBUILT_SHA256.
#
#   ./bootstrap.sh              # install/update if missing or outdated
#   ./bootstrap.sh --force      # always re-download the configured release
#   ./bootstrap.sh --if-stale   # only check the network at most once per 24h
#   ./bootstrap.sh --allow-unverified  # explicitly trust an unverified asset
#   KILIX_PREBUILT_VERSION=0.47.0 KILIX_PREBUILT_SHA256=... ./bootstrap.sh
set -euo pipefail
umask 077

GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_PREBUILT_HOME="${KILIX_PREBUILT_HOME:-$KILIX_STORAGE_HOME/prebuilt/kitty.app}"
KILIX_STATE_DIRECTORY="${KILIX_STATE_DIRECTORY:-$KILIX_STORAGE_HOME/state}"
KILIX_SESSION_HOME="${KILIX_SESSION_HOME:-$KILIX_STORAGE_HOME/session}"
APP="$KILIX_PREBUILT_HOME"
APP_PARENT="$(dirname -- "$APP")"
BIN="$APP/bin/kitty"
SHA_STAMP="$APP/.kitty.txz.sha256"
STATE_DIR="$KILIX_STATE_DIRECTORY"
STAMP="$STATE_DIR/prebuilt-last-update-check"
VERSION_FEED="https://sw.kovidgoyal.net/kitty/current-version.txt"
PINNED_VERSION="${KILIX_PREBUILT_VERSION:-}"
PINNED_SHA256="${KILIX_PREBUILT_SHA256:-}"

FORCE=0; IF_STALE=0
ALLOW_UNVERIFIED="${KILIX_ALLOW_UNVERIFIED_PREBUILT:-0}"
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    --if-stale) IF_STALE=1 ;;
    --allow-unverified) ALLOW_UNVERIFIED=1 ;;
    *) printf 'kilix: unknown option: %s\n' "$a" >&2; exit 2 ;;
  esac
done
log(){ printf 'kilix: %s\n' "$*" >&2; }

mkdir -p "$STATE_DIR"
chmod 0700 "$KILIX_STORAGE_HOME" "$STATE_DIR" 2>/dev/null || true

if [ -n "$PINNED_SHA256" ] && [ -z "$PINNED_VERSION" ]; then
  log "KILIX_PREBUILT_SHA256 requires KILIX_PREBUILT_VERSION"
  exit 2
fi

# --if-stale: if we have a binary and checked within the last day, do nothing.
if [ "$IF_STALE" = 1 ] && [ "$FORCE" = 0 ] && [ -z "$PINNED_VERSION" ] \
     && [ -x "$BIN" ] && [ -f "$STAMP" ]; then
  if find "$STAMP" -mtime -1 2>/dev/null | grep -q .; then exit 0; fi
fi

case "$(uname -m)" in
  x86_64|amd64)  KARCH=x86_64 ;;
  aarch64|arm64) KARCH=arm64 ;;
  *) log "unsupported arch: $(uname -m)"; exit 1 ;;
esac

have=""
if [ -x "$BIN" ]; then
  have="$("$BIN" --version 2>/dev/null | awk 'NR==1{print $2}')" || true
fi

if [ -n "$PINNED_VERSION" ]; then
  latest="${PINNED_VERSION#v}"
else
  latest="$(curl -fsSL --max-time 15 "$VERSION_FEED" 2>/dev/null || true)"
fi

if [ -z "$latest" ]; then
  if [ -x "$BIN" ]; then log "offline — keeping existing kitty ${have:-?}"; exit 0; fi
  log "cannot reach $VERSION_FEED and no local kitty is present"; exit 1
fi
if [ -z "$PINNED_VERSION" ]; then
  touch "$STAMP" 2>/dev/null || true   # record a successful version check
fi

if [ "$FORCE" = 0 ] && [ -n "$have" ] && [ "$have" = "$latest" ]; then
  if [ -n "$PINNED_SHA256" ] && [ "$(cat "$SHA_STAMP" 2>/dev/null || true)" != "$PINNED_SHA256" ]; then
    log "kitty $have is present but checksum stamp is missing/mismatched; re-downloading"
  else
    log "kitty $have is current"; exit 0
  fi
fi

url="https://github.com/kovidgoyal/kitty/releases/download/v${latest}/kitty-${latest}-${KARCH}.txz"
if [ -z "$PINNED_SHA256" ] && [ "$ALLOW_UNVERIFIED" != 1 ]; then
  log "refusing to install an unverified prebuilt bundle"
  log "set KILIX_PREBUILT_VERSION plus KILIX_PREBUILT_SHA256 (recommended),"
  log "or rerun with --allow-unverified after reviewing the source URL: $url"
  exit 1
fi
if [ -z "$PINNED_SHA256" ]; then
  log "WARNING: checksum verification explicitly disabled for $url"
fi
if ! mkdir -p -- "$APP_PARENT"; then
  log "cannot create prebuilt engine directory: $APP_PARENT"
  exit 1
fi
if ! mkdir -p -- "$KILIX_SESSION_HOME"; then
  log "cannot create bootstrap session directory: $KILIX_SESSION_HOME"
  exit 1
fi
log "fetching kitty $latest ($KARCH)${have:+ — replacing $have}"
chmod 0700 "$KILIX_SESSION_HOME" 2>/dev/null || true
tmp="$(mktemp -d "$KILIX_SESSION_HOME/bootstrap.XXXXXX")"; trap 'rm -rf "$tmp"' EXIT
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
