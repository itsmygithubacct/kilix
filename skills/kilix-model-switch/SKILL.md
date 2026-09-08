---
name: kilix-model-switch
description: Change the model used by an existing Codex, Claude Code, or Kimi Code session in a Kilix pane, preserving the session and verifying its effective model. Use for session model, reasoning, or fast-mode changes, not replacing one coding client with another.
metadata:
  kilix-version: "0.2.2"
---

# Switch an existing session's model

Preserve the conversation, process, working directory, and permissions. A
request to switch models is not a request to restart the client, clear or fork
its conversation, migrate providers, purchase access, or change every session.

Run `kilix agent-control --help` and `kilix agent-control list`. Resolve the
named pane unambiguously and retain its `pane_id`, `broker`, and coding-client
identity. Read it with `kilix agent-control dump "$PANE" --lines 80`. If this
is the controlling agent's own pane, do not queue keys into yourself: report
the required client action or use a separately authorized controller.

Preserve the user's exact requested model and other choices. If asked to
choose, inspect the client's currently available models and explain the
choice. Do not hard-code a “best” model or silently substitute one that merely
has a similar name. An unavailable model is a concrete limitation, not a reason
to reconfigure authentication or upgrade the client without permission.

## Switch at a safe boundary

Inspect live state. Wait for a safe input point, or obtain a checkpoint when
the user authorized administration. Do not lose an in-progress tool result or
an existing input draft. A requested immediate interruption may use the
client's observed cancel control; otherwise leave working or approval-blocked
sessions intact and report why switching is pending.

Read only the reference for the identified client:

- [Codex](references/codex.md)
- [Claude Code](references/claude.md)
- [Kimi Code](references/kimi.md)

Open the model control in the target TUI:

```sh
kilix agent-control send "$PANE" --expect-broker "$BROKER" \
  --text '/model' --submit
kilix agent-control dump "$PANE" --lines 80
```

Use the displayed names and controls, not remembered row numbers or a fixed
number of arrow presses. Move with `kilix agent-control key "$PANE"
--expect-broker "$BROKER" down` (or up), inspect the selection, then apply the
client's session-only confirmation. Commands embedded in a natural-language
message are not equivalent to the client's built-in slash commands.

Change effort/thinking or fast mode only if requested or included in the
delegated selection. Preserve other settings. A toggle is not an idempotent
setter: read current state before toggling and verify afterward.

Use a session-only control when available. If the installed client would also
save a default for future sessions and offers no session-only route, disclose
that effect and request direction before broadening the change. Editing a
shared config file is not proof that a running session changed models.

## Prove the result

Read the resulting model confirmation/status and ensure the same pane, broker,
and coding session remain. Verify the selected model, and effort/fast mode
when changed. Distinguish the client's reported selection from the model that
actually served a request; aliases and provider fallback may differ. Do not
start a paid inference merely to inspect configuration unless the task or
test explicitly calls for it.

If the command is still in the input field, inspect and submit it once with a
separate Enter; do not repeatedly resend `/model`. Stop on changed identity,
an unexpected dialog, unavailable model, or unclear persistence behavior.
Report each session's before/after state and any pending change separately.
