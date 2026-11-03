# Kilix 0.2.2 release status and versions

Kilix 0.2.2 shipped with the coordinated Plebian-OS Linux release on
November 3, 2026. The immutable `v0.2.2` tag identifies the released source;
the development branch can contain later changes.

Linux installation and lifecycle qualification covers QEMU/KVM BIOS and EFI
virtual machines. Bare-metal USB installation and physical GPU operation were
not qualified. Windows remains at `windows-v0.2.2-preview.4`; ARM64 is not a
supported OS release target. See the [published release notes](https://github.com/itsmygithubacct/plebian-os/releases/tag/v0.2.2)
for known issues and deferred feature qualification.

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
