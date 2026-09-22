# Kilix 0.2.2: Remote-control authorization

KITTY_LISTEN_ON identifies the remote-control socket for the live terminal instance. KILIX_RC_PASSWORD_FILE identifies the credential file used by scoped authenticated remote-control operations.

The credential file should be owned by the current user, have mode 0600, and not be a symlink. An authenticated command outside the allowlist is refused; possessing the credential does not grant every remote-control command.

Authenticated launch, ls, focus-window, focus-tab and get-text are ordinary allowed operations. The uncredentialed send-text path is restricted to the sender's own OS window; it refuses cross-window or broadcast requests.

An empty recovered socket list means no usable target was found; multiple sockets require identifying the intended instance instead of choosing the first. Use the kitten client belonging to the running terminal engine when recovering a stripped environment.
