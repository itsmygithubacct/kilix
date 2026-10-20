#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Build the static Linux kitten that the WSL companion uses for kilix image.
# ENGINE_SOURCE is the pinned engine at ENGINE_REVISION; it is built in place,
# only to generate kitten's Go sources. The engine's own extension is unused.
# OUTPUT receives kitten, its LICENSE and kitten.json (revision, toolchain, sha256).
set -eu
engine_source=${1:?Pass the engine source directory}
revision=${2:?Pass the engine revision}
output=${3:?Pass the output directory}
case $revision in
    *[!0-9a-f]*|'') echo 'build-kitten: the engine revision must be a full commit' >&2; exit 2 ;;
esac
test ${#revision} -eq 40 || { echo 'build-kitten: the engine revision must be a full commit' >&2; exit 2; }
mkdir -p -- "$output"
output=$(CDPATH= cd -- "$output" && pwd)
cd -- "$engine_source"
# kitten.json records this revision, so the checkout must be exactly it.
test "$(git rev-parse HEAD)" = "$revision" || { echo "build-kitten: $engine_source is not at $revision" >&2; exit 1; }
test -z "$(git status --porcelain)" || { echo "build-kitten: $engine_source has uncommitted or untracked files" >&2; exit 1; }
# go.mod names the exact toolchain; Go fetches and verifies it if it is not local.
toolchain=$(sed -n 's/^toolchain \(go[0-9][0-9.]*\)$/\1/p' go.mod)
test -n "$toolchain" || { echo 'build-kitten: go.mod names no toolchain' >&2; exit 1; }
export GOTOOLCHAIN="$toolchain" GOFLAGS=-buildvcs=false
# The build copies this font into the engine's runtime tree; kitten never reads it.
# Remove the empty stand-in afterwards so a real engine build cannot ship it.
font=fonts/SymbolsNerdFontMono-Regular.ttf placeholder=
if ! test -e "$font"; then
    if test -d fonts; then placeholder=file; else placeholder=directory; fi
    mkdir -p fonts && : > "$font"
fi
trap 'test -z "$placeholder" || { rm -f -- "$font"; test "$placeholder" = file || rmdir -- fonts; }' EXIT
# Only kitten's generated Go sources are used; the C extension is discarded.
python3 setup.py build --skip-building-kitten --ignore-compiler-warnings
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOAMD64=v1 go build -trimpath -ldflags \
    "-X github.com/kovidgoyal/kitty.VCSRevision=$revision -X github.com/kovidgoyal/kitty.IsStandaloneBuild=true -s -w" \
    -o "$output/kitten" ./tools/cmd
"$output/kitten" --version
cp LICENSE "$output/LICENSE"
digest=$(sha256sum "$output/kitten")
printf '{"engine": "%s", "toolchain": "%s", "target": "linux/amd64", "sha256": "%s"}\n' \
    "$revision" "$toolchain" "${digest%% *}" > "$output/kitten.json"
cat "$output/kitten.json"
