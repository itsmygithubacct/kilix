# Kilix 0.2.2: Focusing a specific target

Run `kilix focus pane:74` to focus pane 74. Run `kilix focus tab:37` to focus page 37.

A bare ID matching both a tab and a pane is rejected as ambiguous; qualify it with pane: or tab:. The prefixes window: and win: are aliases for pane: in the basic remote-control verbs.

The prefixes page: and session: are aliases for tab: in the basic remote-control verbs. Use a PANE_ID, not a TAB_ID, with kilix watch; watch rejects a target resolved as a tab.

If a target ID is no longer live, list the panes again with `kilix ls --panes` and choose a current ID. Numeric listing positions are not persistent pane IDs; use the PANE_ID or TAB_ID column when targeting.
