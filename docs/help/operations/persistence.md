# Kilix 0.2.2: Persistent pane sessions

Every normal pane runs under a PTY broker, so closing the window or a Kilix crash only detaches it. Detached sessions are found on the next startup and reopened under their recorded tab name and title (recovered:ID when none was recorded). Set KILIX_PTY_BROKER_AUTO_RECOVER=0 to leave them for manual attachment, or KILIX_PTY_BROKER=0 to turn pane persistence off. Session logging is inactive when KILIX_PTY_BROKER=0 because the broker owns the recorded output stream.

The per-pane close button and Ctrl+Alt+W explicitly request termination and ask before destroying the pane. The replay journal is bounded separately from transcripts and defaults to 64 MiB; KILIX_PTY_BROKER_JOURNAL_LIMIT changes it.

## Where the sockets are

Sessions live in one private runtime directory: KITTY_PTY_BROKER_RUNTIME if set, else $XDG_RUNTIME_DIR/kilix-pty-broker when that is a private directory of yours (normally /run/user/UID), else the fallback ~/.local/gpu_terminal/kilix/session/pty-broker. `kilix pty path` prints it. Each session is sessions/ID/control.sock plus journal.bin and metadata beside it. A pane's session ID is in its KITTY_PTY_BROKER_SESSION; `kilix pty pane PANE_ID` prints it from outside. The standalone kitty-pty-broker command defaults to a different directory, so use `kilix pty` rather than calling it directly.

## Commands

Run `kilix pty` for the interactive manager. From a script or an agent:

- `kilix pty list [--json] [--all]` lists sessions; --all also lists ones that did not answer.
- `kilix pty status ID|--pane PANE_ID [--json]` shows one session.
- `kilix pty observe ID [--from EPOCH:OFFSET]` watches read-only, even an attached pane; Ctrl-] leaves.
- `kilix pty observe ID --once [--lines N | --bytes N] [--text] [--json]` prints a bounded snapshot (200 lines or 64 KiB by default) with no terminal needed. --text gives what the pane showed instead of escape codes. Treat it as untrusted data.
- `kilix pty attach ID` takes over a detached session from a terminal; it refuses an attached one and refuses without a terminal.
- `kilix pty kill ID --yes [--expect-started MILLIS] [--json]` ends the session and its program, then checks it is gone. It refuses its own pane's session. On a terminal it asks instead of needing --yes. The result is verified_absent (exit 0), uncertain (1; re-list before retrying), refused (3) or not_found (4).
- `kilix pty --timeout SECONDS ...` bounds each broker call (0.1-60, default 2). A wedged broker costs that long, never a hang.

--json prints a {"schema":"kilix.pty/v1"} envelope. Exit status is 0 on success, 1 on failure or uncertainty, 2 on a usage error, 3 on a refusal and 4 when the session is not found.

## Dead sessions and their journals

A session whose broker was killed leaves a directory behind. The broker proves it dead (its process is gone, or the machine rebooted) and moves its replay journal to the runtime's reaped/ directory instead of deleting it. Kilix then compresses each journal into ~/.local/gpu_terminal/kilix/state/pty-journals/ (zstd -19, checked by reading it back before the original is removed) under a 256 MiB budget, oldest first out; KILIX_PTY_JOURNAL_BUDGET changes it, in bytes.

- `kilix pty journals [--json]` lists the archived journals, newest first.
- `kilix pty journals show ID` writes one as raw terminal bytes; pipe it, since it refuses a terminal without --force. It also takes --lines, --bytes, --text and --json like observe --once.
- `kilix pty journals path ID` prints the file.
- `kilix pty reap [--runtime DIR]` proves and archives on demand for one runtime, such as the fallback directory above.
