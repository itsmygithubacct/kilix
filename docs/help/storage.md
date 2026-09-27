# Kilix 0.2.2 storage and session logs

Kilix-owned writable files live under `~/.local/gpu_terminal/kilix` by default.
Set `KILIX_STORAGE_HOME` to relocate the complete Kilix tree. Tracked defaults
in the source checkout are separate from writable user settings. Run
`kilix status` to see the active writable configuration and engine.

The user tree contains `config` for settings, `state` for persistent state,
`cache` for regenerable data, `session` for sockets and frame files, `data`
for optional downloads, `build` for compiled fork generations and `prebuilt`
for the fallback engine. Shared chrome and game preferences are in the
stack-wide `~/.local/gpu_terminal/settings.conf` instead.

Desktop launchers and icons are another exception: `kilix --install-desktop`
uses standard XDG application paths and records what it wrote in `state`.
`kilix --uninstall-desktop` uses that record to remove its own installed entries.

Session logging is on by default. Run `kilix transcript` to list recorded
pane sessions, or `kilix transcript show SESSION` to print one recording.
Transcripts contain terminal content and may include sensitive user material;
they are not product documentation or an automatic help-training source.
Removing the user tree removes settings and state as well as caches, so do
not use wholesale deletion as a routine troubleshooting step.
