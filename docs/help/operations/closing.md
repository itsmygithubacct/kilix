# Kilix 0.2.2: Closing targets deliberately

To terminate pane 74 through remote control, use `kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" close-window --match id:74`. The fork's remote close-window path explicitly asks the broker to terminate a persistent pane; it is a destructive action.

If the broker does not acknowledge remote pane termination, the fork refuses to close that pane and leaves it attached. Use `kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" close-tab --match id:37` to close page 37.

Closing a page normally detaches its broker-backed clients; explicit per-pane termination is a separate action. Check foreground_processes in the raw listing before closing a pane you did not create.

Exclude the caller's KITTY_WINDOW_ID when selecting other panes for cleanup. There is no basic kilix close verb in this source revision; use the documented remote-control close commands.
