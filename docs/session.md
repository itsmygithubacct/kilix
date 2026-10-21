# Managed coding sessions in Kilix 0.2.2

`kilix session` and `kilix-session` launch Codex, Claude Code or Grok and retain
their run records. Native interactive UI in a fresh tab is the default. Every
new tab and grid pane uses `--keep-focus`: the current pane keeps focus. Panes
are held after exit so startup failures remain visible.

```sh
kilix session launch codex --cwd /path/to/worktree --trust-folder \
  --model gpt-6-astra --effort max --prompt-file /path/to/BRIEF.txt
kilix session launch claude --cwd /path/to/worktree --trust-folder --grid Reviews
kilix session launch grok --cwd /path/to/worktree --headless --background \
  --prompt-file /path/to/BRIEF.txt
kilix session launch codex --headless --foreground --prompt 'Review the local changes.'
kilix session providers
```

`kilix session codex ...` is shorthand for `kilix session launch codex ...`.
Clients must already be installed and authenticated. `providers` uses the same
PATH and vendor-prefix lookup as `kilix install` and `agent-control`; it starts
no client. Missing clients require an explicit `kilix install PROVIDER`.
Model and effort are forwarded exactly; omission leaves client defaults in
effect. The client determines which models and effort levels it accepts.
Existing worktrees are supported; the launcher does not create or switch them.

## Trusted folders and authorization

Use **`--trust-folder`** when the launch should answer the client's trusted-folder
question before startup. This invokes the existing `agent_trust` implementation:

| Client | Effect |
| --- | --- |
| Codex | Add the canonical cwd as a trusted project in `$CODEX_HOME/config.toml`, or `~/.codex/config.toml`. Existing explicit untrusted decisions and malformed settings are refused. Concurrent Kilix grants are serialized. |
| Claude | Set the project's `hasTrustDialogAccepted` in `$CLAUDE_CONFIG_DIR/.claude.json`, or `~/.claude.json`, under Claude's own lock. A missing config directory is created privately. |
| Grok | Pass the client's `--trust` flag, after checking that its repository-wide grant cannot exceed cwd. Use the Git/jj workspace root, or a directory outside a repository. |

The home directory, `/`, foreign-owned folders and symlinked/malformed trust
files are refused by the shared trust helper. Trust errors are recorded in
`run.json` and printed in the held pane or `runner.log`; the provider does not
start. A Grok receipt records a request to its own trust mechanism, rather
than claiming independent observation of its trust store. Login and other
onboarding dialogs remain owned by the client.

Without `--trust-folder`, the native client's folder-trust behavior remains in
effect. An interactive client can show its own question; focus its recorded pane
and answer there. Headless clients use their native policy (Claude's print mode
skips the interactive trust dialog); a successful launch receipt never means
trust or readiness was observed. Use the explicit flag when project settings
and instructions must be trusted before startup. Do not type a prompt into a
trust or login dialog through pane automation.

Folder trust **does not turn on permission bypass**. The launcher preserves the
client's configured approvals and sandbox. `--coding-yolo` adds the existing
client-specific bypass flag only when Kilix's shared coding-yolo setting is also
on. It never changes that setting, installs clients, or modifies model defaults.
`--dry-run` writes neither trust nor run state.

## Prompts and placement

`--prompt TEXT` is a literal string. `--prompt-file FILE` reads at most 1 MiB of
UTF-8, preserving CRLF, Unicode, final newlines and whitespace. `--prompt-file -`
explicitly reads stdin. Nothing is evaluated by a shell; implicit stdin is never
consumed. NUL and invalid UTF-8 are rejected before launching.

Headless Codex/Claude receive the exact bytes on stdin; Grok receives the saved
file through `--prompt-file --verbatim`. Native sessions receive a short, fixed
instruction to read the exact saved file. This avoids subcommand ambiguity,
multiline input into dialogs and operating-system argv limits. The receipt
distinguishes that bootstrap argv from the original prompt file and records
its hash. Passing the bootstrap is not evidence that the agent read the file.

Placement options are mutually exclusive:

