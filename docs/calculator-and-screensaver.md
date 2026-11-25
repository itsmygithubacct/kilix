# Calculator and screensaver

## Calculator

```bash
kilix calculator
```

The calculator opens over the current terminal session using the same native
overlay theme as the Copy/Paste context window. Every button is clickable;
digits, decimal point, arithmetic operators, percent and `=` also work from the
keyboard, `Enter` calculates, `Backspace` edits, `C` clears, and `Esc` closes
the overlay. The calculator includes memory controls, square root, reciprocal,
sign change, clear-entry and divide-by-zero handling. It adapts when the pane
is resized and reports the minimum usable size instead of drawing a clipped
keypad.

## Screensaver

```bash
kilix screensaver            # matrix digital-rain (the default)
kilix screensaver matrix     # …or by name
```

Terminal screensavers live in `config/screensavers/` as small, self-contained
C programs. kilix compiles the one you ask for on first use (cached under
`~/.local/gpu_terminal/kilix/cache/screensavers`) and runs it in the current pane — press `q` or `Ctrl-C`
to quit. `matrix` is efficient green digital-rain: diff-rendered with one
synchronized write per frame, so it's a couple of percent of a core even
full-screen. Drop another `<name>.c` into that directory and
`kilix screensaver <name>` picks it up. Needs a C compiler (the same one the
fork build uses).
