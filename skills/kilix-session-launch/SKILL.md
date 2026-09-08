---
name: kilix-session-launch
description: Open a new Kilix tab, arrange panes to suit the work, launch Codex, Claude Code, or Kimi Code sessions, and deliver their initialization prompts. Use for a new coding workspace, not switching the model in an existing session.
metadata:
  kilix-version: "0.2.2"
---

# Launch a coding workspace

Translate the user's request into a new tab, suitable pane geometry, and the
requested coding sessions with their working directories, models, and initial
instructions. Preserve explicit choices. Infer a reasonable layout when the
user leaves it open; ask only for missing choices that materially change the
work. Do not create extra workers merely because space is available.

## Plan and preflight

Run `kilix agent-control --help` and `kilix agent-control list`. Identify the
source pane and exact broker identity. Keep existing tabs and their geometry
untouched. Prefer a new tab in that pane's OS window and retain focus while
building it, so the user's mouse/focus changes cannot redirect later splits.

Check the requested working directories and client executables. The helper
uses Kilix's coding-agent resolver, including vendor install locations outside
PATH; `kilix install --json` can help inspect availability. Do not silently
install/update clients, select another model, resume a random old conversation,
or relax permissions. Choose distinct worktrees only when concurrent edits
need isolation and the task authorizes their creation.

Use [layout recipes](references/layouts.md) for split trees and proportions.
Read [client startup and prompts](references/clients.md) before adding
client-specific arguments or initialization text. Each command accepts
`--dry-run`; preflight the agent and first launch before creating resources.

## Create and record exact identities

Create the first session as a new tab, anchored to the known source pane:

```sh
kilix agent-control new-tab "$SOURCE_PANE" --expect-broker "$SOURCE_BROKER" \
  --title 'Coding workspace' --cwd "$PROJECT" --agent codex
```

Add `--model "$MODEL"` when a model was chosen. Record the returned new pane,
tab, and broker IDs. For additional panes, target the new pane being split,
not the caller's original pane or whichever pane is focused:

```sh
kilix agent-control split "$ANCHOR_PANE" --expect-broker "$ANCHOR_BROKER" \
  --direction right --bias 50 --title 'Review' --cwd "$PROJECT" --agent claude
```

The new pane receives `--bias` percent of the anchor's previous area. The
helper uses explicit tab and neighboring-pane targets and never evaluates a
shell command string. Launches are held on exit to preserve startup errors.
It refuses unsupported split geometry rather than changing global layouts or
remote-control permissions.

After each launch, retain the returned identities and inspect the result. If
a command times out, inspect for the possibly created pane before retrying.
On partial failure, report the resources already created; do not duplicate
them or close an entire tab as an automatic rollback.

## Initialize only a ready coding session

Read every new pane and confirm its expected client, model, and ready input
field. A created pane does not mean login, workspace trust, or agent startup
succeeded. Leave login, payment, or unapproved permission dialogs for the
user; never paste an initialization prompt into those dialogs or a shell.

For a short single-line prompt:

```sh
kilix agent-control send "$NEW_PANE" --expect-broker "$NEW_BROKER" \
  --text "$INITIAL_PROMPT" --submit
kilix agent-control dump "$NEW_PANE" --lines 80
```

For multiline or longer prompts, preserve the exact prompt in a private UTF-8
file at a stable path the target can read. Send a short instruction to read
that file as the user's initialization prompt. Do not interpolate its contents
into shell source, split it into independently submitted messages, or delete
the file before reading is acknowledged. Preserve the user's instructions;
add role/reporting arrangements only when they were requested or delegated.

Verify submission and acknowledgement separately. If text is visibly waiting
unsubmitted, one separate Enter can finish submission; inspect again rather
than resending the whole prompt. Finish with a compact mapping of pane, client,
model, working directory, and initialization status. Leave the requested coding
sessions running; close only explicitly disposable fixtures you created.
