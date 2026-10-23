# Selected-session authority

`kilix_pty_controller.Controller` is an embedding interface for a trusted
supervisor. The supervisor calls `select()` from a direct structured user
selection event **before** exposing `dispatch()` to an actor. It must never call
`select()` using an actor's interpretation of natural language, tool arguments,
terminal output, retrieved text, or an exact ID chosen after listing candidates.
There is deliberately no CLI flag, environment variable, token file, or model
RPC for issuance. Ordinary selector `end` and request-v2 `end` now refuse with
`authority_required`. Resolve/list/status/observe remain available.

Practical integration: a trusted application presents selector fields (one ID,
prefix, or literal startup-word array, plus optional attachment predicate) and
an explicit End action. It constructs the v2 request and operation ID, then
calls `controller.select(request)`. Successful issuance reports the original
selector and bound incarnation to that UI. The application may then forward
actor requests only to `controller.dispatch(request)`, or inject that object
into Needle's `pty_cli.run(..., controller=controller)` or
`pty_cli.mcp(..., controller=controller)`. The controller is a Python dependency,
not a JSON field; stock stdio MCP and CLI cannot issue or inject it. Legacy
Needle `end session ID` refuses: use an explicitly selected v2 operation.

## Deployment trust

This is a control boundary for an isolated actor, not a same-UID security
boundary. The supervisor, its Python interpreter, source, broker executable,
state directory, caller context and user event source are trusted. The actor
must have no shell/code execution inside that supervisor, no broker sockets,
no state-file access, no ability to set its environment, and no remote-control
or terminal-input access to the user selection UI. A separate OS user/container
with a narrow dispatch-only IPC adapter is one suitable deployment. Its user
selection channel must be owned by the trusted UI, not passed to the actor.
The embedding application must supply that isolation; this library cannot
attest it. No isolated graphical UI or deployment is installed by this change.

Existing unrestricted Kilix shell sessions do **not** meet this condition.
Low-level broker operations and host legacy v1 job/kill APIs remain trusted
administrative/owned-job primitives, not authorized model tools. Keeping them
available preserves owned-session lifecycle behavior but means that hiding a
single tool or adding this Python object does not sandbox an actor with shell
access. Raw broker and legacy host access must be excluded at the actual actor
boundary. There is no claim of stronger original-user authentication for those
paths. Caller identity remains environment-sourced and authenticated=false;
selection provenance is a distinct assertion by the trusted supervisor.

## Scope and lifetime

One controller holds at most one grant. Issuance revokes the prior grant even
when the new selection fails. It validates the original request, resolves it
with the existing complete-census evaluator, refuses unknown/ambiguous/own
selection, and captures runtime directory identity, caller, timeout and full
resolved incarnation. Grant lifetime uses a monotonic clock, defaults to 30
seconds and is capped at 60, including issuance time. The actor must submit the
identical request, including operation ID and qualifiers. A scope/context
mismatch revokes the grant. Expiry and explicit `revoke()` remove it. Process
exit loses all authority; nothing reloads a grant from disk or an environment.

Dispatch consumes once before admission. Immediately before the durable dispatch
phase it compares the fresh resolution/context with the issued binding and
checks expiry again. Existing census, original-selector re-evaluation, caller
refusal, receiver incarnation binding, durable operation store and replay
checks remain. A same-request retry within the lifetime returns the cached
outcome only; it never re-resolves. Exceptions/cancellation cannot rearm a grant.
After expiry/revocation no retry sends. Durable operation keys still prevent
re-admission after controller restart when a trusted user explicitly selects
again. State corruption, eviction and uncertain outcomes retain existing limits;
users must not invent a fresh key to evade uncertainty.

Calls are serialized. Revocation linearizes before a dispatch starts or after
that synchronous dispatch finishes; it cannot recall an already sent operation.
This is not an interruptible emergency-stop channel. Do not concurrently mutate
the supervisor environment or broker object.

The broker's sampled census is not globally atomic. Millisecond start identity
collision and attachment ABA limitations remain. Extra process/boot fields are
compared at the controller recheck but the receiver protocol is unchanged.
Source fixtures do not rescore historical G1 FAILED/HOLD, establish model
qualification, or certify final release acceptance.
