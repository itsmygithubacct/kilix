# Windows desktop targeting and condition waits

`kilix desktop` on Windows/WSL and `kilix windows --host HOST` on Linux use
the same SDK and native protocol. Replace `HOST` with an existing SSH alias.
Help and `capabilities` remain offline. These commands require an unlocked,
accessible desktop; they do not establish a login session.

## Window selection

```powershell
kilix desktop find --app notepad.exe --title notes --json
kilix desktop find --app 'C:\Windows\System32\notepad.exe' --json
kilix desktop find --pid 4321 --json
kilix desktop find --app notepad.exe --title notes --include-hidden --json
kilix desktop focus --app notepad.exe --title notes
kilix desktop inspect --pid 4321 --title notes --depth 5 --json
```

All supplied window filters must match:

| Filter | Match |
| --- | --- |
| `--app` / `app` | Exact executable basename **including its extension**, or absolute Windows executable path; case-insensitive ordinal comparison. Path separators `/` and `\` compare equally. |
| `--title` / `title` | Case-insensitive ordinal substring of the top-level window title. |
| `--pid` / `pid` | Exact positive uint32 process ID. |

There are no wildcards, executable-prefix matching, extension inference,
environment expansion, path-alias resolution or symlink resolution. Copy a
full path from `process_path` when necessary; dot segments/short-path aliases
are not canonicalized. App is the process executable, not its display name,
window class, browser tab URL or launch command. Hosted applications may have
an executable different from the launcher; discover the actual window first.

`windows` and window `find` normally list visible top-level windows, including
minimized ones. `windows` accepts app/title/PID too. Window `find` alone lists
all visible top-level windows. `find --include-hidden` adds hidden windows.
An element search first needs a unique visible window or an exact target.
The native process only observes its own interactive session.

Actions with CLI `--app`, `--title`, or PID-only `--pid` first perform a fresh
read-only window `find`, then send one action with the resolved full identity.
Every action argument is validated **before even that lookup**. Zero matches
give `window_not_found`; multiple matches give `ambiguous_target`. A skipped
candidate gives `search_incomplete`, even if one accessible match was found.
Neither an action nor a wait silently picks the first match.

For a previously observed window, keep the full identity:

```powershell
kilix desktop close --hwnd 123456 --pid 4321 --started 134000000000000000
kilix desktop wait --hwnd 123456 --pid 4321 --started 134000000000000000 --for closed --wait-ms 5000 --json
```

`hwnd` and `started` are positive decimal uint64 strings. `started` is the
process creation FILETIME, and `pid` is a JSON integer. Exact identity flags
must be complete and cannot be mixed with window filters. `--window HWND`
performs a fresh visible-window lookup; it cannot detect reuse before that
lookup or confirm closure of an already missing handle. Use the saved full
identity for closure waits. PID selection alone does not identify a previous
process generation. HWND/PID/FILETIME checks reject observed handle/process
mismatches; Windows exposes no window-generation token for handle recycling
within the same still-running process.

## UI Automation selectors

```powershell
kilix desktop find --app editor.exe --title Notes --control-type Button --json
kilix desktop find --app editor.exe --title Notes --name Save --automation-id saveButton --control-type Button --json
kilix desktop invoke --app editor.exe --title Notes --name Save --automation-id saveButton --control-type Button
kilix desktop set-value --app editor.exe --title Notes --automation-id documentText --control-type Edit 'Example text'
kilix desktop invoke --hwnd 123456 --pid 4321 --started 134000000000000000 42.7.9
kilix desktop set-value --hwnd 123456 --pid 4321 --started 134000000000000000 42.7.10 'Example text'
```

These are examples of provider-defined names/IDs, not built-in application
mappings. Inspect or find the real controls first. `name`, `automation_id`
and `control_type` are **case-sensitive exact** strings, combined with AND.
An empty name or automation ID is allowed and matches only that empty field.
Control types use the spelling from `inspect`, such as `Button`, `Edit`,
`Document` or `MenuItem`, without a `ControlType.` prefix. At least one UIA
filter is required. There is no fuzzy/substring UIA matching, positional
index selection or coordinate fallback.

`find` returns every matching provider-exposed control within its raw UIA
search, including disabled/offscreen controls. Matches carry runtime `id`,
`name`, `automation_id`, `control_type`, `depth`, `enabled`, `offscreen` and
`password`. It reads no ValuePattern/TextPattern text. Password names are
redacted and cannot be selected by name; IDs/types can still locate a
password control, but actions on it are refused.

`invoke`/`set-value` accept either the positional runtime ID or selector
flags, never both. Native requests use `params.element` **or**
`params.selector`. The native worker searches again inside the exact target,
requires one match in a complete search, and rechecks identity, membership,
selector and state immediately before the single mutation. Duplicate matches
return `ambiguous_element`; no match returns `element_not_found` for selectors
or `stale_element` for runtime IDs. Runtime IDs are scoped to the current UI
tree and may become stale when an application recreates controls.

Actions require a visible, non-minimized, enabled window and a visible,
enabled control. Disabled controls return `element_disabled`; offscreen
controls return `element_offscreen`. No implicit scrolling, enabling,
restoring or focusing occurs for UIA actions. `InvokePattern` or `ValuePattern`
must exist; password and read-only value controls remain refused. Keyboard
and mouse actions retain their explicit foreground-window requirement.

Element search defaults are depth 32 and 2,000 visited nodes for `find`/`wait`,
and depth 32 and 20,000 nodes for actions. `--depth` allows 0–64; `--limit`
allows 1–20,000. Depth zero considers the window's UIA root. A deeper child or
unvisited node at the bound sets `truncated`; provider failures/missing or duplicate IDs
set `incomplete`. Limits apply to **visited nodes**, not matching results.
These bounds are separate from `inspect`'s depth 3 / 200-node defaults.

`find` can return partial observations with `incomplete`/`truncated` flags.
That is not proof of absence or uniqueness. Actions refuse either flag with
`accessibility_incomplete`. Waits can retry read-only observations until their
deadline. Applications with limited accessibility expose only what their
provider supplies; a complete raw-tree search cannot infer unexposed controls.

## Condition waits

```powershell
kilix desktop wait --app editor.exe --title Notes --for visible --wait-ms 10000 --json
kilix desktop wait --app editor.exe --title Notes --automation-id saveButton --control-type Button --for enabled --wait-ms 5000 --poll-ms 100 --json
kilix desktop wait --hwnd 123456 --pid 4321 --started 134000000000000000 --name 'Saving...' --control-type Text --for closed --json
kilix desktop wait --hwnd 123456 --pid 4321 --started 134000000000000000 --element 42.7.9 --for closed --json
```

| State | Window condition | UIA control condition |
| --- | --- | --- |
| `present` | The exact window exists, including hidden/minimized/disabled windows. | One matching control exists, including offscreen/disabled controls. |
| `visible` | `IsWindowVisible` is true and the window is not minimized. | Present, parent window visible, and `IsOffscreen` is false. |
| `enabled` | Visible and `IsWindowEnabled` is true. | Visible, parent window enabled, and control `IsEnabled` is true. |
| `closed` | The exact HWND disappeared, or a complete unbound window-selector search found no matches. | A complete search found no matching control, or the exact parent window disappeared. |

Visibility does not assert foreground, unobscured pixels, successful rendering
or application readiness beyond those flags. Disabled, hidden and offscreen
controls are **not closed**. An incomplete tree/provider failure never proves
closure. Ambiguity is an immediate `ambiguous_target`/`ambiguous_element`
error for every state, rather than waiting for the ambiguity to resolve.

Window-selector waits include hidden windows, so a hidden matching window
cannot falsely satisfy `closed`. A unique first match binds its full identity
for the remainder of the invocation. Subsequent title changes do not retarget
the wait. If that bound window disappears, `closed` succeeds and other states
return `stale_target`; an observed reused HWND/PID/start identity returns
`stale_target`. An exact target already absent satisfies `closed` on the first
observation. Element selectors are re-evaluated inside the bound window each
poll; use `--element` plus an exact target to wait on one saved runtime ID.
For `closed` by selector, absence on the very first observation is success;
perform a separate `present` wait first if a workflow needs an appearance
followed by disappearance. A successful wait is a snapshot, not a lock on
application state; subsequent actions perform their own checks.

Polling happens inside **one** native worker/task, including over SSH, without
holding the mutation lock. Another command can act while a wait is running.
The read-only wait retries transient UIA provider failures, never mutations.

`--timeout` is the overall request/transport budget, 1–60 seconds (default
20). `--wait-ms` is the native condition budget, 0 through
`(timeout - 1) * 1000`, default `min(10000, (timeout - 1) * 1000)`.
`--poll-ms` is 20–1,000 ms (default 100). Zero wait means one immediate
observation. Synchronous provider calls cannot be interrupted individually:
the native outer guard allows at most one extra second for an observation
and returns `wait_timeout` if it hangs. Task startup/SSH overhead is outside
the condition budget. A 30-second condition wait, for example, requires
`--wait-ms 30000 --timeout 35`.

Unconfirmed conditions return `wait_timeout`, CLI exit 1, with elapsed time,
observation count and the last state/incompleteness in the message when the
poll loop returns. A hung-observation timeout names that limitation. A
transport-level failure remains `timeout`/`transport_error`; the client does
not disguise it as an observed condition result. No automatic mutation retry
is ever performed.

## SDK and wire shapes

```python
from kilix_sdk.windows import DesktopClient, DesktopError

