#!/usr/bin/env bash
set -euo pipefail

URL="https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"

if ! command -v apt >/dev/null 2>&1; then
  echo "Google Chrome installer currently supports Debian/Ubuntu apt systems." >&2
  exit 1
fi

arch="$(dpkg --print-architecture 2>/dev/null || true)"
if [ "$arch" != "amd64" ]; then
  echo "Google Chrome for Linux is only available for amd64; detected ${arch:-unknown}." >&2
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
deb="$work/google-chrome-stable_current_amd64.deb"

sudo apt update
sudo apt install -y ca-certificates curl
curl -fL "$URL" -o "$deb"
sudo apt install -y "$deb"

echo
if command -v google-chrome >/dev/null 2>&1; then
  google-chrome --version || true
elif command -v google-chrome-stable >/dev/null 2>&1; then
  google-chrome-stable --version || true
else
  echo "Google Chrome installed, but no chrome executable was found on PATH."
fi
