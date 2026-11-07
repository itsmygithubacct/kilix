"""Route ``kilix transcribe`` without disturbing the prepared tiny-model runtime.

``prepare``, ``serve``, ``status``, ``models``, ``record``, ``unload``,
``cancel``, and ``file`` with no engine stay on that runtime. Selecting an
engine, installing one, or chunking audio uses the file-transcription CLI.
"""

from __future__ import annotations

HELP = """\
usage: kilix transcribe COMMAND [ARGS]

Select engines, install them, and transcribe a file:
  kilix transcribe engines [--json] [--installed]
  kilix transcribe install ENGINE [ENGINE ...] [--default]
  kilix transcribe probe AUDIO [--json]
  kilix transcribe chunk AUDIO --output DIR [--seconds N] [--json]
  kilix transcribe run AUDIO (--engine ENGINE | --engines A,B) [--passes N]
                       [--chunk-seconds N] [--format text|json|srt|vtt]
                       [--reference FILE] [--threads N] [--work DIR]
                       [--output FILE] [--clean] [--dry-run]
  kilix transcribe file AUDIO --engine ENGINE [same flags as run]

engines lists the dictation catalog and whisper-tiny-ggml.
install runs `kilix stt --install`, or `kilix models install` for the tiny model.
--default names one dictation engine as the `kilix stt` default.
probe reads duration without decoding. chunk writes 16 kHz mono wav pieces.
Pieces stay under the recogniser's ten-minute buffer. Several engines share one
chunking. --passes keeps every run and marks the fastest.
RTF is recognition time divided by audio length. Dictation model load and the
wav decode are outside that clock. --reference adds the fastest pass's word
error rate and the lowest rate across passes.
srt and vtt take one engine and time each chunk, not each word.
--clean runs `kilix split` and transcribes the vocal stem. Install that
splitter with `kilix split --install`.
--dry-run prints the chunk plan and does not load a model or separate stems.

Managed tiny model (unchanged):
  kilix transcribe prepare --engine PATH --engine-sha256 SHA256 --decoder PATH --decoder-sha256 SHA256
  kilix transcribe serve
  kilix transcribe status
  kilix transcribe models
  kilix transcribe file AUDIO --format json
  kilix transcribe record --seconds N
  kilix transcribe unload
  kilix transcribe cancel
"""

PROVIDER_COMMANDS = {"prepare", "serve", "status", "models", "record", "unload", "cancel"}


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else [])
    if not args or args[0] in {"-h", "--help", "help"}:
        print(HELP, end="")
        return 0
    command = args[0]
    # `--engines A,B` is also a file-transcription selection. A bare `file`
    # stays on the prepared runtime, including its own extra flags.
    file_selects_engine = command == "file" and ("--engine" in args or "--engines" in args)
    if command in PROVIDER_COMMANDS or (command == "file" and not file_selects_engine):
        from kilix_sdk.transcribe_provider import main as provider_main
        return provider_main(args)
    from kilix_sdk.transcribe_cli import main as cli_main
    return cli_main(args)
