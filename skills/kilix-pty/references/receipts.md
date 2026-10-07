# Receipts, exit codes and the JSON request route

For `kilix-pty`. Every `--json` document is `{"schema":"kilix.pty/v1","runtime":...,"timeout_seconds":...}` plus
its fields. Errors that are not an answer go to stderr as one `kilix pty: ...` line.

## kill

`kilix pty kill ID --yes --expect-started MILLIS --json` returns `result`, `id`, `request_sent`, `reason`, `message`:

| result | exit | meaning |
| --- | --- | --- |
| `verified_absent` | 0 | terminated, then the listing no longer shows this ID and `started_millis` |
| `uncertain` | 1 | re-list before retrying; `request_sent` says whether anything was sent |
| `refused` | 3 | `caller_unidentified` (no `KITTY_PTY_BROKER_SESSION`: stop and report), `own_session`, `started_mismatch` (another session now has the ID), `cannot_bind` (an old broker cannot check the identity; nothing was done) or `declined` |
| `not_found` | 4 | no such session; nothing was sent |

Usage errors exit 2. `status` of a missing session is `not_found` with exit 4.

## Reads

- `kilix pty list --json`: `sessions` (each with `id`, `attached`, `started_millis`, `cwd`, `cwd_now`) and `unreachable`.
- `kilix pty observe ID --once --lines 50 --json`: `text` or `bytes_b64`, `truncated`, `cursor`, `untrusted`.
- Bound a read with `--lines N` or `--bytes N`, not both. `--timeout SECONDS` (0.1-60) goes before the verb.

## The JSON request route

One request in, one receipt out, the same documents as above. Its bounds are `max_lines` (1-10000) or
`max_bytes` (1-1048576). `kill` needs an `operation_id`, `expect_started_millis` (the `started_millis` you just read) and `--yes` on the command line.

```sh
kilix pty request --request-json - <<'EOF'
{"schema":"kilix.pty.request/v1","verb":"observe","args":{"id":"3fa9c2d41b7e6a05","max_lines":50,"text":true}}
EOF
```

The same `operation_id` with the same arguments returns the stored receipt with `"duplicate": true`. Do not reuse
an `operation_id` after `uncertain`: re-list, then use a new one.
