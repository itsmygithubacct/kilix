# Durable agent mailboxes

`kilix ralph` exposes the pinned [Ralph module](https://github.com/itsmygithubacct/kilix-ralph#readme)
without starting the terminal engine. Run `kilix ralph --help` for its queue,
receiver and worker commands. Pull receivers explicitly claim messages at their
idle boundary. Provider adapters require their own guarded delivery and durable
receipts; they do not use terminal keystrokes or the pane activity detector.
The managed streaming provider owns a separate session and does not take over
an existing interactive session. Native provider compatibility requires separate
qualification against the selected client version.

Recurring schedules start paused. Use `--max-wakes` to bound a schedule and
`serve --once` for a single worker pass. No worker starts at installation or boot.
Cancellation applies only before a message is claimed; uncertain or provider-owned
messages require reconciliation and must not be blindly resent. Worker shutdown
preserves queue state. See the module documentation for restart, cleanup and
receipt retention limits. `KILIX_RALPH_HOME` selects a local module checkout.

Selected-session original-user authority requires a trusted embedding controller;
ordinary selector `end` CLI/JSON calls now refuse with `authority_required`.
Read-only operations and owned-job primitives remain available. See
[the controller boundary](pty-controller.md) for issuance, scope, expiry,
replay, revocation and the required isolation from model shell/broker access.
