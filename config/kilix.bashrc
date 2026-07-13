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
#    ~/kilix/config/prompt.bash (kilix-local, not committed) to have it apply
#    to kilix sessions only. If that file is absent, kilix falls back to a
#    synth-shell prompt when you have one installed.
if [[ $- == *i* ]]; then
    if [ -f "${KILIX_HOME:-$HOME/kilix}/config/prompt.bash" ]; then
        . "${KILIX_HOME:-$HOME/kilix}/config/prompt.bash"
    elif [ -f "$HOME/.config/synth-shell/synth-shell-prompt.sh" ]; then
        . "$HOME/.config/synth-shell/synth-shell-prompt.sh"
    fi
fi

# 4. Pleb sessions (Plebian-OS): the whole desktop is this kilix — there is no
#    window manager, so a GUI app launched from a prompt would open an
#    unmanaged X11 window the session cannot manage or show properly. Alias the
#    common GUI apps to `kilix run <app>`, which gives each one a private X
#    server streamed into a tab. Detection: the XDG session markers exported by
#    pleb-session. Force on/off with KILIX_RUN_ALIASES=1/0; add apps with
#    KILIX_RUN_ALIAS_APPS="foo bar". Only real PATH commands are wrapped (an
#    alias or function you defined in ~/.bashrc wins), and rcfiles are read by
#    interactive shells only — scripts exec'ing these binaries are unaffected.
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
    _kilix_bin="$(command -v kilix)" || _kilix_bin="${KILIX_HOME:-$HOME/kilix}/kilix"
    # The default list is every GUI program a stock Plebian-OS install ships
    # (see plebian-os provision/install-deps.sh: the browsers, xterm, zenity,
    # and the x11-utils viewers), plus common alternate names and gimp.
    for _kilix_app in chromium chromium-browser firefox firefox-esr gimp \
                      xterm uxterm zenity xmessage xev xfontsel xfd \
                      editres viewres \
                      ${KILIX_RUN_ALIAS_APPS:-}; do
        if [ "$(type -t "$_kilix_app" 2>/dev/null)" = file ]; then
            # shellcheck disable=SC2139  # expand $_kilix_bin now, by design
            alias "$_kilix_app"="$(printf '%q run %q' "$_kilix_bin" "$_kilix_app")"
        fi
    done
    unset _kilix_app _kilix_bin
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
