# Kilix 0.2.2: Finding tabs and panes

Run `kilix ls` to list live pages and their tab IDs. Run `kilix ls --panes` to list individual panes and their pane IDs.

The ACT marker identifies the tab or pane that currently has focus. PANE_ID identifies an individual terminal; TAB_ID identifies its containing page.

The pane listing includes its title, foreground process name and working directory to help distinguish similar panes. KITTY_WINDOW_ID is the calling pane's own ID, not the ID of whichever pane has focus.

For raw structured state, run `kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" ls`. Raw listing JSON contains OS windows, their tabs, and each tab's windows; the innermost windows are panes.
