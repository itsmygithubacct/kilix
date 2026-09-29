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
[agent input](references/agent-input.md) before the first `send` or `key`.

```sh
kilix agent-control list                     # JSON: pane_id, broker, title, cwd, program
kilix agent-control dump "$PANE" --lines 60
kilix agent-control send "$PANE" --expect-broker "$BROKER" --text 'TEXT' --submit
kilix agent-control dump "$PANE" --lines 60
```

Stay within the user's target and scope, and check the dump first: never send a prompt into
a shell, password field, approval dialog or model menu. `request_sent` means only that it
was sent; never resend a prompt that may have been accepted.

## Coordinating a group

Follow the user's arrangement. As coordinator, give bounded assignments with owners and
reporting places, collect acknowledgements, and route follow-ups yourself; do not impose
the arrangement on other sessions or create workers unasked. New tabs and layouts:
`kilix-session-launch`; model changes: `kilix-model-switch`. Report pane identities,
verified outcomes and what is still unverified. Sending "continue" completes nothing.
