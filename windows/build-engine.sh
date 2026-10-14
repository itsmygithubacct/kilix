#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
engine_source=${1:?Pass the Windows engine source directory in MSYS path form}
cd -- "$engine_source"
export PATH="/mingw64/bin:$PATH"
export PYTHONUTF8=1
export GOMAXPROCS=2
python -c 'import sys; assert sys.platform == "win32", sys.platform; print(sys.version); print(sys.executable)'
gcc --version | head -n 1
go version
python setup.py build --portable
cp -f kitty/fast_data_types.so kitty/fast_data_types.pyd
cp -f kitty/glfw-win32.so kitty/glfw-win32.pyd
./kitty/launcher/kitty-console.exe --version
