# Session logging (on by default)

Because the broker already owns every pane's PTY, it also records that pane's
output to a durable log — including for a pane that is detached, recovered, or
whose frontend crashed. Logs live in `~/.local/gpu_terminal/kilix/state/transcripts/`
as `<session-id>.log`, mode `0600`, one per pane.

```bash
kilix transcript                  # newest-first index, live and archived
kilix transcript show <session>   # write one transcript to stdout
kilix transcript view <session>   # search what the pane showed, in a pager
kilix transcript clean <session>  # print what the pane showed, for people or agents
kilix transcript clean --pane 29 --format json
kilix transcript path             # print the directory
kilix transcript prune            # apply the size budgets now
kilix transcript archive          # move dead logs into the denser older tier
```

Each log is bounded (8 MiB by default); on overflow the newest three quarters
are kept and the oldest bytes are dropped, so a busy pane cannot fill the disk.

A log is the raw output of whatever ran in the pane. Full-screen programs such
as coding agents redraw the screen in place, so stripping the escape codes
leaves spinner frames and half-drawn rows in no particular order. `view` and
`clean` therefore replay the log on a virtual screen, with the pinned
`third_party/kilix-transcript-clean` module, and read off what the pane showed,
in order. They drop the program's status lines and prompt boxes, and join lines
the terminal wrapped. The broker records the pane size in the log for this
(a private escape that terminals ignore), so the replay wraps where the pane
did. `clean --format json` gives the same text as sections per program, for
agents, and Needle's logs reader uses the same replay.

Click the document button in a pane's title bar to open that pane's saved log in
a new tab. The viewer opens at the newest output: `g` goes to the first retained
line, `G` to the last, `/` searches, and `q` closes the log tab. It shows a snapshot
of all retained output at opening time, including output beyond the terminal's
scrollback, replayed so it reads as the pane showed it. Logging
must already have been enabled; output removed by retention limits cannot be
recovered. The button is configurable as **Open pane session log** in Settings.

That cap bounds one file, so the **directory** has its own two budgets. A log is
plain only while its pane is live. Within a minute after the pane exits, the log
is compressed with `zstd -3` into
`transcripts/recent/<session-id>.log.zst`; the recent tier is 5 GiB by default.
When that tier fills, the oldest transcripts are recompressed with `zstd -9`
into `transcripts/archive/<session-id>.log.zst`, up to a second 1 GiB budget.
Only when both tiers are full are the oldest archives dropped. A live pane's log
is never touched — the broker holds that descriptor. The budgets are enforced
periodically while the Kilix frontend runs, and on demand with `prune`. Set the
older tier to `off` for a hard ceiling at the recent-tier budget. The 6 GiB
combined default fits the release image's 20 GiB minimum disk while leaving the
larger presets available to operators with room for more history.

Reading an archived transcript is the same command: `kilix transcript show`
decompresses transparently, and `kilix transcript path` resolves to whichever
tier a session ended up in, so nothing needs to know where a log lives. Archiving
is lossless — an archived transcript reads back byte-for-byte identical.
Kitty **graphics payloads are elided** by default — a pane running Kilix 95,
`browse`, `run`, or `icat` emits base64 pixel data at megabytes per second, and
recording it verbatim would evict every readable line within seconds. The log
keeps a `[kitty-pty-broker: N bytes of graphics elided]` marker instead, which
makes a default transcript a faithful record of *text*, not a byte-exact
capture. Choose `keep` when the graphics bytes are themselves the subject.

Only pane **output** is recorded. Input appears solely through terminal echo,
so a password prompt that disables echo is not captured.

Turn it off, or change its two knobs, from any settings interface — they all
write the same shared file:

```bash
kilix settings                            # TUI, "Session logging" section
kilix settings --set transcript=off
kilix settings --set transcript_size=32M          # 2M | 8M | 32M | 128M
kilix settings --set transcript_graphics=keep     # elide | keep
```
