# Kilix 0.2.2: Persistent pane sessions

Run `kilix pty` to open the interactive manager for persistent pane sessions. Detached sessions are discovered on the next startup and opened in recovered:ID tabs by default.

Set KILIX_PTY_BROKER_AUTO_RECOVER=0 to leave detached sessions for manual attachment. Set KILIX_PTY_BROKER=0 to disable pane persistence.

Session logging is inactive when KILIX_PTY_BROKER=0 because the broker owns the recorded output stream. The broker replay journal is bounded separately from transcripts and defaults to 64 MiB.

KILIX_PTY_BROKER_JOURNAL_LIMIT changes the replay-journal bound. The per-pane close button and Ctrl+Alt+W explicitly request termination and ask before destroying the persistent pane.
