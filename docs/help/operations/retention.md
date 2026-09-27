# Kilix 0.2.2: Configuring recorded-log retention

Run `kilix settings --set transcript=off` to disable session logging. Run `kilix settings --set transcript=on` to enable session logging.

Use `kilix settings --set transcript_size=32M` for a 32 MiB per-log limit; available presets are 2M, 8M, 32M and 128M. Run `kilix transcript prune` to apply the configured transcript storage budgets immediately.

Run `kilix transcript archive` to compress dead transcripts regardless of the recent-tier budget; live logs are left alone. Graphics payloads are elided by default; use `kilix settings --set transcript_graphics=keep` when the graphics bytes themselves must be recorded.

Only pane output is recorded; input appears in a transcript only through terminal echo. The default per-log cap is 8 MiB; on overflow the newest three quarters are retained and the oldest bytes are dropped.
