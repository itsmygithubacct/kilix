# Kilix 0.2.3 release status and versions

Kilix 0.2.3 is part of the coordinated Plebian-OS 0.2.3 release. The supported
upgrade source is 0.2.2. Systems on older releases upgrade one published
release at a time; a direct skip from an earlier release is unsupported. The
Plebian-OS 0.2.3 release notes record the qualification and the known issues.

To upgrade from 0.2.2, run `pleb update --to 0.2.3 --dry-run`, then
`pleb update --to 0.2.3`. A machine on a 0.2.3 release candidate (RC1–RC9)
already reports version 0.2.3. Move it to the final once with
`pleb update --to 0.2.3`, not `pleb update --latest`. `pleb update --rollback`
restores the previous release.

Run `kilix --kilix-version` to print the wrapper's coordinated release version.
Plain `kilix --version` reaches the Kitty engine and is not the wrapper version.
Run `kilix status` for the source version and commit, selected engine, writable
configuration and provider information.

New in 0.2.3:
- The first desktop login asks which desktop to use. `kilix default-desktop
  choose` asks again.
- On a fresh installation, the first login then asks once whether to turn on
  an SSH server. The default is No. Change it later with
  `sudo plebian-os-ssh-server enable` or `disable`, and check it with
  `plebian-os-ssh-server status`. Enable refuses while any account still has
  the 0.2.0 starter password and names those accounts: change your own with
  `passwd` and another with `sudo passwd NAME`, then run it again.
- Kilix Ask answers with its grammar until you install the LFM2 command model.
  The first interactive `kilix ask` and `kilix wizard` offer the download.
- Paradee reads a long text in full.

Since 0.2.2, the three-line Start button and status widgets open floating
popups above the panes. Panes keep their size and continue updating. Popups
adapt to the page-strip position and available window space. An outside click
dismisses a popup without activating the pane beneath it.

The presence of a model or application in a catalog does not establish that
its payload is installed, its provider is ready, or it is release-qualified.
