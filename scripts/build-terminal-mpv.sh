#!/usr/bin/env bash
# Build the pinned terminal player with the Kitty resize-buffer lifetime fix.
set -euo pipefail
if [[ ${1:-} == --help ]]; then
  echo 'usage: kilix video --install-player'
  echo 'Builds mpv 0.40.0 with the terminal resize fix into private Kilix storage.'
  echo 'Requires meson, ninja, cc, pkg-config, curl, patch and mpv development dependencies.'
  exit 0
fi
[[ $# == 0 ]] || { echo 'unexpected installer argument' >&2; exit 2; }
for tool in meson ninja cc pkg-config curl patch tar sha256sum flock; do
  command -v "$tool" >/dev/null || { echo "kilix video: build prerequisite missing: $tool" >&2; exit 1; }
done
pkg-config --exists libavcodec libavfilter libavformat libavutil libswresample libswscale libplacebo libass luajit libpulse || {
  echo 'kilix video: install mpv development packages listed in docs/terminal-media.md first.' >&2
  exit 1
}
root="${KILIX_STORAGE_HOME:-${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}/kilix}/media/mpv-0.40.0-kilix1"
mkdir -p "$root"
exec 9>"$root/build.lock"
flock 9
work="$(mktemp -d "$root/build.XXXXXXXX")"
trap 'rm -rf -- "$work"' EXIT
curl --fail --location --retry 2 --output "$work/source.tar.gz" \
  https://codeload.github.com/mpv-player/mpv/tar.gz/refs/tags/v0.40.0
(cd "$work" && echo '10a0f4654f62140a6dd4d380dcf0bbdbdcf6e697556863dc499c296182f081a3  source.tar.gz' | sha256sum -c -)
tar -xzf "$work/source.tar.gz" -C "$work"
source_dir="$work/mpv-0.40.0"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
patch --batch --forward -d "$source_dir" -p1 < "$script_dir/mpv-kitty-resize.patch"
meson setup "$work/build" "$source_dir" --prefix=/usr/local --buildtype=release \
  -Dauto_features=disabled -Dgl=disabled -Dlibmpv=false -Dlua=luajit -Dpulse=enabled
ninja -C "$work/build" -j "${KILIX_MEDIA_BUILD_JOBS:-2}"
meson install -C "$work/build" --no-rebuild --destdir "$work/stage"
"$work/stage/usr/local/bin/mpv" --version
mkdir -p "$root/bin" "$root/licenses"
install -m 755 "$work/stage/usr/local/bin/mpv" "$root/bin/mpv.new"
mv -f "$root/bin/mpv.new" "$root/bin/mpv"
cp "$source_dir/Copyright" "$source_dir/LICENSE.GPL" "$source_dir/LICENSE.LGPL" "$root/licenses/"
cp "$script_dir/mpv-kitty-resize.patch" "$root/"
# Retain corresponding source for the private build, including the applied patch.
cp "$work/source.tar.gz" "$root/source.tar.gz"
echo "Terminal player installed: $root/bin/mpv"
