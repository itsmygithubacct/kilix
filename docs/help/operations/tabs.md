# Kilix 0.2.2: Creating pages and tabs

Run `kilix new-tab` to open a new page containing a shell. Run `kilix new-tab --title build` to create a page named build.

Run `kilix new-tab --cwd /tmp` to open a page whose shell starts in /tmp. Without --cwd, a new page follows the calling pane's working directory.

Run `kilix new-tab --title tests -- make test` to execute make test on a named page. `kilix new-page` is an alias for `kilix new-tab`.

Press Ctrl+Shift+T to create a new page using the keyboard. In Kilix, a page is a Kitty tab; a pane is one terminal inside that page.
