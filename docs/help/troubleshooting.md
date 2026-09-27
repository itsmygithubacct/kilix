# Kilix 0.2.2 troubleshooting

If clickable pane buttons are missing, run `kilix --which` to identify the
engine. A path ending in `kitty.app/bin/kitty` indicates the prebuilt fallback.
Install the documented build dependencies, run `kilix --build` and relaunch
to use the fork with clickable chrome. A verified prebuilt can start a terminal
without providing the fork's buttons.

If the taskbar shows the wrong icon, run `kilix --install-desktop` and restart
the panel or log out and back in. On Wayland the installed desktop entry is
needed for the application icon.

Kilix is a graphical terminal. Launching it without a local graphical session
and a usable DISPLAY or WAYLAND_DISPLAY does not provide a headless terminal.
Use `kilix status` to inspect the selected engine and writable configuration.

If a first-run fork build fails because dependencies are absent, Kilix attempts
the prebuilt fallback. A prebuilt download requires a pinned version and
SHA-256 unless the user explicitly chooses unverified acquisition. Do not
invent a checksum or assume a newer engine is the coordinated wrapper release.

For silent speech or missing dictation, run `kilix voice doctor` first.
Check output and input separately, then inspect model installation and runtime
support using `kilix stt --models`. A successful command exit or downloaded
model alone does not establish audible output or a working recognizer.
