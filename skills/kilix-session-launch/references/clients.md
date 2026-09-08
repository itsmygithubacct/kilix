# Client startup and initialization

The helper starts interactive `codex`, `claude`, or `kimi` directly with an
optional `--model MODEL`. It does not add permission bypasses, change global
settings, or submit a prompt automatically. Inspect the installed client's
`--help` before adding flags with repeated `--agent-arg=VALUE`; every value is
one literal argv item, not a shell expression.

| Client | Startup detail |
| --- | --- |
| Codex | `--model` selects this launch's model; the working directory comes from the pane launch. Do not use `codex exec` when an interactive coding session was requested. |
| Claude Code | `--model` is launch-scoped. Avoid `-p`/`--print`, which exits after a response. Don't replace user prompts with `--system-prompt`. |
| Kimi Code | `--model` takes a configured alias. Current `-p`/`--prompt` is non-interactive, so start the TUI first and submit initialization after it is ready. Legacy kimi-cli flags differ. |

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
