#!/bin/sh
# Install a verified companion source bundle in the current WSL user's home.
set -eu
archive=${1:?Pass the companion source bundle}
expected=${2:?Pass its SHA256}
uv=${3:?Pass the Linux uv executable}
actual=$(sha256sum "$archive")
test "${actual%% *}" = "$expected" || { echo 'Companion bundle hash mismatch' >&2; exit 1; }
root="$HOME/.local/share/kilix-wsl"
generation="$root/sources/$(date -u +%Y%m%d%H%M%S)"
mkdir -p "$generation" "$root/bin"
tar -xzf "$archive" -C "$generation"
export UV_PYTHON_INSTALL_DIR="$root/python"
"$uv" python install 3.12.14
python=$("$uv" python find 3.12.14)
ln -sfn "$python" "$root/bin/python3"
ln -sfn "$generation/needle" "$root/needle"
mkdir -p "$generation/content"
ln -sfn "$generation/content" "$root/content"
# Preserve the owner's existing acceptance records; never manufacture one.
receipts="$HOME/.local/gpu_terminal/license-receipts"
mkdir -p "$receipts"
chmod 700 "$receipts"
for source in "$generation"/receipts/*.json; do
    test -f "$source" || continue
    target="$receipts/$(basename "$source")"
    if test -e "$target"; then cmp "$source" "$target"; else install -m 600 "$source" "$target"; fi
done
make -C "$generation/games/kilix-pong" -j2
make -C "$generation/games/kilix-pong" install PREFIX="$root"
"$python" "$root/needle/needle_cli.py" --help >/dev/null
echo 'WSL companion Python, Needle sources, and Pong installed. Needle verifies model/license readiness when run.'
