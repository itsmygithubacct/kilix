# Kilix 0.2.2: Creating panes

Run `kilix new-pane right` to split to the right; right is also the default when direction is omitted. Run `kilix new-pane down` to place a new pane below the caller.

Run `kilix new-pane left` to create a pane on the caller's left. Run `kilix new-pane up` to place the new pane above the caller.

The split is anchored to the calling pane, even when another pane currently has focus. Place --cwd before the direction: `kilix new-pane --cwd /tmp right` opens a right-hand pane in /tmp.

Run `kilix new-pane down -- htop` to launch htop in a pane below the caller. `kilix split` is an alias for `kilix new-pane`.
