# Kilix 0.2.2: Reading live terminal text

Run `kilix watch 74 --once` to read pane 74 once and then exit. Run `kilix watch 74 --once --extent all` to include scrollback in the snapshot.

Run `kilix watch 74 --once --plain` to read a snapshot without ANSI styling. Run `kilix watch 74 --interval 2` to keep watching at a two-second polling interval.

The default watch extent is screen; use --extent all when you need scrollback. Watch is read-only text polling; it does not attach an interactive PTY or transfer graphics and mouse state.

Redirect `kilix watch 74 --once --extent all --plain` into an explicitly chosen file to save a text snapshot. The watch interval must be greater than zero; zero and negative intervals are rejected.
