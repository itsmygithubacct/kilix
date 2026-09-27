# Client startup and initialization

The helper starts interactive `codex`, `claude`, `kimi`, `grok` or
`qwen-omp` (`omp --model qwen3.8-max` unless `--model` is given) directly,
with an optional `--model MODEL` (never starting with `-`).
- Approvals: a client's approval-skip flag is added only with
  `--coding-yolo`, and then only when Kilix's coding-yolo setting is on.
  Otherwise omp, which approves every tool by default, is started with
  `--approval-mode=always-ask` and a `--tools` allow-list that omits `task`.
  Omp 18.3.2's task executor forces `tools.approvalMode: yolo` for its child
  agents, irrespective of the parent's approval mode; removing `task` is the
  supported command-line restriction that closes that path.
- A launch prompt is passed only with `--prompt`: one line, never starting
  with `-`. Every one-word prompt is refused for every client, because the
  whole positional argv item could be a listed or hidden subcommand (`claude
  rc`, `codex cloud-tasks`, `grok share`). Multi-word prompts are additionally
  checked against subcommands read from `--help`. An omp prompt has no
  `@file` words.
- `--resume ID` resumes that session id.
- `--trust-folder` records the client's own trust for exactly `--cwd`
  first: claude's config under its own lock, codex's `config.toml` (checked
  before it is replaced), grok's own `--trust`; a symlinked or malformed file
  is left alone. Grok's installed folder-trust implementation records the git
  worktree/repository root when launched inside a repository (and covers its
  subdirectories, but not a nested checkout); outside Git it records the
  launch directory.

It changes no other global setting. `--agent-arg` is a per-client allow-list,
and every value is one literal, self-contained argv item (not a shell
expression): Claude accepts `--add-dir=PATH`; Codex accepts `--add-dir=PATH`
and `--effort=LEVEL`; Grok accepts `--effort=LEVEL`; qwen-omp accepts
`--thinking=LEVEL`; Kimi accepts none. All other values are refused.

| Client | Startup detail |
| --- | --- |
| Codex | `--model` selects this launch's model; the working directory comes from the pane launch. Do not use `codex exec` when an interactive coding session was requested. |
| Claude Code | `--model` is launch-scoped. Avoid `-p`/`--print`, which exits after a response. Don't replace user prompts with `--system-prompt`. |
| Kimi Code | `--model` takes a configured alias. Current `-p`/`--prompt` is non-interactive, so start the TUI first and submit initialization after it is ready. Legacy kimi-cli flags differ. The helper refuses `--prompt` and `--resume` for kimi. |
| Grok Build | `--model`/`-m`; `--resume ID`; a positional launch prompt; `--always-approve` is its approval skip; `--trust` records its own folder trust. |
| qwen-omp | `omp --model qwen3.8-max`; `--resume=ID`; positional messages; `--auto-approve` is its approval skip. Without yolo, `--approval-mode=always-ask` and an explicit built-in tool list without `task` prevent its always-yolo task subagents; `--allow-home` when started in `~`; no folder-trust prompt. |

Use the same post-startup initialization sequence for all three: inspect the
foreground client and UI, resolve any user-owned login/trust choices, submit
the initialization, then verify acknowledgement. A caller's approval policy
does not grant permission to disable the new session's own approvals.

For a long prompt, create a private file using the coding agent's file-editing
tool in a user-approved workspace or task-state directory. Keep it out of
commits and publication. Its path must be readable from the target session;
do not assume container/remote paths refer to the same filesystem. A short
bootstrap can say: “Read /absolute/path/initial.txt as my initialization prompt
and carry out that task.” Preserve the exact original file contents and verify
that it was read. Do not include credentials in the bootstrap or process argv.

When resuming a specific old conversation was explicitly requested, first
confirm its identity and absence of a conflicting live owner. Consult that
client's installed resume help; never substitute `--continue`/`--last` for a
specific session ID. Reporting a new session as a resumed conversation is an
error even if it uses the same directory and model.

Official references, checked 2026-09-08:

- [Codex models](https://learn.chatgpt.com/docs/models)
- [Claude CLI](https://code.claude.com/docs/en/cli-reference)
- [Kimi CLI](https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command.html)