| Option | Behavior |
| --- | --- |
| `--new-tab` (default) | Fresh tab anchored to the verified caller; preserve focus. |
| `--grid NAME` | Reuse this store's named grid in the caller's OS window, up to eight total panes per tab; create another tab when full. Preserve focus. |
| `--foreground` | Use the current terminal and wait for provider exit. Native UI requires terminal stdin/stdout and foreground job control. |
| `--background` | Detach a logged headless runner without a pane. Interactive mode is rejected. |

Pane placement uses the existing credentialed control route and source/broker
verification. There is no fallback to a focused, unrelated tab. Use
`--source-pane ID --expect-broker BROKER` only for an already identified caller
whose environment was stripped. Explicit identity must agree with any inherited
caller. No command closes panes or changes focus as rollback.

Grid allocations are serialized in the managed store. The marker includes the
store path and group name; matching a tab title alone never establishes ownership.
Manual panes count toward capacity. Tabs whose layout was manually changed are
left alone. Closed panes free capacity; held completed panes continue to count.
Use existing explicit pane commands if you want to close a retained pane.

## Inspection and lifecycle

Launch prints a JSON receipt with an exact `run_id`. Use that ID:

```sh
kilix session list
kilix session show RUN_ID
kilix session logs RUN_ID --stream events --follow
kilix session logs RUN_ID --stream stderr
kilix session logs RUN_ID --stream final
kilix session wait RUN_ID --timeout 600
kilix session stop RUN_ID --timeout 10
```

`show` includes the provider argv, worker argv, pane-launch argv when attempted,
placement receipt, requested settings, observed identities, process exit code
and status. `list` summarizes these without dumping prompt-bearing argv.
`logs` tails up to the last 1 MiB, by default 200 lines; streams also include
`prompt` and background `runner`. Native sessions retain their client history
and the existing `kilix transcript` terminal log, rather than a captured TUI.

`prepared`, `queued`, `starting` and `running` describe launcher/process state.
They do not assert an empty composer, login, requested model acceptance or task
completion. Headless Codex thread IDs and Claude init model/session IDs come
from known JSON events. Grok's generated session ID is only **requested**.
Interactive models and conversation IDs remain unobserved. `completed` means
the provider exited zero without a recognized error event; task acceptance
still requires checking the requested work.

Exit codes: foreground launch and `wait` return the provider/result code;
recognized error events produce failure even if the client exits zero.
`wait`/`stop` return 124 on observation timeout and leave the run active.
Signals produce `128 + signal` (normally 143 for stop). Inspection commands
return zero for readable records, including failed runs. A pane/background
submission can return before the worker starts; use `wait` for its outcome.

`stop` writes a private cancellation request. The owning worker forwards it to
its directly created provider process group, waits three seconds, then escalates
to KILL if necessary. Its unreaped group leader anchors cleanup; no PID read
from a receipt is signalled. Native UI runs in its own terminal foreground
group and terminal Ctrl-C goes to that UI. The terminal foreground group is
restored when it exits. The provider group is cleaned before the leader is
reaped, including descendants still in that group. Programs that deliberately
create separate sessions/groups are outside this boundary; this is not a
cgroup or application sandbox.

SIGKILL, machine failure or a dead runner may leave `status` incomplete.
`effective_status: orphaned` reports that the recorded runner identity is gone
(boot ID and process start time are checked). The manager then refuses to signal
saved PIDs. Inspect the receipt, transcript and client history before recovery.
A terminal launch timeout is `uncertain`, retains attempted argv, and is never
automatically retried. A cancellation of a queued run is applied if its worker
starts later. No global process/pane cleanup or automatic restart is performed.

## Explicit batches

`kilix session batch FILE` accepts a JSON array of argument arrays, each beginning
with a provider. Every entry is validated before the first submission:

```json
[
  ["codex", "--cwd", "/work/one", "--trust-folder", "--grid", "Reviews", "--prompt-file", "/briefs/one.txt"],
  ["claude", "--cwd", "/work/two", "--trust-folder", "--grid", "Reviews", "--prompt-file", "/briefs/two.txt"]
]
```

The default stagger is 30 seconds between submission starts; `--stagger 0`
disables it. `--dry-run` validates without waiting or writing. Relative paths
resolve against the invoking directory. Up to 100 entries and 1 MiB of batch
JSON are accepted. Foreground runs, shared stdin, nested batches and per-entry
root/dry-run flags are rejected. A submission failure stops later submissions,
retaining earlier runs and their receipts. The batch does not impose a global
worker limit or infer extra workers from a layout.

