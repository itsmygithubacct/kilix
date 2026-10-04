---
name: kilix-session-launch
description: Open a new Kilix tab, arrange panes to suit the work, launch Codex, Claude Code, or Kimi Code sessions, and deliver their initialization prompts. Use for a new coding workspace, not switching the model in an existing session.
metadata:
  kilix-version: "0.2.2"
---

# Launch a coding workspace

Turn the request into a new tab, a pane layout, and coding sessions with their directories,
models and initial instructions. Keep explicit choices, infer a layout the user left open,
and ask only about choices that change the work. Do not add workers because there is room.

## One agent, nothing else

`kilix new-tab --title TITLE --cwd DIR codex`, or another agent command. One command, no
preflight: in the 2026-09-29 route benchmark it cost a quarter of the procedure below.

The command confirms pane creation. Before reporting a ready agent, inspect
the returned pane's client and directory with `kilix agent-control list`;
use `dump` if readiness is unclear. Keep creation, readiness and completion
separate. If the client exited or a login/trust dialog is present, report
that state and preserve the pane instead of launching another session.

## Layouts, several sessions, models or initial prompts

`kilix agent-control` pins every launch to a verified pane:

```sh
kilix agent-control list          # your pane_id and broker: SOURCE_PANE, SOURCE_BROKER
kilix agent-control new-tab "$SOURCE_PANE" --expect-broker "$SOURCE_BROKER" \
  --title 'Coding workspace' --cwd "$PROJECT" --agent codex
kilix agent-control split "$ANCHOR_PANE" --expect-broker "$ANCHOR_BROKER" \
  --direction right --bias 50 --title 'Review' --cwd "$PROJECT" --agent claude
```

- Add `--model M` for a chosen model; any command takes `--dry-run`. `--bias` is the new
  pane's percentage of the anchor's area.
- Split the new panes, by the ids each command returns; never the caller's pane or
  whichever pane has focus. Record every returned pane, tab and broker id.
- Layout recipes: [layouts](references/layouts.md). Read [clients](references/clients.md)
  before adding client arguments or initial text.
- Leave existing tabs alone. Do not install or update clients, change the model, resume an
  old conversation or relax permissions unless asked. Create separate worktrees only when
  concurrent edits need them and the task allows it.
- If a command times out, look for the pane before retrying. On partial failure, report
  what was created; do not duplicate it or close a whole tab as a rollback.

## Initial prompts

Dump each new pane and confirm the client, model and a ready input field. Leave login,
payment, trust and permission dialogs to the user; never type a prompt into them or a shell.

```sh
kilix agent-control send "$NEW_PANE" --expect-broker "$NEW_BROKER" \
  --text "$INITIAL_PROMPT" --submit
kilix agent-control dump "$NEW_PANE" --lines 80
```

For a long or multiline prompt, save it in a private file the target can read, send a
short instruction to read it, and keep the file until the target acknowledges it; do not
split it into several messages. Check submission and acknowledgement separately: if the
text sits unsubmitted, one separate Enter may finish it; never resend the whole prompt.
Finish with a table of pane, client, model, directory and initialization status. Leave the
sessions running; close only disposable fixtures you made.
