# Tmux control contract

API: `from kilix_tmux import dispatch`; call `dispatch(request)`.
CLI: `bin/kilix-tmux --socket /absolute/path --json VERB ...`.
Python CLI: `python3 -m kilix_tmux --socket /absolute/path --json VERB ...`.
Discovery: `KILIX_TMUX_CLI` may select the executable; `KILIX_TMUX_MODULE_ROOT`
may select this import root. Neither selects a socket. There is no socket
environment or default-server fallback. The socket is mandatory on every call.

Requests are objects with `operation`, `socket`, and optional boolean
`dry_run` (default false). Optional `schema` must be `kilix.tmux.request/v1`.
Unknown fields and wrong types are usage errors. Operations:

| Operation | Fields | Result |
| --- | --- | --- |
| list | none | `sessions`, each with stable `id`, `name`, `windows`, `attached`, and `panes` |
| new | `name`, optional absolute existing `cwd` | `session` with stable ID, name and panes |
| read | `target`, optional integer `lines` (1–2000, default 80) | `target` stable pane ID, `text`, `lines` |
| send | `target`, `text` | `target`, `sent`, `submitted: false` |
| type | `target`, `text` | `target`, `sent`, `submitted: true`, `completion: "unknown"` |
| key | `target`, nonempty `keys` string list | `target`, `keys`, `submitted` (true if Enter), `completion: "unknown"` |
| rename | `target`, `new_name` | `session` with stable ID and new name |
| close | `target` | `closed` stable session ID |

Targets are exact session names, stable session IDs (`$N`), stable pane IDs
(`%N`, I/O only), or `NAME:WINDOW.PANE` with numeric indices (I/O only).
Session-only I/O requires exactly one pane in the session; otherwise
`EAMBIGUOUS`. Never use tmux prefix/fnmatch lookup. Names use
`[A-Za-z0-9_][A-Za-z0-9_-]*`, maximum 128 characters. The listing reports
unsupported external names but they can still be addressed by stable ID.
Lifecycle operations accept only session names or stable session IDs.

Text must be a nonempty string without control characters except tab.
It is literal data, never interpreted by the controller. `send` does not
append Enter; `type` sends literal text and then a separate Enter invocation.
Literal text uses a unique named stdin-loaded tmux buffer, preserving trailing
semicolons. If the Enter call fails after text delivery, the error envelope
includes `details` with `sent`, `submitted: false`, and `completion: "unknown"`.
The controller never selects a shell command for a new session: tmux starts
its configured default shell. Text length is limited to 65536 characters.
Keys: Enter, Tab, Escape, BSpace, Space, Up, Down, Left, Right, Home, End,
PageUp, PageDown, Delete, C-c, C-d, C-u, C-a, C-e, C-l. At most 16 per call.

`dry_run: true` validates the entire request and resolves targets against the
specified socket. New/rename duplicate checks apply. No mutation occurs;
`data` contains `operation`, `dry_run: true`, normalized `request`, and
resolved `target` where relevant. List still reads its snapshot. Read plans
resolve without capturing. No dry-run implies execution or submission.

All responses have `schema: "kilix.tmux/v1"` and `ok`. Success: `data`
contains `operation`, `dry_run`, and operation fields above. Errors:
`error` (message), `code`, `exit`. CLI exit code matches `exit`.

| Code | Exit | Meaning |
| --- | --- | --- |
| EUSAGE | 2 | malformed request, target, socket, text or key |
| ENOENT | 3 | exact session/pane absent |
| EEXIST | 4 | new/rename name already exists |
| ETIMEDOUT | 5 | tmux subprocess deadline exceeded |
| ENOSERVER | 6 | missing/dead socket for operations requiring a target |
| ETMUX | 7 | tmux unavailable or command/snapshot failed |
| EAMBIGUOUS | 8 | session has multiple possible I/O panes |

An absent server yields an empty list; new may start a server at that exact
socket. Unresponsive or malformed snapshots are errors, never empty lists.
All subprocesses use argument arrays and the explicit `-S` socket. No
dashboard, HTTP server, session logger, setup rewrite or live-server discovery.

Needle integration: `kilix-needle tmux --socket PATH REQUEST`; MCP
`kilix_tmux_plan` / `kilix_tmux_act` with required `socket`. The grammar is
owned by Needle and must refuse unsupported or compound requests. Kilix
integration: `kilix tmux --socket PATH [--json] VERB ...`, same derivative.
