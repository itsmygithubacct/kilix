#!/bin/sh
set -eu

# Rootless, reproducible runtime for Kilix's GPU-backed private Wayland host.
# Debian packages are fetched by exact version and verified before extraction;
# no system package database or /usr path is modified.

TARGET=${KILIX_GPU_HOST_HOME:-${KILIX_STORAGE_HOME:-$HOME/.local/gpu_terminal/kilix}/dependencies/gpu-host}
case $TARGET in
  ''|'/'|"$HOME")
    echo "kilix gpu host: refusing unsafe target: $TARGET" >&2
    exit 1 ;;
esac

packages='weston|14.0.2-1|cb2cddfc082a6f14d15e4682cdbb9778439f9b89123ed41356f108ccb3ebb48e
libweston-14-0|14.0.2-1|882469912622c4ef2b0cf85d9cba56906250a6da7e935580feca868bb7dfe942
pipewire|1.4.2-1|36b7421700912553db2acda062d7a171de097302be28dfc858a82e5f2f77dbda
pipewire-bin|1.4.2-1|ac30a24f7efc42afff5269b02c5a65068eb7ebba65f1d7749bd7737986de0d17
libpipewire-0.3-modules|1.4.2-1|ee9a07d97d80369a7377b83bdfbc5178d80dd76a49b3df5bff294b878b9afa53
xwayland|2:24.1.6-1|7e51a144e858d5be7e91d6644c2758a618e425c7222234d2e8f059e5aba6c1f5'

verify() {
  root=$1
  test -x "$root/usr/bin/weston" \
    && test -x "$root/usr/bin/pipewire" \
    && test -x "$root/usr/bin/pw-link" \
    && test -x "$root/usr/bin/Xwayland" \
    && test -f "$root/usr/lib/x86_64-linux-gnu/libweston-14/gl-renderer.so" \
    && test -f "$root/usr/lib/x86_64-linux-gnu/libweston-14/pipewire-backend.so" \
    && test -f "$root/usr/lib/x86_64-linux-gnu/pipewire-0.3/libpipewire-module-protocol-native.so"
}

case ${1:-} in
  --print-path)
    verify "$TARGET/root" || exit 1
    printf '%s\n' "$TARGET/root"
    exit 0 ;;
  --verify)
    verify "$TARGET/root"
    printf 'kilix gpu host: verified %s\n' "$TARGET/root"
    exit 0 ;;
  ''|--install) ;;
  *) echo "usage: $0 [--install|--verify|--print-path]" >&2; exit 2 ;;
esac

command -v apt-get >/dev/null 2>&1 || {
  echo 'kilix gpu host: apt-get is required for rootless package acquisition' >&2
  exit 1
}
command -v dpkg-deb >/dev/null 2>&1 || {
  echo 'kilix gpu host: dpkg-deb is required for rootless package extraction' >&2
  exit 1
}
command -v sha256sum >/dev/null 2>&1 || {
  echo 'kilix gpu host: sha256sum is required for package verification' >&2
  exit 1
}

mkdir -p "$TARGET"
work=$(mktemp -d "$TARGET/.install.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM
mkdir -p "$work/packages" "$work/root"

old_ifs=$IFS
printf '%s\n' "$packages" | while IFS='|' read -r package version digest; do
  (cd "$work/packages" && apt-get download "$package=$version")
  archive=$(find "$work/packages" -maxdepth 1 -type f -name "${package}_*.deb" -print)
  if test -z "$archive" || test "$(printf '%s\n' "$archive" | wc -l)" -ne 1; then
    echo "kilix gpu host: expected one archive for $package" >&2
    exit 1
  fi
  printf '%s  %s\n' "$digest" "$archive" | sha256sum -c --status || {
    echo "kilix gpu host: checksum mismatch for $package=$version" >&2
    exit 1
  }
  dpkg-deb -x "$archive" "$work/root"
done
IFS=$old_ifs

verify "$work/root" || {
  echo 'kilix gpu host: extracted runtime is incomplete' >&2
  exit 1
}
rm -rf "$TARGET/root.new"
mv "$work/root" "$TARGET/root.new"
if test -e "$TARGET/root"; then
  mv "$TARGET/root" "$work/root.old"
fi
mv "$TARGET/root.new" "$TARGET/root"
printf 'kilix gpu host: installed %s\n' "$TARGET/root"
