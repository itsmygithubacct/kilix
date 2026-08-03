# kilix shell rc — loaded via `bash --rcfile` when kilix starts a shell (see
# the `-o shell=` in ../kilix). This is how kilix carries prompt/shell tweaks
# WITHOUT putting them in ~/.bashrc, where they would also leak into regular
# shells and tmux. Order: your normal bashrc, then kitty's shell integration
# (kept working because a custom rcfile disables kitty's auto-injection), then
# the kilix-only prompt.

# 1. your normal interactive shell setup
[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"

# 2. kitty shell integration — cwd reporting (so new tabs/splits inherit the
#    current directory) and prompt marks. Manual, since a custom --rcfile turns
#    off kitty's automatic injection. kitty sets KITTY_INSTALLATION_DIR.
if [ -n "${KITTY_INSTALLATION_DIR:-}" ] && \
   [ -r "$KITTY_INSTALLATION_DIR/shell-integration/bash/kitty.bash" ]; then
    export KITTY_SHELL_INTEGRATION="${KITTY_SHELL_INTEGRATION:-enabled}"
    . "$KITTY_INSTALLATION_DIR/shell-integration/bash/kitty.bash"
fi

# 3. kilix-only prompt. Drop your prompt customisation in
#    ~/.local/gpu_terminal/sources/kilix/config/prompt.bash (kilix-local, not committed) to have it apply
#    to kilix sessions only. If that file is absent, kilix falls back to a
#    synth-shell prompt when you have one installed.
if [[ $- == *i* ]]; then
    _kilix_source_root="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
    if [ -f "${KILIX_HOME:-$_kilix_source_root/kilix}/config/prompt.bash" ]; then
        . "${KILIX_HOME:-$_kilix_source_root/kilix}/config/prompt.bash"
    elif [ -f "$HOME/.config/synth-shell/synth-shell-prompt.sh" ]; then
        . "$HOME/.config/synth-shell/synth-shell-prompt.sh"
    fi
fi

# 4. Pleb sessions (Plebian-OS): the whole desktop is this Kilix. A native X11
#    window escapes its page/pane controls even when the session's fallback
#    Openbox can raise it. Alias the
#    GUI apps to `kilix run <app>`, which gives each one a private X server
#    streamed into a tab. Detection: the XDG session markers exported by
#    pleb-session. Force on/off with KILIX_RUN_ALIASES=1/0; add or remove apps
#    with KILIX_RUN_ALIAS_APPS="foo bar" and
#    KILIX_RUN_ALIAS_EXCLUDE_APPS="baz". Only real PATH commands are wrapped
#    (an alias or function you defined in ~/.bashrc wins), and rcfiles are read
#    by interactive shells only — scripts exec'ing binaries are unaffected.
case "${KILIX_RUN_ALIASES:-}" in
    1|yes|true|on)  _kilix_run_aliases=1 ;;
    0|no|false|off) _kilix_run_aliases=0 ;;
    *) if [ "${XDG_SESSION_DESKTOP:-}" = pleb ] || [ "${XDG_CURRENT_DESKTOP:-}" = Pleb ]; then
           _kilix_run_aliases=1
       else
           _kilix_run_aliases=0
       fi ;;
esac
if [ "$_kilix_run_aliases" = 1 ]; then
    _kilix_source_root="${GPU_TERMINAL_SOURCE_HOME:-$HOME/.local/gpu_terminal/sources}"
    _kilix_bin="$(type -P kilix 2>/dev/null || true)"
    [ -n "$_kilix_bin" ] \
        || _kilix_bin="${KILIX_HOME:-$_kilix_source_root/kilix}/kilix"
    # Start with widely used Debian GUI command names so manually installed
    # binaries without a .desktop file are covered. Then add every visible,
    # non-terminal application in the installed XDG desktop catalogue. The
    # helper deliberately rejects interpreters, generic dispatchers, hidden
    # services and desktop infrastructure so `python3`, `sh`, `openbox`, etc.
    # remain ordinary terminal commands.
    _kilix_apps=(
        abiword atril audacious blender brave-browser chromium chromium-browser
        code codium discord dolphin editres eog epiphany evince feh firefox
        firefox-esr galculator geany gedit gimp gnome-calculator gnome-terminal
        google-chrome google-chrome-stable gnumeric inkscape kate konsole krita
        libreoffice mousepad mpv nautilus obconf okular opera pcmanfm qpdfview
        rhythmbox signal-desktop slack smplayer soffice spotify steam
        sublime_text system-config-printer telegram-desktop thunar thunderbird
        uxterm vivaldi-stable vlc xev xfd xfontsel xmessage xpdf xterm zenity
        zoom
    )
    _kilix_gui_catalog="${BASH_SOURCE[0]%/*}/gui_apps.py"
    _kilix_python="$(type -P python3 2>/dev/null || true)"
    if [ -n "$_kilix_python" ] && [ -r "$_kilix_gui_catalog" ]; then
        while IFS= read -r _kilix_app; do
            [ -n "$_kilix_app" ] && _kilix_apps+=("$_kilix_app")
        done < <("$_kilix_python" "$_kilix_gui_catalog" 2>/dev/null)
    fi
    read -r -a _kilix_extra_apps <<< "${KILIX_RUN_ALIAS_APPS:-}"
    _kilix_apps+=("${_kilix_extra_apps[@]}")
    read -r -a _kilix_excluded_apps <<< "${KILIX_RUN_ALIAS_EXCLUDE_APPS:-}"
    declare -A _kilix_seen_apps=()
    for _kilix_app in "${_kilix_apps[@]}"; do
        [ -n "$_kilix_app" ] || continue
        [ -z "${_kilix_seen_apps[$_kilix_app]+x}" ] || continue
        _kilix_seen_apps[$_kilix_app]=1
        _kilix_excluded=0
        for _kilix_excluded_app in "${_kilix_excluded_apps[@]}"; do
            if [ "$_kilix_app" = "$_kilix_excluded_app" ]; then
                _kilix_excluded=1
                break
            fi
        done
        [ "$_kilix_excluded" = 0 ] || continue
        case "$_kilix_app" in kilix|kitty|kitten) continue ;; esac
        if [ "$(type -t "$_kilix_app" 2>/dev/null)" = file ]; then
            # shellcheck disable=SC2139  # expand $_kilix_bin now, by design
            alias "$_kilix_app"="$(printf '%q run %q' "$_kilix_bin" "$_kilix_app")"
        fi
    done
    unset _kilix_app _kilix_apps _kilix_bin _kilix_excluded \
        _kilix_excluded_app _kilix_excluded_apps _kilix_extra_apps \
        _kilix_gui_catalog _kilix_python _kilix_seen_apps
fi
unset _kilix_run_aliases

# 5. Streamed session (`kilix serve`, KILIX_STREAM=1): inline images must use
#    direct transmission or they won't survive a remote/tmux attach. Force icat
#    to stream + unicode-placeholders so images render on every attached device.
#    (No effect on a normal local kilix shell, where KILIX_STREAM is unset.)
if [ "${KILIX_STREAM:-}" = 1 ]; then
    icat() { command kitten icat --transfer-mode=stream --unicode-placeholder "$@"; }
    kitten() {
        if [ "${1:-}" = icat ]; then
            shift
            command kitten icat --transfer-mode=stream --unicode-placeholder "$@"
        else
            command kitten "$@"
        fi
    }
fi
