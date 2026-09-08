# Split-tree layouts

Choose proportions for usable editor/output widths, terminal size, and roles,
not just equal pane counts. Prefer fewer readable panes to many narrow ones.
The helper reports each pane's lines, columns, layout, and neighbors for checks.

Start with pane A in a new tab. Each step below is `agent-control split` with
the current exact anchor ID and broker. Directions refer to the NEW pane.

| Desired layout | Operations after creating A |
| --- | --- |
| Two equal columns | Split A right, bias 50, creating B. |
| Two equal rows | Split A down, bias 50, creating B. |
| Main pane left, two smaller panes right | Split A right, bias 40, creating B; split B down, bias 50, creating C. |
| Four-pane 2×2 grid | Split A right, bias 50, creating B; split A down, bias 50, creating C; split B down, bias 50, creating D. |
| Three equal columns | Split A right, bias approximately 66.67, creating B; split B right, bias 50, creating C. |

For other geometries, recursively divide rectangles. Split the anchor that
occupies the intended rectangle, allocating the requested percentage to its
new right/down/left/up child. Rounding to terminal cells makes pixel-exact
ratios impossible; inspect the resulting dimensions and explain constraints.

All multi-pane recipes require the new tab's `splits` layout and the installed
engine's explicit `--next-to`, location, and bias support. Do not enable new
remote-control permissions or rearrange another tab to force a layout. If the
new tab inherited another layout, report the constraint and obtain the needed
layout choice through the supported UI before continuing.

An existing pane has a broker identity; neighbor entries may be window-group
IDs, not pane IDs. Do not use a raw neighbor number as a pane target. Prefer
the pane IDs returned by your own launches and verify them in a fresh listing.
