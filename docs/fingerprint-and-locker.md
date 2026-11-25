# Fingerprint reader and locker

## Fingerprint reader

`kilix fingerlock` is the host verb for the machine's USB fingerprint reader.
With no arguments it finds the reader, installs Debian `fprintd` the first
time it is asked to, and reports what is enrolled:

```sh
kilix fingerlock                  # find the reader; install fprintd on first use
kilix fingerlock enroll           # enroll the right index finger (or name one)
kilix fingerlock verify           # touch the sensor; exit 0 on a match, 8 on none
kilix fingerlock list             # enrolled fingers
kilix fingerlock delete --all     # forget them
kilix fingerlock doctor --json    # every check, as one kilix.fingerlock/v1 object
```

Discovery is read-only: the verb reads sysfs and asks the kernel whether the
USB node is openable, and never opens it. Support is decided by libfprint's
own hardware database once it is installed.

The lazy install is one fixed command, `apt-get install -y
--no-install-recommends fprintd`, run as root through `sudo` in your terminal
(or in `xterm` when the caller has no terminal) after you say yes, or at once
with `--yes`. Plebian OS points apt at a dated snapshot, so this installs the
release's versions. No repository is added and no recommends are pulled.
`libpam-fprintd` is not installed, and login is left unchanged.

When only one finger is enrolled, default verification selects it explicitly.
With several enrolled fingers, fprintd handles the automatic selection.
Use `verify right-index-finger` or a locker command's `--finger right-index-finger`
option to select a particular finger.

Everything after installation goes through fprintd over the system D-Bus,
behind polkit. Verifying is allowed from an active seat session. Enrolling and
deleting also need your password through a polkit authentication agent, and a
bare terminal session may not have one: `--tty-agent` runs `pkttyagent` for
the duration of the command. A successful `verify` checks the reader. It is
not a screen-lock token and it does not unlock a session. Exit statuses are
stable and documented at the top of `config/fingerlock.py`. Use `--redact`
before sharing output: it removes the USB serial, port, and device node.

## Locking files and passwords

```sh
kilix fingerlock init                         # create a passphrase-protected locker
kilix fingerlock lock holiday.jpg             # publish holiday.jpg.flk, then unlink the source
kilix fingerlock unlock holiday.jpg.flk       # authenticate the whole container before output
kilix fingerlock password set wifi            # store an encrypted named secret
kilix fingerlock password get wifi            # print that secret
kilix fingerlock forget                       # discard the agent's cached custody
```

`lock` verifies the recipient public key against the passphrase-derived key
or against the root agent's custody before sealing. An existing destination,
a symlink, or a changed source is refused. Use `--keep` to retain the source.
Unlink removes a directory entry; it is not secure deletion, and other hard
links and storage remnants can remain. Password replacement publishes the
new ciphertext atomically and preserves the previous entry if writing fails.

The locker wraps a random X25519 seed under an Argon2id passphrase key.
Each container uses a random file key and authenticated XChaCha20-Poly1305
chunks. Unlock authenticates the complete container before publishing a file
or writing plaintext to stdout. `passwd` rewraps the same seed; existing
containers stay readable and the underlying key does not rotate.

Without an agent, use the passphrase. The optional root custodian keeps the
master key in memory and performs bounded decrypt operations. It returns
plaintext for the approved container and never exports the master key.
Each fingerprint operation first displays a fresh polkit approval containing
the requesting process, requested name, byte count and container SHA-256.
The custodian asks polkit over D-Bus, so these details are not on any command
line that other users can read. The command prints the full SHA-256 to compare
with the dialog. Polkit can require the account password; approval is not
cached. After approval,
touch the sensor; a touch before approval is not read. One approval allows up
to three scans within one 45-second deadline: a rejected touch re-arms the
reader, as fprintd's own PAM module does, and three rejections end the
operation with no match. Cancelling the dialog, expiry, forget, caller exit, a
session change or a locked seat refuses the operation. Plaintext already
delivered remains with its recipient.

Custody belongs to the logind session that deposited the key and is used only
from it. Locking that session, switching away from it, logging out, or logind
being unable to report it forgets the key, normally within a second and within
a few seconds if logind stops answering; unlocking again does not restore it.
A lock is seen when logind is asked to lock the session (`loginctl
lock-session` and its Lock signal, which forget at once) or when the screen
locker sets logind's `LockedHint`. A locker started some other way that does
not set the hint is not seen; Pleb's `pleb-lock` sets the hint itself when no
supervised locker does.

Suspend, hibernate and shutdown forget the key before they happen. The
custodian holds a logind delay inhibitor; when logind announces the
transition, the custodian wipes the key and only then releases the inhibitor,
so the key is gone before memory is frozen or a hibernation image is written.
The inhibitor is taken again on resume, and a cancelled or failed transition
has forgotten the key all the same. As a backstop, any growth of the boot-time
clock past the monotonic clock, which happens only while the system sleeps,
also forgets it.

Custody is accepted and used only while the custodian is watching: its session
monitor runs and its sleep inhibitor is held. If logind cannot be watched, the
monitor stops or a session check fails, the key is forgotten and deposits and
operations are refused until watching resumes. `status` shows this under
`watch` and reports why the last key was dropped. An approval or scan in
progress for a forgotten key stops.

The custodian never saves custody to a file, so a restart or reboot holds
nothing. The custody buffer is locked in memory when the kernel allows it, but
other copies made while parsing the deposit are not, and they can reach swap
under memory pressure. Swap is not a secrecy boundary unless it is encrypted.

Agent installation is explicit:

```sh
kilix fingerlock install --locker              # fprintd and python3-nacl dependencies
kilix fingerlock install --agent               # review package digest; authorize root installation
kilix fingerlock install --agent --rollback    # restore the previous verified agent version
```

The agent also requires python3-dbus, python3-gi, polkit, systemd/logind and a
kernel supporting `SO_PEERPIDFD`. Its installer checks Python dependencies
and does not run apt. The root interpreter verifies the confirmed package
SHA-256 before executing package code. Code and unit files are stored as a
complete version under `/usr/local/lib/kilix-fingerlock/versions/`. A single
per-user `current` link selects the version. Each user has a mode-0600 socket
under `/run/kilix-fingerlock/`, owned by that user; the service runs as root
and verifies both the caller UID and live logind session.

Activation stops the old units, switches the version, reloads systemd and
checks the new custodian. Failures restore the previous version and its
activation state. An interrupted transaction leaves a journal that the next
install or rollback recovers first. Verified version directories are retained
for rollback. Installation and rollback discard cached keys; the next unlock
requires the locker passphrase again. Existing unmanaged units, modified
stored code, different polkit policy, and systemd drop-ins require review.

Root, the kernel, and processes able to read agent memory are outside this
boundary. Custody uses mutable buffers, attempts mlock, disables core dumps
and reports hardening failures, but Python and cryptographic libraries may
retain other copies. Swap and hibernation secrecy are not guaranteed. The
passphrase protects the wrapped seed on disk; use a strong passphrase.
