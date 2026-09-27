# Kilix 0.2.2 explicit model setup

Run `kilix models list` to inspect the pinned Content model catalog.
Run `kilix models show whisper-tiny-ggml` to inspect one model's exact
version, source, notices, disk allowances and actual installer root. These
inspection commands do not initialize settings, install directories or receipts.

Run `kilix models install whisper-tiny-ggml` to request installation.
Installation requires an interactive terminal and explicit confirmation;
there is no `--yes` option or piped consent. The installer shows exact notices
before recording decisions. Informational notices do not request acceptance;
affirmative requirements have separate choices that start declined.

Some artifacts require user-supplied input. For example,
`kilix models install encodec-24khz-stateful --input /absolute/checkpoint.th`
verifies that input against the packaged size and digest. Supplying input is
not acceptance of invented model terms. No checkpoint is bundled by this command.

An explicit `--root /absolute/installer/root` goes before the subcommand to
target another installer root. An inherited `KILIX_CONTENT_ROOT` does not
override the host root. `kilix models reconcile-receipts` recovers an interrupted
receipt transaction without inventing decisions. The install timeout defaults
to 900 seconds; `install --timeout` accepts 1 through 3600 seconds.

Catalog membership and installation do not prove provider readiness, performance
or RAM/VRAM fit. Ordinary Amp, remote and voice launches do not automatically
run this setup interface, acquire these models or accept their terms.
