#!/usr/bin/env bash
# Detector-specific use of Content's verified, offline Git transport.

detector_carrier_cleanup() {
  if [ -n "${_detector_carrier_tmp:-}" ]; then
    rm -rf -- "$_detector_carrier_tmp"
    _detector_carrier_tmp=""
  fi
}

detector_carrier_prepare() {
  local host="$1" repository="$2" ref="$3" parent="$4" keep="$5"
  local helper="$host/third_party/kilix-content/src/kilix_content/source_carrier.py"
  local config
  [ -f "$helper" ] && [ ! -L "$helper" ] \
    || { printf '%s\n' 'detector source carrier: Content helper is unavailable' >&2; return 1; }
  _detector_carrier_tmp="$(mktemp -d "$parent/.detector-carrier.XXXXXX")" || return 1
  trap detector_carrier_cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  # Materializing the exact node also rejects an otherwise valid carrier that
  # omits this requested commit. Explicit keep means the user chose not to
  # install that ref, so only the carrier's own complete graph is checked.
  if [ "$keep" != 1 ]; then
    if ! python3 -I -B "$helper" materialize --repository "$repository" --ref "$ref" \
        --destination "$_detector_carrier_tmp/exact" >&2; then
      printf '%s\n' 'detector source carrier: requested source was refused' >&2
      return 1
    fi
  fi
  if ! config="$(python3 -I -B "$helper" prepare-git \
      --destination "$_detector_carrier_tmp/pool")"; then
    printf '%s\n' 'detector source carrier: offline Git pool was refused' >&2
    return 1
  fi
  case "$config" in
    "$_detector_carrier_tmp"/pool/*) ;;
    *) printf '%s\n' 'detector source carrier: unsafe Git configuration path' >&2; return 1 ;;
  esac
  [ -f "$config" ] && [ ! -L "$config" ] \
    && [ "$(realpath -m -- "$config")" = "$config" ] \
    || { printf '%s\n' 'detector source carrier: invalid Git configuration' >&2; return 1; }
  # The generic helper maps only verified source nodes to file:// repositories.
  # Git children inherit these settings, so an unlisted URL cannot go online.
  export GIT_CONFIG_GLOBAL="$config" GIT_CONFIG_NOSYSTEM=1
  export GIT_ALLOW_PROTOCOL=file GIT_TERMINAL_PROMPT=0
}
