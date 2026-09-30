# Structured agent actions

`kilix action` accepts a versioned JSON request and returns a compact receipt.
It shares the terminal's existing credential and broker checks. It does not
start a language model or install a component. Inspect the selected implementation
with `kilix action capabilities` before relying on a feature from a newer release.

Use this interface when an agent already knows the operation and its arguments.
Needle's structured action CLI and MCP tools use the same executor and receipt.
Needle's natural-language jobs remain useful for requests that need interpretation.

## Request

Pass JSON through stdin to avoid shell interpolation of literal values:

```sh
kilix action --request-json - < request.json
```

For example, after verifying the source and anchor pane identities:

```sh
kilix action --request-json - <<'JSON'
{
  "schema": "kilix.actions/v1",
  "operation_id": "open-build-001",
  "operation": "pane.open",
  "source": {"pane_id": 1, "broker": "aaaaaaaaaaaaaaaa"},
  "target": {"pane_id": 1, "broker": "aaaaaaaaaaaaaaaa"},
  "params": {
    "argv": ["/bin/bash"],
    "cwd": "/tmp",
    "title": "Build",
    "placement": "split",
    "direction": "right"
  }
}
JSON
```

The IDs and broker in this example are placeholders. Use the exact values from
the selected terminal's `kilix agent-control list` result. A source is the calling
pane; a target is the launch anchor or message recipient. Titles are display
labels, not identity selectors. A plan is not permission to reuse stale identities:
execution validates them again.

`--request-json -` needs the JSON body in the same invocation: use a quoted
here-document as above, a pipe, or `< request.json`. An empty stdin is refused.
`timeout` is optional and measured in **seconds**, from 1 to 60, default 15.
Omit it for a routine call or use `"timeout": 15`; `15000` is invalid.
Set `"dry_run": true` for an optional preview. An authorized action runs the
same checks directly, so a preview is not required.

Supported operations:

| Operation | Purpose |
| --- | --- |
| `pane.open` | Launch an explicit argument vector in a new pane or tab. |
| `agent.launch` | Launch a supported coding client using the existing agent-control policy. |
| `agent.deliver` | Submit a message through the verified Codex delivery helper. |
| `operation.status` | Read an operation's durable receipt without repeating its mutation. |

Each new mutation needs a distinct `operation_id`. Preserve that ID through
retries. Reusing it with different arguments is an error. Requests reject unknown
fields rather than silently ignoring misspelled arguments. Literal arguments are
not evaluated as a shell command by the executor; explicitly launching a shell
with `-c` still invokes that shell's normal semantics.

Structured agent launches do not record folder trust: `trust_folder: true` is
refused before connecting. Use the existing `kilix agent-control new-tab` or
`split` workflow with `--trust-folder` when an authorized launch must also record
folder trust. This
keeps parallel launches from racing writes to a client's shared trust config.

## Results and recovery

A receipt distinguishes a verified creation or submission from an uncertain
attempt. A `pending` receipt has no verified outcome yet; it is not proof that
the worker is still running. Creating a pane does not prove that its program is
ready, and submitting
a prompt does not prove that an agent acknowledged or completed it. Check the
explicit verification fields. This version does not infer acknowledgment from
screen activity or silence.

If a caller loses the result, submit `operation.status` with the original
`operation_id`, source, target, and an empty `params` object. The status query
does not launch or resend anything. An interrupted attempt can remain uncertain;
inspect the exact target before deciding on further work. Changing the ID merely
to escape uncertainty can duplicate the original action.

Use status to recover a missing or uncertain result. A fresh operation does not
need a status preflight, and a verified receipt does not need another receipt
lookup. Keep the receipt and its operation ID for later recovery.

For clients using only these actions, `kilix-needle mcp --tools actions` exposes
and accepts just the three structured action tools. The default MCP menu still
includes the other Needle jobs. Tool selection does not change consent, identity
checks, or receipt guarantees.

Receipts are private local state. Their idempotency guarantee depends on retaining
that state and using the same terminal and identities. Removing it removes the
record of prior attempts. Receipt status records what was verified at the time;
it is not a continuous check that a created pane remains alive.

The normal result is deliberately small. It omits terminal screenshots, prompts,
credentials, and complete process inventories. Use bounded inspection when an
uncertain result needs diagnosis.

## Validation and release use

Test CLI and MCP against the same fixed backend. Cover successful launches,
literal arguments, changed identities, duplicate operation IDs, interrupted
mutations, and missing receipts. Score the actual target state independently of
the result text. Track first-call success, recovery calls, latency, model turns,
and tokens per verified success on new task wording.

The interface alone does not establish token savings or improved benchmark
scores. Plebian OS should pin a tested Kilix/Needle combination and preserve its
existing update and rollback process. A capability query describes the selected
and observed installations; it does not update either one.
