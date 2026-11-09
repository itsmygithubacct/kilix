# Pages (Tilix sessions)

Tilix groups panes into **sessions**; kilix maps each session to a kitty **tab** —
a "page" you flip between. In 0.1.9 the page strip (kitty's powerline tab bar)
is always visible across the top and ends with a clickable **`+`** to open a new
page. The 0.2.0 candidate can place it at the top or bottom as described in
[Kilix Start menu and page-strip placement](release-notes.md#kilix-start-menu-and-page-strip-placement).
You can
**drag a tab to reorder** it, press **`F12`** for a visual page chooser (kilix's
stand-in for Tilix's session sidebar), and **`F2`** to rename the current page.
In 0.2.2, the optional three-line Start button at the far left toggles the hierarchical
Kilix Start menu; `Ctrl+Alt+M` provides the same action without a mouse.
Run `kilix ls` from inside kilix to list the live pages, their tab IDs, pane
counts, titles, and current working directories. The page
shortcuts are in [Keybindings](keybindings.md#keybindings-tilix-layout).

## Live tab and pane control

```bash
kilix ls                  # tabs/pages
kilix ls --panes          # individual pane IDs
kilix focus 45            # focus tab or pane 45
kilix focus pane:74       # disambiguate when needed
kilix watch 74            # poll pane 74 as read-only text
kilix watch --once 74     # one snapshot
kilix new-pane            # open a pane to the right of this one
kilix new-pane left       # ...or to its left; also `right`, `down`
kilix new-pane down -- htop        # run something in it
kilix new-tab --title notes        # open a new page
kilix switch              # the page/pane switcher (same as F12)
```

`kilix new-pane` places the pane relative to **the pane the command runs in**,
not to whichever pane currently has focus, so it does the same thing from a
background pane as from the foreground one.

All four directions are exact, which needed a change in the fork. The splits
layout could always put a window on either side of either axis, but upstream
kitty named only three of the four placements: `vsplit` and `hsplit` are always
the far side of their axis, and `before` is the near side of whichever axis the
*layout* defaults to. The near side of a chosen axis was reachable only by
splitting the other way and then using `move_window` to swap — which works from
a keybinding and nowhere else, so remote control could not do it at all. The
fork adds `vsplit-before` and `hsplit-before` for those two placements; the
pane-title bar's own left and up split buttons use them now instead of the swap.

These commands use kitty remote control against the current live GUI instance.
`kilix focus` can jump to a tab or pane; `kilix watch` is intentionally
read-only and polls `kitten @ get-text`, so it is useful for shell output and
simple full-screen programs but is not real multiplexing. It does not carry
graphics, mouse state, or a second interactive PTY. For true attach/view, start
the session under tmux with `kilix serve` or `kilix mux <name>`.