## Storage, library and installation

Default storage is **`~/.local/gpu_terminal/kilix/agent-runs`**. It follows
`GPU_TERMINAL_HOME` and `KILIX_STORAGE_HOME`; `KILIX_AGENT_RUNS_HOME` or `--root`
can select another absolute private directory. Existing `KILIX_SESSION_HOME`
terminal state is unchanged. Managed directories are 0700 and files 0600;
symlinks, foreign directories and public managed roots are refused. Metadata
uses the host's atomic, fsynced receipt writer. Prompts and logs can contain
private material. No automatic retention/deletion policy is imposed.

The receipt carries HOME, CODEX_HOME and CLAUDE_CONFIG_DIR path overrides to
the worker, so a long-running terminal server cannot select a different client
trust/auth directory from the invocation. Credentials and full environments are
never recorded; other settings and authentication remain client-owned.

Each run retains `run.json`, `launch.json`, exact `prompt.txt`, `worker.claim`
and `tmp/`. Headless runs add raw `events.jsonl`, `stderr.log` and, for Codex or
Claude, `final.txt` when supplied. Background runs add `runner.log`. Cancellation
adds `stop.json`. The exclusive worker claim prevents replay of an existing run.
Validation failures before preparation do not create run directories.

The importable **`config/kilix_session`** package belongs to the existing Kilix
host repository alongside `agent_control`, `agent_trust` and `agent_programs`.
Python 3.11+ on Linux is required; no new Python dependencies or separate repo
are needed. Add the selected host's `config/` to `PYTHONPATH`:

```python
from kilix_session import Launch, launch, inspect_run, wait, stop

receipt = launch(Launch("codex", cwd="/work/app", interactive=False,
                        placement="background", prompt="Review the current changes."))
result = wait(receipt["run_id"], timeout=600)
```

`plan(Launch(...))` is non-mutating; `list_runs()` and `inspect_run()` are read-only.
Foreground library launches run on the main thread and temporarily own signal
handling. Use background placement in a larger application. The implementation
reuses host path discovery, client lookup, trust, approval flags, private-directory
guards, durable JSON receipts and guarded pane launching. It uses ordinary process
groups for terminal clients; the private-X/application-bus supervisor is not
appropriate for a native coding TUI.

Run `python3 -B scripts/install-kilix-session.py [--prefix /absolute/prefix]` to
install the `bin/kilix-session` entry, or `kilix install kilix-session`. The default
prefix is `~/.local` (`KILIX_SESSION_PREFIX` can override it). The executable
points back to this selected host source, so it follows host updates. Reinstall
when moving the source. An unrelated existing entry is preserved and reported.
`python3 -m kilix_session` and the source-tree `./kilix-session` also work.

The existing `install-kilix-tui-utils.sh` command-installation path now installs
this host entry into the same prefix. Pleb install/update and the coordinated
Plebian-OS provisioning path already call that installer. The host source pin
therefore carries the implementation and entry-point wiring together. Release
integration should select the reviewed host commit and verify the installed
entry in the image/upgrade gates; this change does not move release pins itself.

The external `agent-session` launcher informed the features, but is not imported
or required. Deliberate differences: native UI/fresh tab defaults, opt-in grids,
explicit stdin, managed Kilix storage, trusted-folder setup, no unconditional
permission bypass, fixed file bootstrap for native prompts, guarded caller
identity, focus preservation, and durable cancellation/inspection. Resume,
message delivery, client history and terminal transcripts continue to use the
existing `agent-control`, rollout and transcript interfaces.

## Verification

`python3 -B -m unittest discover -s tests -p test_kilix_session.py -v` uses fake
provider executables, real subprocesses/PTYs and an in-memory terminal. It covers
literal prompts, native job control, trust, concurrent grants, cancellation,
failure receipts, duplicate workers, grids, focus, batch staggering and install
entries. It opens no real model sessions or Kilix panes. Current installed client
help was consulted for argv spelling; provider authentication, real model
acceptance and image acceptance remain separate integration checks.
