#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Invoked by setup.ps1 in the explicitly selected distribution/user.
set -eu
directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
archive=${1:?Pass the companion archive}
expected=${2:?Pass its SHA256}
dependencies=${3:-}
test "$(id -u)" != 0 || { echo 'Run companion setup as your normal Linux user, not root.' >&2; exit 1; }
case "$expected" in *[!a-fA-F0-9]*|'') echo 'Invalid SHA256' >&2; exit 1;; esac
test "${#expected}" -eq 64 || { echo 'Invalid SHA256 length' >&2; exit 1; }
actual=$(sha256sum "$archive")
test "${actual%% *}" = "$expected" || { echo 'Companion bundle hash mismatch' >&2; exit 1; }
if [ "$dependencies" = --install-dependencies ]; then
    command -v apt-get >/dev/null || { echo 'Install build-essential, zlib development headers, Python 3, curl and PulseAudio clients using your distribution package manager.' >&2; exit 1; }
    sudo apt-get update
    sudo apt-get install -y build-essential zlib1g-dev python3 curl ca-certificates pulseaudio-utils
elif [ -n "$dependencies" ]; then
    echo 'Unknown bootstrap option' >&2; exit 1
fi
for tool in make cc python3; do
    command -v "$tool" >/dev/null || { echo "Missing $tool. Rerun setup with -InstallWslDependencies on Ubuntu/Debian." >&2; exit 1; }
done
managed="$HOME/.local/share/kilix-wsl/current/bin/python3"
if [ -x "$managed" ] && "$managed" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    exec "$managed" -B "$directory/wsl/setup.py" "$archive" "$expected"
fi
if python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    exec python3 -B "$directory/wsl/setup.py" "$archive" "$expected"
fi
# Ubuntu 22.04 has Python 3.10. Bootstrap a versioned uv binary, never a pipe-to-shell installer.
# Digest from the official 0.12.5 release's uv-x86_64-unknown-linux-gnu.tar.gz.sha256.
test "$(uname -m)" = x86_64 || { echo 'This bootstrap supports x86_64 Linux. Provide Python >=3.12 on other architectures.' >&2; exit 1; }
command -v curl >/dev/null || { echo 'Install curl or rerun with -InstallWslDependencies.' >&2; exit 1; }
cache="$HOME/.cache/kilix-setup"
mkdir -p "$cache"
temporary=$(mktemp -d "$cache/bootstrap.XXXXXXXX")
trap 'rm -rf -- "$temporary"' EXIT HUP INT TERM
url=https://github.com/astral-sh/uv/releases/download/0.12.5/uv-x86_64-unknown-linux-gnu.tar.gz
digest=68a509da24b06b4223a1c0175fb5eb5bc79342b76cbeff0cfe51ac3f5b17b6b2
curl --fail --location --proto '=https' --proto-redir '=https' --retry 2 "$url" -o "$temporary/uv.tar.gz"
printf '%s  %s\n' "$digest" "$temporary/uv.tar.gz" | sha256sum --check --status
tar -xzf "$temporary/uv.tar.gz" -C "$temporary" uv-x86_64-unknown-linux-gnu/uv
uv="$temporary/uv-x86_64-unknown-linux-gnu/uv"
export UV_PYTHON_INSTALL_DIR="$HOME/.local/share/kilix-wsl/python"
# Avoid local project configuration and user-supplied download mirrors during bootstrap.
unset UV_PYTHON_INSTALL_MIRROR UV_PYTHON_DOWNLOADS_JSON_URL
"$uv" --no-config python install 3.12.14
python=$("$uv" --no-config python find --managed-python 3.12.14)
"$python" -B "$directory/wsl/setup.py" "$archive" "$expected"
