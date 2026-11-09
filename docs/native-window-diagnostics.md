# Native-window routing diagnostics

Pleb sessions record applications that appear as host X11 windows outside
Kilix tabs/panes in `~/.local/gpu_terminal/pleb/state/native-windows.jsonl`
(or `PLEB_STATE_HOME`). This runs independently of clickable-chrome settings.
Each observed window open, remap, and focus transition records its X window ID,
WM class/command, local executable, argument vector, working directory, and up
to eight available parent processes. XRes identifies local clients even when
they omit `_NET_WM_PID`. Dialogs and skip-taskbar windows are included; Kilix's
own windows, desktop/dock infrastructure, and nested in-pane displays are not.

These records help identify applications and launch paths needing routing
fixes. Process arguments are observed state, not recovered shell syntax:
launchers that already exited, remote clients, and requests sent to an existing
process through D-Bus may not expose the original invocation. Missing process
details are marked unavailable. No environment variables, terminal output, or
window titles are captured. Arguments and paths can contain private data; logs
are local, mode 0600, and rotate at 10 MiB with four backups (about 50 MiB total).
Do not publish raw logs without reviewing them.

The system Python and `python3-xlib` run `scripts/native-window-log.py`.
One watcher per display/state directory is enforced with a lock. It exits when
the session parent or X server exits. Existing windows are inventoried at
startup; very short-lived windows that vanish before inspection may lack
metadata. Inspect records with `tail -f` or a JSON-lines reader.
