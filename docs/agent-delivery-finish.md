# Finish an already staged delivery

Normal delivery recovery remains observation-only: a repeated `deliver` call
never pastes the message again. To finish a message whose text was staged but
whose submit key was never attempted, use the explicit `--finish-staged` path
with the original message ID, target, mode and body:

```sh
kilix agent-control deliver PANE_ID --expect-broker BROKER_ID \
  --message-id review-note-7 --mode steer --finish-staged \
  --file /private/path/to/the-original-message.txt
```

The file must contain the same original body used for the first delivery. The
body is used only to recompute the saved digest and compare the framed client
composer; it is never pasted by this operation. `--text` is also supported.
Keep the original pane, broker and mode. A changed value, missing or corrupt
receipt, changed foreground process, or composer that is empty, partial,
collapsed or different blocks input. The receipt must still be in `pasting`.
Composer wrapping is accepted only when every original character is visible.
The parser removes the provider's one recognized prompt gap and continuation
indentation, then compares observed row content exactly. Claude's observed typed
composer uses one NBSP after `❯`; the supported ASCII-space frame remains
recognized. The parser removes only that single prompt spacer, preserving every
payload character including repeated spaces. It never infers a missing
separator at a wrap seam. If a client drops a separator while wrapping, inspect
the composer and resolve the staged delivery manually; automatic completion
blocks it.
Wrapped Claude drafts are deliberately not accepted: Claude does not draw the
space it wraps at, so a hard newline or an extra space at a full row renders
identically and no screen reader can prove the exact input. A refusal names this
ambiguity. For long messages use the opt-in `--envelope` (structured action
param `"envelope": true`): the exact caller text is stored unchanged in a private
content-addressed file `~/.local/state/kilix/envelopes/<first 32 hex of its
SHA-256>.txt` (0600, create-exclusive, never overwritten) and only the short
one-row pointer `envelope <path> bytes=<n>` is typed, so the normal exact-match
guards apply. The receipt reports `envelope.path/sha256/bytes`; the reader
should verify the hash. A pointer that would wrap on the target pane is refused
before any input. `--finish-staged` with `--envelope` requires the same file.
Claude's recognized shell-count footer requires `1 shell` or a plural count
(`2 shells` and above), with only the known optional interrupt/agent hints.
Malformed counts and unknown footer text remain unsupported.

After a fresh exact composer check, Kilix durably records `submitting` before
sending one Enter for `steer` or one Tab for `defer`. A `submitting` receipt is
never keyed again, because a previous key may have taken effect. A later call
can only observe and verify the result. Already submitted receipts return
without terminal input.

For structured actions, use a new action `operation_id` and identify the old
delivery receipt separately:

```json
{
  "schema": "kilix.actions/v1",
  "operation_id": "finish-review-note-7",
  "operation": "agent.deliver",
  "source": {"pane_id": SOURCE_PANE_ID, "broker": "SOURCE_BROKER_ID"},
  "target": {"pane_id": TARGET_PANE_ID, "broker": "TARGET_BROKER_ID"},
  "params": {
    "text": "The exact original body goes here.",
    "mode": "steer",
    "finish_staged_message_id": "review-note-7"
  }
}
```

The `text` and `mode` must match the original delivery. `finish_staged_message_id`
selects its existing target ledger entry; the new `operation_id` protects this
structured action receipt. Finish-staged actions cannot be dry-run previews.
