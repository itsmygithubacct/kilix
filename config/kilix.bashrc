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

# 4. Streamed session (`kilix serve`, KILIX_STREAM=1): inline images must use
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
