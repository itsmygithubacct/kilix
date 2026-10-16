#!/bin/sh
# Run inside the intended WSL distribution as the intended Linux user.
set -eu
directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
archive=${1:?Pass the companion source bundle}
expected=${2:?Pass its SHA256}
uv=${3:?Pass the Linux uv executable}
actual=$(sha256sum "$archive")
test "${actual%% *}" = "$expected" || { echo 'Companion bundle hash mismatch' >&2; exit 1; }
export UV_PYTHON_INSTALL_DIR="$HOME/.local/share/kilix-wsl/python"
"$uv" python install 3.12.14
python=$("$uv" python find 3.12.14)
exec "$python" "$directory/setup.py" "$archive" "$expected"
