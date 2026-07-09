#!/usr/bin/env bash
set -euo pipefail

INSTALL_URL="https://chatgpt.com/codex/install.sh"

ensure_curl() {
  if command -v curl >/dev/null 2>&1; then
    return 0
  fi
  if command -v apt >/dev/null 2>&1; then
    sudo apt update
    sudo apt install -y ca-certificates curl
    return 0
  fi
  echo "curl is required to install Codex." >&2
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

echo "Installing Codex..."
curl -fsSL "$INSTALL_URL" | sh

echo
if command -v codex >/dev/null 2>&1; then
  codex --version || true
else
  echo "Codex finished installing, but 'codex' is not on PATH yet."
  echo "Open a new terminal session, or add $HOME/.local/bin to PATH."
fi
