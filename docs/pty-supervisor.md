# Trusted terminal selection and constrained actor

`kilix pty supervise --actor FILE [--needle-source DIR] [--lifetime SECONDS]`
launches a local Python actor only after a direct user has selected and confirmed
one session operation through the launcher's controlling terminal. This is a
Linux rootless Bubblewrap integration proposal; it is not enabled for existing
unrestricted agent shells, and installing or qualifying a release is separate.

The trusted user runs this command from a terminal the actor cannot control.
The supervisor asks for literal selector JSON, for example
`{"id_prefix":"job.","attached":false}`. It shows the original selector and a
complete-census resolved incarnation as escaped JSON. Only typing `END` issues
authority. The supervisor re-resolves and compares the displayed incarnation
before issuance; ambiguity, unknown facts, declined confirmation or changed
preview launch no actor. There is no selection/confirmation argument, environment
variable, stdin fallback, user-FD option, or model-exposed issuance method.

`--read-only` launches the constrained actor with no end authority and requires
no selection or confirmation. It retains the trusted terminal for revocation.
Typing a line while the actor runs revokes authority and terminates its sandbox.
The documented `REVOKE` line is conventional; any terminal input revokes. The
actor's stdout/stderr is captured as bounded data, never forwarded into this UI.
The final JSON report escapes that output. Its operation_outcome comes only
from controller receipts; actor stdout and exit zero cannot claim an end. In
selected-end mode exit zero additionally requires verified_absent. Read-only
mode permits a successful actor exit with no end. A connection is processed synchronously
with bounded reads/transport; revocation cannot recall an already sent operation.

## Isolation admission

The fixed `/usr/bin/bwrap` must be a root-owned non-writable regular system
executable. Namespace setup must succeed; there is no permissive fallback.
The actor gets fresh user, mount, PID, network, IPC and UTS namespaces, no
capabilities, no new privileges, disabled nested user namespaces and a new
terminal session. Only system executable/library trees, a private `/proc`,
minimal `/dev`, empty `/tmp`, actor source, PTY client files, one dispatch socket
and one read-only selection document are exposed. Home, host `/run`, host `/tmp`,
controller code, broker sockets/state, terminal-control credentials, host process
IDs and the user-input descriptor are not exposed. The environment is cleared;
PATH and the observational caller ID are supplied. That ID is **not** a credential.

The supervisor, system kernel/Bubblewrap/Python, source and dependency selection,
user terminal and trusted launcher are in the trusted computing base. The model
must start inside this constrained path; a model already holding unrestricted
same-UID shell, desktop remote control, host filesystem or terminal-input access
can bypass or alter the trusted launcher and is outside this admission. This is
not protection from another compromised host process, kernel vulnerabilities,
malicious administrators or physical input spoofing. The fixture exercises an
owned terminal driver as the trusted user; it does not attest a physical person.

The current dependency layout is Linux with `/usr/bin`, `/usr/lib`, `/bin`,
`/lib`, `/lib64` and rootless user-namespace support. Missing prerequisites refuse.
There is no ARM/platform matrix claim. The actor has a 256 MiB address-space
limit, five CPU seconds per process, limited file size/descriptors, bounded
output/requests and at most lifetime+5 seconds (maximum 65) wall time. These
limits suit the benign fixture, not a model-weight runtime or comprehensive
resource-abuse protection. A production model transport/resource profile remains
an independent admission and qualification task; no network/model gateway is
implicitly opened by this implementation.

## Dispatch protocol and authority lifecycle

The actor reads `/run/selection.json` for the issued original request, resolved
incarnation and caller echo. This is **not** a bearer credential. The separate
controller memory holds authority; editing the document cannot issue or broaden
it. `/run/dispatch.sock` accepts bounded newline JSON, one request per connection:

- `{"route":"selector","request":REQUEST_V2}` returns `{"code":N,"receipt":...}`.
- `{"route":"broker","argv":[...]}` implements the constrained facade below.
- `{"route":"read","request":REQUEST_V1}` returns numeric `code` and the existing
  typed host receipt. Only list, status by session ID, observe, reaped and bounded
  journal reads are available; pane/desktop lookup and legacy kill are refused.

