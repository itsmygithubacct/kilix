---
name: kilix
description: Inspect and control Kilix terminal panes and coding sessions. Use for finding sessions, reading their output, sending instructions, or administering a user-selected group of panes.
metadata:
  kilix-version: "0.2.2"
---

# Kilix pane control

For Kilix 0.2.2. These commands are the cheapest reliable route (2026-09-29 route
benchmark); `kilix --help` lists them too. Run them directly, with no preflight:

- Open a shell pane beside yours: `kilix pane right|left|up|down [--title T] -- bash`
- List panes: `kilix pane list` (ids, titles, programs; `(self)` is you)
- Type into a shell pane: `kilix pane send TITLE 'TEXT' --submit`
- Close a pane: `kilix pane close TITLE`
- A coding agent in a new tab: `kilix new-tab --title T --cwd DIR codex`
- Files and logs: `find`, `rg`

Target a pane by its unique title or `pane:ID`; a shared title is refused with the matching ids,
and a bare number is an id. Closing kills the pane's programs: close
only exact panes the user asked for, or disposable ones you made. Do not poll a live session
with `kilix` in a loop (each call reloads its config), install upgrades, or start a second
remote-control daemon.

## Typing into another coding agent

Only for another coding agent's input box; shell panes use `kilix pane send`
above. `kilix agent-control` checks the target's broker identity before any input. Read
[agent input](references/agent-input.md) before the first `deliver`, `send` or `key`.

```sh
kilix agent-control list                     # JSON: pane_id, broker, title, cwd, program
kilix agent-control deliver "$PANE" --expect-broker "$BROKER" \
  --message-id task-42 --text 'Read the prepared result and report your findings.'
```

`deliver` supports Codex's recognized input layout. It checks the empty composer,
pastes once, checks the full message, sends separate Enter, and verifies submission.
It returns a short JSON receipt; `submitted` is not acknowledgment or task completion.
Reuse the same message ID when retrying the same payload: verified repeats return the
receipt, and uncertain repeats never resend input. Keep messages within 900 UTF-8 bytes
including the helper's ID prefix; use a file reference for longer briefs. `--mode defer`
uses Tab only for an intentional next-turn follow-up to a visibly busy session.

Stay within the user's target and scope. For other clients or unrecognized layouts,
use `dump`, `send` without `--submit`, another `dump`, a separate broker-checked
`key ... enter`, and a final `dump`. Never append to occupied input, send into menus,
or resend a prompt that may have been accepted. Inspect an `uncertain` result instead
of assigning a new ID to retry it.

## Coordinating a group

Follow the user's arrangement. As coordinator, give bounded assignments with owners and
reporting places, collect acknowledgements, and route follow-ups yourself; do not impose
the arrangement on other sessions or create workers unasked. New tabs and layouts:
`kilix-session-launch`; model changes: `kilix-model-switch`. Report pane identities,
verified outcomes and what is still unverified. Sending "continue" completes nothing.

## Tmux session control

Use `kilix tmux --socket /absolute/path --json VERB ...` for the server the user
selected. The socket is required on every control call; `TMUX` does not select
it. `kilix tmux --help` describes control, and a verb's `--help` lists its
arguments. With no arguments, `kilix tmux` opens the interactive manager.

Operations are `list`, `new`, `read`, `send`, `type`, `key`, `rename`, and
`close`. Address exact session names, stable session IDs (`$N`), stable pane
IDs (`%N`), or numeric `NAME:WINDOW.PANE` for I/O. Quote IDs containing `$`
so the caller's shell does not expand them. Session-only I/O is refused if
more than one pane exists. Read before input when the target's state matters.

`send` writes literal text without Enter. `type` writes literal text and then
sends separate Enter. `key` accepts named keys, including Enter. A response
with `submitted: true` records input submission; command completion remains
unknown until separately observed. Use `--dry-run` to validate and resolve
a request without mutation. Close only the exact session within the user's
requested scope. Implementation selection uses `KILIX_TMUX_CLI` or
`KILIX_TMUX_MODULE_ROOT`; these do not choose a socket or install anything.
