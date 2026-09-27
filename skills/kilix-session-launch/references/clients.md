# Client startup and initialization

The helper starts interactive `codex`, `claude`, `kimi`, `grok` or
`qwen-omp` (`omp --model qwen3.8-max` unless `--model` is given) directly,
with an optional `--model MODEL` (never starting with `-`).
- Approvals: a client's approval-skip flag is added only with
  `--coding-yolo`, and then only when Kilix's coding-yolo setting is on.
  Otherwise omp, which approves every tool by default, is started with
  `--approval-mode=always-ask`. `--agent-arg` can never carry an approval,
  permission, sandbox or trust change.
- A launch prompt is passed only with `--prompt`: one line, never starting
  with `-`, and never starting with one of the client's own subcommands
  (read from its `--help`: "codex logout", "claude update"). An omp prompt
  must be more than one word, with no `@file` words.
- `--resume ID` resumes that session id.
- `--trust-folder` records the client's own trust for exactly `--cwd`
  first: claude's config under its own lock, codex's `config.toml` (checked
  before it is replaced), grok's own `--trust`; a symlinked or malformed file
  is left alone.

It changes no other global setting. Inspect the installed client's
`--help` before adding flags with repeated `--agent-arg=VALUE`; every value is
one literal argv item, not a shell expression.

| Client | Startup detail |
| --- | --- |
| Codex | `--model` selects this launch's model; the working directory comes from the pane launch. Do not use `codex exec` when an interactive coding session was requested. |
| Claude Code | `--model` is launch-scoped. Avoid `-p`/`--print`, which exits after a response. Don't replace user prompts with `--system-prompt`. |
| Kimi Code | `--model` takes a configured alias. Current `-p`/`--prompt` is non-interactive, so start the TUI first and submit initialization after it is ready. Legacy kimi-cli flags differ. The helper refuses `--prompt` and `--resume` for kimi. |
| Grok Build | `--model`/`-m`; `--resume ID`; a positional launch prompt; `--always-approve` is its approval skip; `--trust` records its own folder trust. |
| qwen-omp | `omp --model qwen3.8-max`; `--resume=ID`; positional messages (its first word can name an omp command, so one-word prompts are refused); `--auto-approve` is its approval skip, and it approves everything by default, so `--approval-mode=always-ask` is passed otherwise; `--allow-home` when started in `~`; no folder-trust prompt. |

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
