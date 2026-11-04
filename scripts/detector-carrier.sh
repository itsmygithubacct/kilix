#!/usr/bin/env bash
# Detector-specific use of Content's verified, offline Git transport.

detector_carrier_cleanup() {
  if [ -n "${_detector_carrier_tmp:-}" ]; then
    if [ "${_detector_carrier_cleanup_uncertain:-0}" = 1 ]; then
      printf 'detector source carrier: cleanup uncertain; preserving %s\n' "$_detector_carrier_tmp" >&2
      return
    fi
    rm -rf -- "$_detector_carrier_tmp"
    _detector_carrier_tmp=""
  fi
}

# Carrier helpers can start Git children. Give each invocation an owned
# process group so an interrupted installer cannot remove its pool while a
# child is still using it. Never signal the caller's process group.
detector_carrier_interrupt() {
  trap '' INT TERM
  if [ -n "${_detector_carrier_child:-}" ]; then
    if ! kill -KILL -- "-$_detector_carrier_child" 2>/dev/null; then
      # The child may not have entered its session yet. Preserve the pool
      # because group cleanup cannot be established in this race.
      _detector_carrier_cleanup_uncertain=1
      kill -KILL -- "$_detector_carrier_child" 2>/dev/null || true
    fi
    wait "$_detector_carrier_child" 2>/dev/null || true
    if kill -0 -- "-$_detector_carrier_child" 2>/dev/null; then
      _detector_carrier_cleanup_uncertain=1
    fi
    _detector_carrier_child=""
  fi
  exit "$1"
}

detector_carrier_call() {
  local result
  setsid python3 -I -B "$@" >"$_detector_carrier_tmp/helper-stdout" &
  _detector_carrier_child=$!
  if wait "$_detector_carrier_child"; then result=0; else result=$?; fi
  _detector_carrier_child=""
  return "$result"
}

detector_carrier_prepare() {
  local host="$1" repository="$2" ref="$3" parent="$4" keep="$5"
  local helper="$host/third_party/kilix-content/src/kilix_content/source_carrier.py"
  local config
  [ -f "$helper" ] && [ ! -L "$helper" ] \
    || { printf '%s\n' 'detector source carrier: Content helper is unavailable' >&2; return 1; }
  command -v setsid >/dev/null 2>&1 \
    || { printf '%s\n' 'detector source carrier: setsid is unavailable' >&2; return 1; }
  _detector_carrier_tmp="$(mktemp -d "$parent/.detector-carrier.XXXXXX")" || return 1
  trap detector_carrier_cleanup EXIT
  trap 'detector_carrier_interrupt 130' INT
  trap 'detector_carrier_interrupt 143' TERM
  # Materializing the exact node also rejects an otherwise valid carrier that
  # omits this requested commit. Explicit keep means the user chose not to
  # install that ref, so only the carrier's own complete graph is checked.
  if [ "$keep" != 1 ]; then
    if ! detector_carrier_call "$helper" materialize --repository "$repository" --ref "$ref" \
        --destination "$_detector_carrier_tmp/exact"; then
      printf '%s\n' 'detector source carrier: requested source was refused' >&2
      return 1
    fi
    cat "$_detector_carrier_tmp/helper-stdout" >&2
  fi
  if ! detector_carrier_call "$helper" prepare-git \
      --destination "$_detector_carrier_tmp/pool"; then
    printf '%s\n' 'detector source carrier: offline Git pool was refused' >&2
    return 1
  fi
  config="$(cat "$_detector_carrier_tmp/helper-stdout")"
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
