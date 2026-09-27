# Kilix 0.2.2 release status and versions

Kilix 0.2.2 is a coordinated Plebian-OS release candidate. Passing source tests
does not establish acceptance of an install image or an upgrade. Image,
upgrade and final human acceptance remain pending in this source revision.
The supported upgrade source is 0.2.1. Systems on older releases need the
fresh-install baseline; a direct skip from an earlier release is unsupported.

Run `kilix --kilix-version` to print the wrapper's coordinated release version.
Plain `kilix --version` reaches the Kitty engine and is not the wrapper version.
Run `kilix status` for the source version and commit, selected engine, writable
configuration and provider information.

In 0.2.2, the three-line Start button and status widgets open floating popups
above the panes. Panes retain their size and continue updating. Popups adapt
to the page-strip position and available window space. An outside click
dismisses a popup without activating the pane beneath it.

The presence of a model or application in a catalog does not establish that
its payload is installed, its provider is ready, or it is release-qualified.