client = DesktopClient()  # Or runner=an existing SSHTransport/cli_runner.
windows = client.find_windows(app="editor.exe", title="Notes")
target = client.resolve_window(app="editor.exe", title="Notes")
matches = client.find_elements(target=target, automation_id="saveButton",
                               control_type="Button")
client.wait("enabled", target=target, automation_id="saveButton",
            control_type="Button", wait_ms=5000)
client.call("invoke", target=target,
            params={"selector": {"automation_id": "saveButton", "control_type": "Button"}})
client.call("set-value", app="editor.exe", title="Notes",
            params={"selector": {"automation_id": "documentText"}, "text": "Example text"})
client.call("close", target=target)
try:
    client.wait("closed", target=target, wait_ms=5000)
except DesktopError as error:
    if error.code != "wait_timeout":
        raise
    # WM_CLOSE may have opened a save prompt. Inspect current state; do not retry close.
    print(str(error))
```

`find_windows`/`find_elements` return result dictionaries with completeness
metadata, not bare lists. `resolve_window` returns a unique full target or
raises. `wait` returns the matched observation or raises `DesktopError`.
`call` accepts `target=` or `window=` or `app=/title=/pid=`; actions with
selectors require a read plus one mutation, while `wait` with app/title/PID
performs all discovery/polling in one request. `execute` accepts a full raw
request and returns the envelope. Raw mutations always require `target`;
window filters are accepted natively only by `windows`, `find` and `wait`.

Example raw requests (each is one JSON object on stdin):

```json
{"schema":"kilix.desktop/v1","operation":"find","params":{"app":"editor.exe","title":"Notes"},"timeout":20}
{"schema":"kilix.desktop/v1","operation":"find","target":{"hwnd":"123456","pid":4321,"started":"134000000000000000"},"params":{"selector":{"name":"Save","control_type":"Button"},"depth":32,"limit":2000},"timeout":20}
{"schema":"kilix.desktop/v1","operation":"wait","params":{"app":"editor.exe","title":"Notes","selector":{"automation_id":"saveButton","control_type":"Button"},"state":"enabled","wait_ms":5000,"poll_ms":100},"timeout":20}
{"schema":"kilix.desktop/v1","operation":"invoke","target":{"hwnd":"123456","pid":4321,"started":"134000000000000000"},"params":{"selector":{"automation_id":"saveButton","control_type":"Button"}},"timeout":20}
```

The normal envelope is `{"schema":"kilix.desktop/v1","operation":OP,
"ok":true,"result":RESULT}`. Representative result shapes:

```json
{"windows":[{"title":"Notes","class":"EditorWindow","process_path":"C:\\Apps\\editor.exe","target":{"hwnd":"123456","pid":4321,"started":"134000000000000000"},"rect":{"x":50,"y":50,"width":800,"height":600},"visible":true,"enabled":true,"minimized":false}],"skipped":0,"incomplete":false}
{"target":{"hwnd":"123456","pid":4321,"started":"134000000000000000"},"elements":[{"id":"42.7.9","name":"Save","automation_id":"saveButton","control_type":"Button","depth":2,"enabled":true,"offscreen":false,"password":false}],"visited":42,"incomplete":false,"truncated":false}
{"state":"closed","matched":true,"polls":3,"elapsed_ms":204,"target":{"hwnd":"123456","pid":4321,"started":"134000000000000000"},"window":null,"element":null}
{"target":{"hwnd":"123456","pid":4321,"started":"134000000000000000"},"element":"42.7.9","completion":"provider_returned"}
```

For other successful wait states, `window` is the current window row and
`element` is the matching control row (or null for window-only waits).
`target` is null when an unbound selector was already absent for `closed`.
`process_path` can be null in an unfiltered window listing; an app-filtered
candidate with an inaccessible path is counted in `skipped`. Window searches
report skipped/incomplete candidates; UIA searches report visited nodes,
provider incompleteness and truncation separately.

Failure envelopes have no result:

```json
{"schema":"kilix.desktop/v1","operation":"wait","ok":false,"error":{"code":"wait_timeout","message":"Condition 'enabled' was not confirmed after 5000 ms (51 observations); last=disabled. No mutation was attempted."}}
```

Invalid argument combinations, strings, identities or limits fail before
transport with `invalid_request` (CLI exit 2). Keep full identities from
successful observations and inspect failures instead of resubmitting an
uncertain action.
