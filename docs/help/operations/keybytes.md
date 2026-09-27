# Kilix 0.2.2: Submission bytes and remote key limits

The authenticated policy does not allow arbitrary send-key; send-text sends bytes rather than abstract key events. A carriage-return byte can submit input to a terminal application that accepts it as Enter; verify the target program's behavior.

In an ordinary shell, a trailing line-feed byte can submit a command, but that behavior should not be assumed for every full-screen application. Plain text delivery and command submission are separate operations; read back after the intended submission.

The authenticated byte-input checker rejects --match-tab, --all, --exclude-active and session_id targeting on its scoped send-text route. The byte-input match must contain a broker session ID of 16 through 64 lowercase hexadecimal characters.

Sending text into an editor, agent or password prompt is not equivalent to running a shell command. Use the existing scoped credential and supported operations; weakening the remote-control policy is not required to read or send bounded pane text.
