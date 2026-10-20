# Kilix 0.2.2 Windows preview 4

In WSL panes, `kilix image FILE` shows images inline and `kilix video FILE`
plays media with the catalog `kilix-mpv` build (`kilix apps install kilix-mpv`).
The companion now carries a Linux `kitten` built from the pinned engine source
and checked by SHA-256 at setup. The player streams silence while paused,
because WSLg's audio server can otherwise hang playback after a pause of five
seconds or more. Video frames are
sized for an assumed 9x18-pixel cell, since WSL panes report no pixel size, so
they fill only part of a large pane.

The WSL companion installs Needle 84ac35d. `kilix needle ask REQUEST` shows how
a request is routed and its plan, and a request without a job verb is routed.

Desktop requests over SSH report a request ID, the host and the time spent in
each phase; the ID is also appended to error messages. The compiled desktop
backend is cached per user, which cut the native part of a status request from
2.65 s to 1.90 s on the test laptop. `kilix windows --host HOST` accepts raw
`--json --request-json -` requests. `diagnostics.ps1 -WslDistribution NAME`
records the active companion generation, its Needle revision and Needle's
workflow readiness.

Earlier previews bound SSH request writes, native process execution and output
completion under one deadline. Native children receive their redirected pipes
without inheriting the caller's SSH handles. Portable installation paths travel
as JSON data, so Unicode paths do not enlarge the remote shell command. A failed
or timed-out action is never automatically retried.

The shared model wizard checks workflow readiness through the selected Needle
application. An unavailable or unready app keeps the page pending; this status
probe never installs a missing app. Completing setup again can retry the saved
choice after the app and its existing licensed assets are ready.

Native Windows x64 Kilix with PowerShell, Command Prompt and optional WSL
profiles. This preview includes the terminal renderer, tabs and splits,
fullscreen, persistent native and WSL sessions, and the pinned public companion
catalog. Windows and Linux use the same Kilix repository and shared SDK.

Desktop automation is available as `kilix desktop` in Windows and WSL, and
`kilix windows --host SSH_ALIAS` on Linux:

- Find windows by application, title and PID; find controls by UI Automation
  name, automation ID and control type.
- Wait for a window or control to appear, become visible/enabled, or close.
- Focus, move, resize, minimize, maximize, restore and close windows; launch
  applications and send keyboard, Unicode text and mouse input.
- Inspect controls, invoke buttons and set editable values.
- Save screenshots on Windows or directly on the calling computer with
  `screenshot --local PATH`. WSL also accepts Linux screenshot paths.

SDK 1.18 exposes these operations through `kilix_sdk.windows`. The archive
includes `examples/notepad.py`, which creates, saves and verifies a new note,
captures its window, then closes it. See `examples/README.txt` for the command.

Download the application ZIP and verify it against `SHA256SUMS`. Extract the
complete archive, then open `Kilix.exe`. `setup.ps1` installs per-user shortcuts
and supports upgrade and rollback. WSL setup is optional. Matching source,
dependency revisions, companion source bundle and toolchain records accompany
the binaries.

Desktop control requires the Windows user to be logged in and unlocked.
Existing SSH authentication and jump-host configuration are used; no network
listener is installed by the desktop commands. UAC secure desktops and elevated
applications remain outside the ordinary interactive user's access. Builds are
unsigned; `signing.json` records their signing status.

The hardware qualification target is Windows 10 x64 build 19045 with PowerShell
5.1, Intel HD Graphics 620 and Ubuntu WSL2 at 150% display scaling. Windows 11
and mixed-DPI monitor layouts require additional qualification. This Windows
preview does not qualify the separate coordinated Plebian-OS release.
