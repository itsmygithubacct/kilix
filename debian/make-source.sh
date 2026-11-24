#!/bin/bash
# make-source.sh OUTDIR : the Debian source tree for this checkout.
#
# Writes OUTDIR/kilix-VERSION/ holding the tracked files of this commit plus
# the submodules named in debian/bundled-submodules, each at the commit this
# checkout pins. Build with `dpkg-buildpackage -b` inside that directory.
set -euo pipefail
umask 022
top=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
out=$(mkdir -p "$1" && cd "$1" && pwd -P)
version=$(dpkg-parsechangelog -l "$top/debian/changelog" -S Version)
tree="$out/kilix-$version"
[ ! -e "$tree" ] || { echo "make-source: $tree already exists" >&2; exit 1; }
[ -z "$(git -C "$top" status --porcelain --untracked-files=no)" ] \
  || { echo "make-source: the checkout has uncommitted changes" >&2; exit 1; }
mkdir "$tree"
git -C "$top" archive --format=tar HEAD | tar -xf - -C "$tree"
while read -r module; do
  [ -n "$module" ] || continue
  pinned=$(git -C "$top" ls-files -s -- "$module" | awk '$1 == "160000" {print $2}')
  actual=$(git -C "$top/$module" rev-parse HEAD 2>/dev/null || true)
  [ -n "$pinned" ] && [ "$actual" = "$pinned" ] \
    || { echo "make-source: $module is not checked out at its pinned commit" >&2; exit 1; }
  [ -z "$(git -C "$top/$module" status --porcelain --untracked-files=all)" ] \
    || { echo "make-source: $module has local changes" >&2; exit 1; }
  mkdir -p "$tree/$module"
  git -C "$top/$module" archive --format=tar "$pinned" | tar -xf - -C "$tree/$module"
  printf '%s %s\n' "$module" "$pinned" >> "$tree/debian/bundled-submodules.pins"
done < "$top/debian/bundled-submodules"
echo "$tree"
