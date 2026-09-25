#!/usr/bin/env bash
set -euo pipefail

INSTALL_URL="https://claude.ai/install.sh"
# Bootstrap fetched 2026-09-25. The script may still download a moving release.
INSTALL_SHA256="3a68d3406cf674e17bed1733a4dcf37805e2e47d87417700007d7e1aa766a944"

ensure_curl() {
  if command -v curl >/dev/null 2>&1; then
    return 0
  fi
  if command -v apt >/dev/null 2>&1; then
    sudo apt update
    sudo apt install -y ca-certificates curl
    return 0
  fi
  echo "curl is required to install Claude Code." >&2
  return 1
}

ensure_local_bin_path() {
  mkdir -p "$HOME/.local/bin"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) return 0 ;;
  esac
  export PATH="$HOME/.local/bin:$PATH"
  if [ -f "$HOME/.profile" ] \
      && ! grep -qs 'HOME/.local/bin\|~/.local/bin' "$HOME/.profile"; then
    {
      echo
      echo '# Add user-local command installs to PATH.'
      echo 'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac'
    } >> "$HOME/.profile"
  fi
}

ensure_curl
ensure_local_bin_path

echo "Installing Claude Code..."
stage="$(mktemp)"
trap 'rm -f "$stage"' EXIT
curl -fsSL "$INSTALL_URL" -o "$stage"
echo "${INSTALL_SHA256}  ${stage}" | sha256sum -c -
bash "$stage"

echo
if command -v claude >/dev/null 2>&1; then
  claude --version || true
else
  echo "Claude Code finished installing, but 'claude' is not on PATH yet."
  echo "Open a new terminal session, or add $HOME/.local/bin to PATH."
fi
