#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
windows_source=$(cd -- "$(dirname -- "$0")" && pwd)
engine_source=${1:?Pass the engine source directory}
cd -- "$engine_source"
export PATH="/mingw64/bin:$PATH" PYTHONUTF8=1 GOMAXPROCS=2
export MINGW_ROOT=/mingw64
export PYVER
PYVER=$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
destination="$PWD/dist/kilix"
if [[ ${2:-} != --stage-existing ]]; then
    bash packaging/windows/make-dist.sh
    if [[ -e "$destination" ]]; then
        previous="$destination.previous.$(date +%Y%m%d%H%M%S)"
        mv -- "$destination" "$previous"
    fi
    # Windows scanners can briefly keep a just-executed image open.
    for attempt in 1 2 3 4 5; do
        if mv dist/kitty "$destination"; then break; fi
        if [[ $attempt == 5 ]]; then exit 1; fi
        sleep 1
    done
fi
test -f "$destination/kitty/launcher/kitty.exe"
mkdir -p "$destination/config" "$destination/licenses"
cp "$windows_source/config/kilix.conf" "$destination/config/"
cp "$windows_source/baseline.json" "$destination/"
cp "$windows_source/README.txt" "$destination/"
cp "$windows_source/licenses/"* "$destination/licenses/"
cp LICENSE WINDOWS-PORT-NOTICE "$destination/licenses/"
mkdir -p "$destination/licenses/mingw"
cp -r /mingw64/share/licenses/. "$destination/licenses/mingw/"
mkdir -p "$destination/fontconfig"
cp -r /mingw64/etc/fonts/. "$destination/fontconfig/"
cp __main__.py "$destination/"
cp kitty/child.py "$destination/kitty/"
cp kitty/fast_data_types.so kitty/fast_data_types.pyd kitty/glfw-win32.so kitty/glfw-win32.pyd "$destination/kitty/"
cp kitty/launcher/conpty.dll kitty/launcher/OpenConsole.exe "$destination/kitty/launcher/"
gcc -std=c11 -Wall -Wextra -Werror -municode -mwindows -static -Os \
    "$windows_source/src/launcher.c" -lshell32 -o "$destination/Kilix.exe"
printf '0.2.2-windows-dev\n' > "$destination/VERSION"
python "$windows_source/package-manifest.py" "$destination"
echo "Portable application: $destination/Kilix.exe"