There is no arbitrary subprocess, selection, revocation, environment, runtime or
state override route. Unknown fields/methods refuse. The existing Controller
checks exact request/context, original selector, issued incarnation, monotonic
expiry, one-shot consumption and cached replay. Its default lifetime is 30 seconds,
maximum 60. The supervisor generates an operation ID before showing/confirming
selection and never automatically restarts/reissues. User revocation, timeout,
actor completion or supervisor exit revoke authority. Inspect uncertainty using
reads; starting a new supervisor requires a new explicit user selection, not an
automatic retry of an uncertain operation.

See [controller semantics](pty-controller.md) for durable store, callback and
receiver limitations. Original-selector re-evaluation and receiver start binding
remain unchanged. Census sampling is not globally atomic; millisecond-incarnation
and attachment-ABA limits remain. Source tests do not award historical G1 credit.

## R1: administrative native broker versus constrained facade

The published native broker remains an unchanged **trusted administrative**
primitive. It has no original-user authentication. An unrestricted raw-broker
shell cannot be registered as an authenticated model route merely by adding a
nonce, environment variable or Python import.

Inside this sandbox, PATH names `/tools/kitty-pty-broker`, a controller-backed
facade. It accepts the historical core forms `list [--json] [--all]`,
`status ID [--json]`, `reaped [--json]`, `observe ID` (bounded, no stdin), and
`kill ID [--expect-started MILLIS]`. Reads use the trusted bounded transport.
A kill must name the already user-approved ID; a supplied start must also match.
It consumes the stored **original selector, all qualifiers, operation ID and
incarnation**, rather than narrowing that original request to the model's ID.
The current census and receiver checks still run. Exact retries return the cached
receipt. Runtime/transport overrides, attach, run, arbitrary flags and unrelated
IDs refuse. In read-only mode every kill refuses. Kill output is a controller
receipt, not the native broker's output; errors use controller exit semantics.

This facade preserves a legitimate positive selected-end path without exposing
native authority, but it is an explicitly changed R1-controlled route. The old
R1 guide's unrestricted `kitten @ ls`, direct journal filesystem and native socket
access are not silently restored. Historical raw R1 is retained as historical
scope. Coordinator/contract/measured workers must approve the changed route
mapping and applicable positive cases before freezing a new campaign. These
fixtures neither waive unchanged G3 nor claim full R1/G3 coverage.

## Needle and a small actor

The actor has a constrained `/tools/kilix` client. Its selected end/resolve and
request forms call the existing host selector/request parsers with a remote
dispatch controller. The client does not discover a native broker or expose the
launcher. Other administrative host commands refuse.

With `--needle-source DIR`, selected Needle PTY/parser, MCP and import-dependency
modules are mounted. The actor can run:

```
kilix-needle pty --request-json '{"operation":"end","selector":{"id":"job"},"operation_id":"KEY"}'
```

It must use the exact issued selector/key from the selection document. This
adapter calls the actual `pty_cli.main` with a remote dispatch-only controller.
Text requests use the existing grammar, for example `end selector JSON operation
KEY`. The adapter emits JSON records. `kilix-needle mcp --tools pty` runs the actual
`mcp_server.serve` newline-delimited JSON-RPC transport with only PTY tools.
Initialization, notifications, tool listing, read and end calls use that server;
this is not a substitute parser. Injection is a Python dependency, never an MCP
argument. The ordinary server has no injected controller by default.
Importing or altering client code inside the sandbox cannot reach issuance or
raw runtime sockets. Stock unrestricted Needle CLI/MCP keeps refusing selected
ends without controller authority; this adapter is a distinct constrained route.

A minimal actor file for an explicitly user-confirmed selection is:

```python
import json
import sys
sys.path.insert(0, "/tools")
from kilix_pty_agent_client import RemoteController
request = json.load(open("/run/selection.json"))["request"]
code, receipt = RemoteController().dispatch(request)
print(json.dumps(receipt))
raise SystemExit(code)
```

Run only against disposable owned sessions when qualifying this proposal. The
actor cannot select its own authority; the controlling-terminal user must do so
before it starts. The test suite includes successful selector, R1-facade and
Needle ends, read-only behavior, ambiguity/decline/changed-preview refusal,
stale/mismatched/expired authority, separate user revocation and owned sandbox
cleanup. It does not run a model or certify final release acceptance.
