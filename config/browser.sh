# shellcheck shell=bash

# Real-browser dispatch for `kilix open-url` and its `kilix browse`
# compatibility alias.  Keep this policy deliberately small and explicit:
# callers must not fall through to the in-pane browser while one of these
# supported desktop browsers is installed.

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
