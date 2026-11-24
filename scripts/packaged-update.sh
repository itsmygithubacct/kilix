#!/bin/sh
# Update a Kilix that was installed from Debian packages.
#
# `kilix update` on a checkout pulls from git. A packaged install has no
# checkout; apt owns its files, so the update is an apt upgrade of the Kilix
# packages. This is the only privileged step Kilix takes on such a system and
# it is deliberately rigid:
#   - the two commands and the package list below are fixed; nothing from the
#     command line or the environment is added to them;
#   - apt-get and sudo are called by absolute path;
#   - packages that are not installed are left alone (--only-upgrade);
#   - sudo asks for the password itself; this script never reads one.
# KILIX_PACKAGED_UPDATE_DRY_RUN=1 prints the commands instead of running them.
set -eu

APT=/usr/bin/apt-get
SUDO=/usr/bin/sudo
PACKAGES="kilix kilix-engine kilix-native kilix-multiplexer"

[ $# -eq 0 ] || { echo "usage: packaged-update.sh" >&2; exit 2; }

if [ "$(id -u)" -eq 0 ]; then
  as_root=
else
  as_root=$SUDO
fi

run() {
  if [ "${KILIX_PACKAGED_UPDATE_DRY_RUN:-}" = 1 ]; then
    echo "would run: $*"
    return 0
  fi
  "$@"
}

if [ "${KILIX_PACKAGED_UPDATE_DRY_RUN:-}" != 1 ]; then
  [ -x "$APT" ] || { echo "kilix update: $APT not found" >&2; exit 1; }
  if [ -n "$as_root" ] && [ ! -x "$SUDO" ]; then
    echo "kilix update: $SUDO not found; as root run:" >&2
    echo "  $APT update && $APT install --only-upgrade $PACKAGES" >&2
    exit 1
  fi
fi

echo "kilix update: this Kilix is installed from packages; upgrading with apt"
# shellcheck disable=SC2086  # as_root and PACKAGES are fixed word lists
run $as_root "$APT" update
# shellcheck disable=SC2086
run $as_root "$APT" install --only-upgrade $PACKAGES
echo "kilix update: done; restart Kilix to use the new version"
