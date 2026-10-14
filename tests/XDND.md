# Native private-display drag/drop validation

`xdnd_native_workflow.py` launches the real paired engine and Apprun with
independent GTK3 processes on distinct authenticated owned Xvfb displays. It
uses physical XTest pointer gestures and normal GTK drag-data-received callbacks.
The engine remote-control socket is private and used only for geometry and
window setup. Shell checks inspect the actual Bash readline buffer without
Enter, eval or executing the payload. Fixture file names and data are synthetic.

Prerequisites are built paired source clones, Python Xlib/PyGObject GTK3, Xvfb,
xauth, xdotool, xclip and dbus-run-session. The native frame presenter must be
materialized at Kilix's existing gitlink. No global dependency installation is
part of the test. Run in an empty inherited environment with private HOME,
XDG/config/cache/data/state/runtime, TMPDIR and Kilix stores, using the handoff's
journal-denial Landlock wrapper. From the handoff root:

```sh
env -i /usr/bin/python3 validation/run.py /usr/bin/python3 source/kilix/tests/xdnd_native_workflow.py --case text
env -i /usr/bin/python3 validation/run.py /usr/bin/python3 validation/final_matrix.py
```

`--engine`, `--kilix`, and `--evidence` can select explicit owned source pairs
and artifact directories. Each run writes callback JSONL, engine/Apprun logs,
result JSON, geometry, payload digest, timing and PID/start/pidfd ownership.
Processes are cleaned up by exact owned identities; displays and buses are never
borrowed from the inherited desktop.

The registered cases are text, large (850,000 bytes through INCR), uri,
private-shell, physical-private, physical-shell, move, physical-move,
modifier-copy (stationary action change), move-refuse, refuse, refuse-data, link, malformed-offer, cancel, timeout, stale-finished,
cross-pane, cross-window, resize, display-resize, target-exit, target-replaced,
source-exit, lost-selection, shell-malformed, shell-encoded-lf,
shell-encoded-cr, shell-encoded-tab, input-after-cancel and clipboard.
`input-baseline` probes ordinary input without a drag for baseline comparison.
The input case disables the engine's Ctrl+Shift+U shortcut in its private test
configuration so GTK receives its own Unicode composition chord, then verifies
selection and scrolling. This does not modify product defaults.

The ordinary shell URI positives contain spaces, Unicode, punctuation, percent
and backslash in local paths. The encoded LF/CR/TAB cases mix a valid URI with a
decoded control path: the complete list must be refused and the input buffer
must remain unchanged. These successor scenarios are authored and unexecuted;
they remain native I4d requirements. The selected repository test uses POSIX
word parsing only as a source oracle, without launching a shell.

`test_native_xdnd.py` exercises parser, coordinate, capability, ownership,
peer/action, transfer budget, complete-data, INCR, timeout and MOVE guards.
The handoff's `validation/mutations.py` replaces operative SDK guards in memory;
`engine_mutations.py` and `engine_peer_mutation.py` compile native C mutants,
exercise real GTK workflows, then restore and rebuild the original source.

Installed default-desktop, Wayland-host and hardware acceptance and independent
high review remain the integration supervisor's work. Supported limitations
are documented in the repository README and engine protocol documentation.
