# Kilix 0.2.2 pages and panes

A page is a Kitty tab containing one or more terminal panes. Run `kilix ls`
to list pages and `kilix ls --panes` to list individual pane IDs. Run these
commands inside the live Kilix instance you want to inspect.

Run `kilix new-pane right` to split to the right of the calling pane.
The other directions are `left`, `up` and `down`. The split is anchored to
the calling pane even if another pane has focus. For example,
`kilix new-pane down -- htop` starts htop below it. A command pane closes
when its command exits. Run `kilix new-tab --title notes` to open a new page.

Run `kilix focus pane:74` to focus pane 74, or `kilix focus tab:45` for tab 45.
Use explicit kinds in scripts: an ID matching both a tab and a pane is
ambiguous and is rejected. Replace example IDs with IDs from your live listing.

Run `kilix watch --once 74` for a read-only text snapshot of pane 74.
Watching does not carry graphics, mouse state or an interactive PTY.
Use `kilix switch` or F12 for the page/pane switcher. F2 renames the current
page; `Ctrl+Shift+T` opens a new page. `Ctrl+Alt+R` splits right and
`Ctrl+Alt+D` splits down. `Alt+Arrows` changes pane focus.
