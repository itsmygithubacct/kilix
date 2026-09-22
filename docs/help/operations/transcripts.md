# Kilix 0.2.2: Finding and reading recorded logs

Run `kilix transcript` or `kilix transcript list` to list recorded sessions newest first. Run `kilix transcript show SESSION` to print a recorded session, replacing SESSION with an ID from the transcript list.

Run `kilix transcript path` to print the active transcript directory. Run `kilix transcript path SESSION` to resolve the file for one recorded session.

By default, transcripts live under ~/.local/gpu_terminal/kilix/state/transcripts and use the broker session ID as the filename. `kilix transcript show SESSION` reads plain or compressed logs transparently; compressed logs require zstd.

The transcript index labels sessions live, recent or older and includes archived entries alongside live logs. A live-screen snapshot and a saved transcript are different: use watch for current pane text and transcript show for recorded session output.
