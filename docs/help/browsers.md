# Kilix 0.2.2 contained browsers

Run `kilix run chromium` to launch a contained browser in a new tab.
Contained Chromium and Firefox launches normally get a private disposable
profile. This isolates them from an existing native browser, but each launch
starts with a fresh login.

To retain a browser session, set `KILIX_RUN_BROWSER_PROFILE` to a profile
directory in your writable `kilix.env`. Kilix creates an owned profile with
mode 0700 or makes an existing user-owned profile private. Symlinks and
unsafe writable or foreign-owned ancestors are refused. Existing parent
permissions are not changed to make an unsafe path acceptable.

Every launch using that persistent path shares one browser profile. A second
Chromium launch joins the first; a second Firefox launch refuses. An explicit
Chromium `--user-data-dir` or Firefox `--profile` overrides the configured
default for that launch. Explicit profiles are never replaced or deleted.

Modifier keys travel with their keyboard or mouse gestures. Modifier state
is released on focus-out and exit so changing panes does not leave Alt stuck
in the contained app. `Ctrl+Q` quits the contained application. With no
`--size`, the contained display follows pane dimensions; `--size 640x400`
fixes its resolution and scales the picture as the pane changes size.
