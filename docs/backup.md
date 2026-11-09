# Back up and restore settings and documents

```
kilix backup create                 # ~/kilix-backups/kilix-backup-<time>.tar.gz (private, 0600)
kilix backup list ARCHIVE           # what a restore would create or replace
kilix backup restore ARCHIVE --yes  # restore; your current files are backed up first
```

A backup holds the shared settings file, your `kilix.env`, the desktop's
configuration and saved state, and the documents on the desktop. Restoring only
creates or replaces the files the archive names, and first keeps a copy of each
file it replaces. It refuses the whole archive if any member is a link, escapes
its folder, or fails its checksum. `list` and `restore` show every setting the
archive would change, and flag every `kilix.env` key that can change what Kilix
runs or downloads (all but a few display settings), because a backup file may
come from somewhere else. A file whose name a backup cannot hold, such as one
with a backslash in it, is left out and named when the backup is made. From the command line,
restore refuses while the desktop is running, since the desktop would save its
own state over the restored files. Control Panel > Backup in Kilix 95 restores
and restarts the desktop for you. A restored `kilix.env` takes effect only in a
new Kilix session (log out and back in); restarting the desktop is not enough.
