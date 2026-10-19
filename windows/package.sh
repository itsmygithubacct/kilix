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
PYVER=${PYVER//$'\r'/}
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
cp "$windows_source/RELEASE-NOTES.md" "$destination/"
cp "$windows_source/desktop-targeting.md" "$destination/"
cp "$windows_source/"{setup,setup-common,install,diagnostics,update,update-common,update-dispatch}.ps1 "$destination/"
cp "$windows_source/wsl-bootstrap.sh" "$destination/"
cp "$windows_source/"test-*.ps1 "$destination/"
mkdir -p "$destination/tests"
cp "$windows_source/tests/"*.py "$windows_source/tests/"*.ps1 "$destination/tests/"
mkdir -p "$destination/examples"
cp "$windows_source/examples/"*.py "$windows_source/examples/"*.txt "$destination/examples/"
cp "$windows_source/licenses/"* "$destination/licenses/"
cp LICENSE WINDOWS-PORT-NOTICE "$destination/licenses/"
if [[ -f WINDOWS-PTY-BROKER-NOTICE ]]; then cp WINDOWS-PTY-BROKER-NOTICE "$destination/licenses/"; fi
mkdir -p "$destination/licenses/mingw"
cp -r /mingw64/share/licenses/. "$destination/licenses/mingw/"
mkdir -p "$destination/fontconfig"
cp -r /mingw64/etc/fonts/. "$destination/fontconfig/"
cp __main__.py "$destination/"
cp kitty/child.py "$destination/kitty/"
cp kitty/shell_integration.py "$destination/kitty/"
cp kitty/cli.py "$destination/kitty/"
cp kitty/main.py "$destination/kitty/"
cp kitty/remote_control.py "$destination/kitty/"
# Refresh recovery code when reusing a distribution built before these modules.
for module in windows_pty_broker windows_pty_ipc windows_pty_protocol; do
    if [[ -f kitty/$module.py ]]; then cp "kitty/$module.py" "$destination/kitty/"; fi
done
cp kitty/fast_data_types.so kitty/fast_data_types.pyd kitty/glfw-win32.so kitty/glfw-win32.pyd "$destination/kitty/"
cp kitty/launcher/conpty.dll kitty/launcher/OpenConsole.exe "$destination/kitty/launcher/"
# The engine's minimal distribution drops tkinter. Restore the relocatable GUI
# runtime for the catalog chooser, including _tkinter on --stage-existing builds.
cp -r "/mingw64/lib/python$PYVER/tkinter" "$destination/pylib/lib/python$PYVER/"
tk_module=$(python -c 'import _tkinter; print(_tkinter.__file__)')
tk_module=$(cygpath -u "${tk_module//$'\r'/}")
mkdir -p "$destination/pylib/lib/python$PYVER/lib-dynload"
cp "$tk_module" "$destination/pylib/lib/python$PYVER/lib-dynload/"
for toolkit in tcl tk; do
    version=$(python -c "import _tkinter; print(_tkinter.${toolkit^^}_VERSION)")
    version=${version//$'\r'/}
    # Tcl ships read-only catalog files. Make our previous staging copy writable
    # so --stage-existing can refresh it on Windows as well as a fresh build.
    if [[ -d "$destination/pylib/lib/$toolkit$version" ]]; then
        chmod -R u+w "$destination/pylib/lib/$toolkit$version"
    fi
    cp -r "/mingw64/lib/$toolkit$version" "$destination/pylib/lib/"
done
if [[ -d /mingw64/lib/tcl8 ]]; then
    if [[ -d "$destination/pylib/lib/tcl8" ]]; then chmod -R u+w "$destination/pylib/lib/tcl8"; fi
    cp -r /mingw64/lib/tcl8 "$destination/pylib/lib/"
fi
python -c 'import _tkinter, json, pathlib, sys; pathlib.Path(sys.argv[1], "tk-runtime.json").write_text(json.dumps({"tcl": "pylib/lib/tcl" + _tkinter.TCL_VERSION, "tk": "pylib/lib/tk" + _tkinter.TK_VERSION}) + "\n", encoding="utf-8")' "$destination"
declare -A tk_seen
tk_queue=("$tk_module")
for ((i=0; i<${#tk_queue[@]}; i++)); do
    while IFS= read -r dll; do
        key=${dll,,}
        [[ -n ${tk_seen[$key]:-} ]] && continue
        tk_seen[$key]=1
        if [[ -f /mingw64/bin/$dll ]]; then
            cp "/mingw64/bin/$dll" "$destination/kitty/launcher/"
            tk_queue+=("/mingw64/bin/$dll")
        fi
    done < <(objdump -p "${tk_queue[$i]}" | awk '/DLL Name/{print $3}')
done
gcc -std=c11 -Wall -Wextra -Werror -municode -mwindows -static -Os \
    "$windows_source/src/launcher.c" -lshell32 -o "$destination/Kilix.exe"
gcc -std=c11 -Wall -Wextra -Werror -municode -DKILIX_CONSOLE -static -Os \
    "$windows_source/src/launcher.c" -lshell32 -o "$destination/Kilix.com"
cp "$destination/Kilix.com" "$destination/kilix-cli.exe"
mkdir -p "$destination/wsl-bin"
cp "$windows_source/kilix-wsl.sh" "$destination/wsl-bin/kilix"
chmod +x "$destination/wsl-bin/kilix"
mkdir -p "$destination/wsl" "$destination/wsl-bin/config"
cp "$windows_source/wsl/"*.py "$windows_source/wsl/setup.sh" "$destination/wsl/"
cp "$windows_source/wsl/README.txt" "$windows_source/wsl/"*.json "$destination/wsl/"
cp "$windows_source/wsl/kitten.sh" "$destination/wsl-bin/kitten"
chmod +x "$destination/wsl-bin/kitten"
cp -r "$windows_source/../config/kilix_sdk" "$destination/wsl-bin/config/"
cp "$windows_source/../config/remote.py" "$destination/wsl-bin/config/"
cp "$windows_source/wsl/games.py" "$destination/wsl-bin/config/"
# The command adapter has its own pinned, licensed source closure.
if [[ -f "$windows_source/stage-commands.py" ]]; then
    python "$windows_source/stage-commands.py" "$destination"
fi
# Qualification commands can create bytecode when reusing a staging tree.
# Ship sources and rebuild caches at runtime, without stale code or build paths.
find "$destination" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$destination" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
printf '0.2.2-windows-dev\n' > "$destination/VERSION"
# package.ps1 creates the final manifest after optional source provisioning/signing.
echo "Portable application: $destination/Kilix.exe"
