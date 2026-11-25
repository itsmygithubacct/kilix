# Coding agents

The skill commands and discovery roots are in the README's [Optional coding-agent skills](../README.md#optional-coding-agent-skills) section. This page keeps the rest of that section and the Claudex notes. Managed coding runs are described in [session.md](session.md).

## Skill registration, ownership and removal

Codex and Kimi share the default three registrations. Installing for the second
agent records another owner without adding duplicate directories. Removing
one owner keeps the set available for the other. Because the directory is
shared, either agent can discover an installed set even before it is recorded
as an additional owner; `status` reports this as `shared`.
The `--agent` choice tracks registration/removal ownership, not isolation from
the other client; both automatically discover the shared `.agents` root.

The installer creates three symlinks to a private version snapshot through one
atomic version pointer in the sibling `.kilix-skill-bundles` directory. It checks
recorded file bytes, modes and registration inodes before changing an existing
set. Existing user-owned agent directories may retain their normal `0775` mode;
only the new snapshot store requires `0700`. User files, foreign or replaced
symlinks, edited skills, extra files in an installed skill and world-writable
or foreign unsafe directory ancestors cause refusal. It does not
chmod existing directories, edit agent configuration, or enable remote control.
Conflicts require the user to preserve/reconcile their own entries before retrying;
there is no force-overwrite option. Checks cover the named skills in the selected
and documented alternate user roots, not arbitrary project or plugin registries.

Removal unregisters the last owner's three links. Private version snapshots
and interrupted-operation recovery data remain outside discovery; automatic
recursive deletion could erase later user additions. No background cleanup runs.
An interrupted partial first install/removal is reported as a conflict for
manual reconciliation. A concurrent update switches the common version pointer
once the complete new set is ready; readers already opening old files can finish
against the retained snapshot. Installation status describes files and ownership,
not proof that a running agent has reloaded them. Refresh/restart that agent if
its skill list has not changed.

The tracked `skills/` files travel with the full source clone, Git source archive
and normal source update. The Kitty fallback binary download is separate;
it is not a Kilix source installation. `kilix agent-control` dispatches to the
bundled control helper and the new source-only commands work before submodule
or terminal-engine initialization.

## Claudex and rollout logs

`kilix install kilix-claudex` installs the public, MIT-licensed application at
its catalog-pinned revision. It uses Python's standard library and performs no
account login or agent launch during installation. `kilix claudex --help`
works before installation; `kilix claudex` forwards commands to an explicit
PATH installation, the verified catalog copy, or the existing local fallback.
For example, `kilix claudex team plan team.json --json` previews a team before
`kilix claudex team launch team.json` launches it. The application retains its
private configuration independently of the installed source.

The selected toolkit includes `kilix rollout log --pane N [--json]` for locating
the log written by a pane's current agent. See `kilix rollout log --help` for
selection and exit statuses.
