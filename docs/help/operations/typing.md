# Kilix 0.2.2: Sending text to one pane

For authenticated send-text, match exactly env:KITTY_PTY_BROKER_SESSION= followed by the target pane's broker session ID. Resolve the target broker session from that pane's env object in an authenticated raw ls listing.

Supply the existing credential with `--password-file "$KILIX_RC_PASSWORD_FILE"` when using the authenticated kitten remote-control path. Use `send-text --match "env:KITTY_PTY_BROKER_SESSION=$SESS" --stdin` after resolving SESS to send standard input to that pane.

The authenticated byte-input policy limits each decoded payload to 1024 bytes; split larger UTF-8 text at character boundaries or let the target read a file. For the authenticated send-text checker, bracketed paste must be unset or disable.

Text without a submission byte is placed in the target input buffer; it does not automatically press Enter. After sending, read the target pane back; send-text can exit successfully even when the terminal silently refused the request.
