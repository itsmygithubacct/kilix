# shellcheck shell=bash

# Real-browser dispatch for `kilix open-url` and its `kilix browse`
# compatibility alias.  Keep this policy deliberately small and explicit:
# callers must not fall through to the in-pane browser while one of these
# supported desktop browsers is installed.
#
# Below the desktop browsers sits Chawan, and below that the in-pane Chrome
# renderer.  Chawan is only consulted when it is ALREADY built: open-url is a
# programmatic entry point, so it must never stall behind a first-run compile.
# `kilix chawan` is the command that installs it.

_kilix_find_real_browser() {
  local candidate resolved
  for candidate in google-chrome chromium-browser firefox-esr; do
    resolved="$(command -v "$candidate" 2>/dev/null)" || continue
    if [ -n "$resolved" ] && [ -x "$resolved" ]; then
      printf '%s\n' "$resolved"
      return 0
    fi
  done
  return 1
}

_kilix_find_installed_chawan() {
  local installer="${KILIX_HOME:-}/scripts/install-kilix-chawan.sh" resolved
  [ -n "${KILIX_HOME:-}" ] || return 1
  [ -f "$installer" ] && [ ! -L "$installer" ] && [ -x "$installer" ] || return 1
  resolved="$("$installer" --print-installed 2>/dev/null)" || return 1
  [ -n "$resolved" ] && [ -x "$resolved" ] || return 1
  printf '%s\n' "$resolved"
}

_kilix_exec_chawan() {
  local browser="$1" argument
  local -a arguments=()
  shift

  # Chawan has no separate private mode — it is a fresh process with no
  # persistent profile — and --no-cursor belongs to the terminal renderer.
  for argument in "$@"; do
    case "$argument" in
      --incognito|--no-cursor) ;;
      *) arguments+=("$argument") ;;
    esac
  done

  CHA_DIR="${KILIX_CHAWAN_CONFIG_DIR:-${KILIX_CONFIG_HOME:-$HOME/.config/kilix}/chawan}"
  if [ ! -e "$CHA_DIR/config.toml" ] \
       && [ -f "${KILIX_HOME:-}/config/chawan/config.toml" ]; then
    mkdir -p "$CHA_DIR" 2>/dev/null \
      && cp -- "$KILIX_HOME/config/chawan/config.toml" "$CHA_DIR/config.toml" \
         2>/dev/null || :
  fi
  export CHA_DIR
  exec "$browser" "${arguments[@]}"
}

_kilix_exec_real_browser() {
  local browser="$1" private=0 argument
  local -a arguments=()
  shift

  # `--no-cursor` belongs only to the terminal renderer.  Preserve the public
  # `--incognito` spelling and translate it for Firefox ESR.
  for argument in "$@"; do
    case "$argument" in
      --incognito) private=1 ;;
      --no-cursor) ;;
      *) arguments+=("$argument") ;;
    esac
  done

  case "${browser##*/}" in
    firefox-esr)
      if [ "$private" = 1 ]; then
        arguments=(--private-window "${arguments[@]}")
      fi ;;
    *)
      if [ "$private" = 1 ]; then
        arguments=(--incognito "${arguments[@]}")
      fi ;;
  esac

  exec "$browser" "${arguments[@]}"
}
