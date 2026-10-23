# Nested Wayland sessions

SDK 1.19 exports `kilix_sdk.wayland.NestedWaylandSession` and
`kilix_sdk.privilege.run_helper`. The Waydroid application requires this SDK
version; importing an older host is rejected before first-use setup.

A nested session starts Weston with its X11 backend inside the caller's
existing X display. Use it as a context manager, call `start_x11(width, height)`,
then `launch_client(name, argv)` and `wait(client)`. `wait` returns a
`SessionExit` with source `client` or `compositor` and the exit status.
`compositor_diagnostic()` returns the last 4 KiB of the compositor log.

The session uses the existing StreamSupervisor and PrivateBus ownership
boundary for both compositor and client. PrivateBus supplies containment;
launched processes retain the caller's D-Bus address. Cleanup closes client
owners before the compositor owner and retains the session and exclusion lock
if ownership cleanup is incomplete. The exception's `resource` permits retry.
The installed, source-matching private-bus systemd supervisor and polkit rule
are required; there is no process-only fallback.

The private Wayland socket lives below `KILIX_SESSION_HOME/stream` (or the
host's default session root). Its encoded pathname must be shorter than 108
bytes. The environment preserves the parent X authority and session bus,
removes inherited `WAYLAND_SOCKET`, and preserves host PulseAudio access when
changing `XDG_RUNTIME_DIR`. The per-UID exclusion lock is independent of the
selected storage root. Lock files are retained to avoid competing lock inodes.
Weston 14's X11 backend is the OS integration target.

`run_helper` accepts only the fixed root-owned Plebian-OS Waydroid setup path,
without caller arguments. It validates the helper and ancestor ownership and
permissions, asks for explicit confirmation, and invokes sudo. Graphical
callers get the same prompt in xterm on their current X display. Refusal,
EOF, and helper failure do not report setup success. No policy grants generic
root commands; OS package/image selection remains in the release-owned helper.

`session_bus_address()` returns an explicit session-bus address unchanged or
resolves the original `$XDG_RUNTIME_DIR/bus` socket before the nested runtime
directory is substituted. An unset or empty address uses this fallback. Socket
path bytes are escaped for D-Bus address syntax. The application doctor uses
the same resolver, so it does not report a different runtime directory or a
regular file as a usable fallback. Resolution is read-only; it does not connect
or autolaunch a daemon. X11 autolaunch configurations must supply an explicit
`DBUS_SESSION_BUS_ADDRESS` for this application.
