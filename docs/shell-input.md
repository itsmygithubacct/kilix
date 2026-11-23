# Verified shell input

Kilix 0.2.4 can run one command line in an existing pane only when the pane's
shell proves, at the moment it accepts the command, that its line editor is at a
fresh primary prompt with an empty buffer. In every other state the request is
refused and nothing the user typed is changed.

A prompt mark (OSC 133), the pane title, the foreground process and the screen
text say nothing reliable about the line editor: a vi command mode, a reverse
history search, a continuation line, a draft and type-ahead all look like "a
prompt". Observing the pane and then typing is also unsafe, because the person
can type between the observation and the keys. The proof is therefore split
between the two parties that each see part of the truth.

## Who proves what

| Party | Knows | Checks |
| --- | --- | --- |
| Shell integration (`kitty.bash`) | The editor mode, buffer, prompt kind, unread type-ahead | Empty `READLINE_LINE`, emacs or vi insert keymap, primary prompt (not PS2), its own one-time token |
| Engine (`kitty/shell_input.py`) | Every byte sent to the pane (`schedule_write_to_child`), command-start marks, process groups | No byte since the prompt was reported, no command started, shell leads the foreground process group, no alternate screen, prompt id matches the plan |
| Needle | The plan and the pane's identity | The pane's id, pid, child pid, tab and broker session are unchanged, and the prompt id it planned against is still current |

The engine never sees the buffer text, only whether the shell accepted or why it
refused.

## Protocol (version 1)

1. Before each primary prompt `PROMPT_COMMAND` runs `_ksi_input_ready`, which
   draws a fresh random token, checks `read -t 0` for unread type-ahead and
   sends `DCS @kitty-input|ready;v=1;pid=PID;token=TOKEN;pending=0|1 ST`.
   A redraw of the same prompt sends nothing.
2. The engine stores the prompt with a new random `prompt_id` and the input
   byte count at that moment. `kitten @ ls` reports
   `shell_input: {"verifiable": true, "prompt_id": ..., "protocol": 1}`, or
   `{"verifiable": false, "code": ..., "reason": ...}`.
3. `kitten @ shell-submit --match id:N --expect-prompt PROMPT_ID` (command on
   stdin) is checked in one main-loop step against everything in the engine's
   column above. Then the engine writes the command to
   `$XDG_RUNTIME_DIR/kilix-shell-input/request-TOKEN` (directory 0700, file
   0600, created exclusively). It queues one inert key, `CSI 63171 u`, which
   carries no command text, and starts a three-second timer.
4. In emacs and vi insert keymaps that key runs a `bind -x` handler inside
   readline. The handler claims the request by creating `claim-TOKEN`
   exclusively, rejects control characters, places the command in the empty
   buffer and accepts the line. In any other state it replies
   `result;status=refused;reason=...` and leaves the buffer alone. vi command
   mode, and an incomplete ESC or Ctrl-X prefix left by type-ahead, have their
   own refusing bindings.
5. If no reply arrives before the timer fires, the engine creates the claim
   itself so the request can never run late. A shell that claimed it first is
   reported as `unconfirmed`, never retried.

A token is single use; the next prompt has a new token and `prompt_id`. Bytes
queued before the shell marks a command start (OSC 133;C) were read as that
command's line. Bytes queued after it and less than 250 ms before the next
prompt make that prompt unverifiable, because they may still be in flight.

Refusal codes: `unavailable`, `busy`, `stale`, `input`, `in_flight`,
`invalid_command`, `timeout`, `unconfirmed`, `closed`, and, from the shell,
`draft`, `continuation`, `vi_command`, `pending_key`, `no_request`, `claimed`,
`unverifiable`.

## Other shells

None of these integrations sends the ready message today, so their panes report
`unavailable` and every submission is refused.

| Shell | What its integration does now | Can it supply the proof? |
| --- | --- | --- |
| zsh (`zsh/kitty-integration`) | precmd/preexec marks, `zle-line-init`/`keymap-select` widgets for cursor shape | Yes, in principle. A ZLE widget bound to the trigger sees `BUFFER`, `PREBUFFER` (non-empty at a continuation), `KEYMAP` (`vicmd`), `ZLE_STATE` and `PENDING`, and can set `BUFFER` and call `accept-line`. The work still to do: emit the ready message from the last precmd hook, and handle plugins that rewrap `zle-line-init` and isearch widgets (ZLE leaves isearch on an unbound key, as readline does). |
| fish (`fish/vendor_conf.d/kitty-shell-integration.fish`) | `fish_prompt`/`fish_preexec` marks, cursor shape by `fish_bind_mode` | Likely. A `bind` to a function sees `commandline` (buffer), `commandline --paging-mode`/`--search-mode`, and `fish_bind_mode`, and can `commandline -r` and `commandline -f execute`. fish has no PS2, since multi-line input stays one buffer, so "empty buffer" covers continuations. It needs fish 3.x key-binding syntax checks and the same type-ahead handling. |
| PowerShell (`powershell/kitty.ps1`) | Wraps `prompt` to emit OSC 133;A and a title | Possibly, with PSReadLine only. `Set-PSReadLineKeyHandler` can bind the trigger, and `[PSConsoleReadLine]::GetBufferState` gives the buffer and cursor. Continuation prompts and vi mode would need checks, and without PSReadLine there is no editor hook at all. Not measurable here (no pwsh installed). |
| dash, `sh`, ksh | No integration | No. They have no editor hook that can inspect a buffer. |

## Known limits

- **History search.** Readline ends an incremental search before any bound key
  runs. The engine's "no byte since the prompt" check refuses before the key is
  sent in practice, but a search started in the few milliseconds between the
  shell reporting its prompt and the engine reading that report is ended by the
  key. A found match is refused as a draft and kept on the line. An empty
  search is accepted.
- **The quiet interval** of 250 ms assumes bytes reach the pane within that
  time, which holds for local and brokered panes. A key typed right before a
  prompt makes that prompt unverifiable. So do Enter on an empty line and
  Ctrl-C, because bash marks no command start for either. The pane is
  verifiable again at the prompt after the next command that actually runs.
- **Remote and nested shells** (ssh, `sudo -s`, a shell inside tmux or screen)
  are refused, since the reporting process does not own the pane's terminal.
- **A misdelivered trigger** is one inert key: a program that ignores unknown
  `CSI u` keys sees nothing.
- **Bash only**: 4.4 or later, readline editing on, `promptvars` on, loaded
  through Kilix's shell integration. A user `bind` of `\e[63171u` or of the two
  helper keys `\e[63172u`/`\e[63173u` overrides the handler and the request
  times out.
