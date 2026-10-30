# Managed coding sessions in Kilix 0.2.2

`kilix session` and `kilix-session` launch Codex, Claude Code or Grok and retain
their run records. Native interactive UI in a pane of a session grid is the
default (see [Session grids](#session-grids)). Every new tab and grid pane uses
`--keep-focus`: the current pane keeps focus. Panes are held after exit so
startup failures remain visible.

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
| (none, default) | Join the `agent-sessions` grid, or a fresh tab when the `KILIX_CODING_SESSION_GRID` setting is `off`. |
| `--new-tab` | Fresh tab of its own anchored to the verified caller; preserve focus. |
| `--grid NAME` | Join the newest grid named NAME with room in the caller's OS window, up to twelve panes per tab; create another tab when full. Preserve focus. |
| `--foreground` | Use the current terminal and wait for provider exit. Native UI requires terminal stdin/stdout and foreground job control. |
| `--background` | Detach a logged headless runner without a pane. Interactive mode is rejected. |

Pane placement uses the existing credentialed control route and source/broker
verification. There is no fallback to a focused, unrelated tab. Use
`--source-pane ID --expect-broker BROKER` only for an already identified caller
whose environment was stripped. Explicit identity must agree with any inherited
caller. No command closes panes or changes focus as rollback.

## Session grids

A session grid is a tab in the engine's `sessions` layout. Columns grow to
four before a new row. One pane fills the tab. Two, three and four panes are
one row of full-height columns. The fifth pane starts a second row under the
first. Panes fill left to right, and a new pane does not move an earlier one.
The ninth pane starts a third row, so twelve panes are four columns by three
rows. The next launch opens another tab named `NAME 2`, `NAME 3`, and so on.
Closing a pane lays the rest out again with no gap.

```text
n=4:  ┌───┬───┬───┬───┐      n=6:  ┌───┬───┬───┬───┐
      │ 1 │ 2 │ 3 │ 4 │            │ 1 │ 2 │ 3 │ 4 │
      └───┴───┴───┴───┘            ├───┼───┤   │   │
                                   │ 5 │ 6 │   │   │
                                   └───┴───┴───┴───┘
```

These coding-session launches into a new tab join a grid unless told otherwise:
`kilix session` without a placement option, `kilix agent-control new-tab`, the
structured `agent.launch` action, the structured `pane.open` action into a new
tab, and `kilix new-tab` or `kilix tab new`, when they run a coding client
(codex, claude, grok, ...). These use the `agent-sessions` grid. Pass
`--own-tab` to `agent-control new-tab`, `kilix new-tab` or `kilix tab new` (or
`--new-tab` to `kilix session`) for a tab of its own.

A command runs a coding client only when the client is in an executable
position: the program itself (`codex --yolo`, `/opt/bin/claude`); the program an
`env` wrapper runs, after its options and `NAME=VALUE` assignments (`env
TEST=1 codex`); or the first command word of a shell line, after assignments,
`exec` and `env` (`kilix tab new -- sh -c 'A=1 exec claude'`, `sh -c 'codex'`). A
client's name as an argument does not count: `printf '%s\n' codex` and `echo
claude` open ordinary tabs, and so does `cd src && codex`, whose first command
is `cd` (use `kilix new-tab --cwd src codex`). An `env` option this does not
know, such as `-S`, is not followed.

Deliberate limits: these launches are not intercepted and open as they always
did. A script or wrapper that starts a client itself (`./run-agent.sh`), a
client typed into a shell after its tab opened, a raw `kilix @ launch`, and the
SDK's low-level `panes.new_tab`. Use `kilix session grid add` for those.

`KILIX_CODING_SESSION_GRID=off` (Settings, "Coding sessions in a grid") restores
one tab per session everywhere; it is on by default. Set in the environment it
wins over the Settings file, for this setting only, so one shell or script can
opt out (or back in). `kilix session grid enabled` prints the effective value,
`on` (exit 0) or `off` (exit 1); scripts that default to a grid check it. An
explicit `kilix session grid add` works whatever the setting.

`kilix session grid` places any command, not only a managed run:

```sh
kilix session grid add --name claudex-team --new --title orchestrator -- codex --yolo
kilix session grid add --into PANE --title worker_1 -- codex --yolo
kilix session grid add --title review -- codex --yolo   # newest agent-sessions grid with room
kilix session grid list [--name NAME]                  # grids, counts and pane slots as JSON
kilix session grid enabled                             # on or off: the default for coding sessions
```

`--new` always opens a new grid tab; `--into PANE` adds to the grid holding that
pane, in whatever OS window it is, and fails if it is full or not a grid. With
`--into` and no `--name`, the grid is the one PANE carries the marker of; with
`--name`, PANE must be in a grid of that name. Without either option, the pane
joins the newest grid of `--name` (default `agent-sessions`) with room in the
caller's OS window.
`--var NAME=VALUE` sets a pane user variable and `--env NAME=VALUE` an
environment variable for the command; both repeat. Panes are held after exit
unless `--no-hold` is given.

A pane belongs to a grid through a pane variable derived from the grid name;
matching a tab title alone never establishes ownership. A grid is therefore the
panes that carry its marker, not a tab identity: a `sessions` tab is a grid of
NAME while at least one of its panes carries NAME's marker. Two consequences
follow, both deliberate. When every marked pane of a grid has closed, the grid
is forgotten, even if manual panes keep the tab open; the next launch opens a
new grid tab. And a marked pane moved into another `sessions` tab takes the
grid with it: that tab becomes eligible for new panes of NAME. Manual panes in a
grid tab count toward capacity. A tab whose layout was manually changed is no
longer a grid and is left alone. Closed panes free capacity; held completed
panes continue to count. Use existing explicit pane commands to close a
retained pane.

Placement is serialized per engine socket by a lock in a fixed per-user folder
(`/run/user/UID/kilix-session-grid` when that directory is private, else
`/tmp/kilix-session-grid-UID`, which must be private and owned). It does not
depend on `KILIX_STORAGE_HOME`, `KILIX_SESSION_HOME`, `XDG_RUNTIME_DIR` or the
run store, so launches with different stores or homes cannot overfill a tab.

The `sessions` layout ships with the Kilix engine and is selected by name
(`goto-layout sessions`); it does not need to be in `enabled_layouts` and is kept
across configuration reloads. `enabled_layouts *` does not include it, so
`next_layout` never cycles into it; listing it by name in `enabled_layouts` makes
it an ordinary layout that cycling reaches. With an engine that lacks it, the
first pane still opens, in a plain tab, and the receipt carries a `fallback`
reason; later launches do not treat that tab as a grid.

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
or required. Deliberate differences: native UI in a session grid by default,
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
