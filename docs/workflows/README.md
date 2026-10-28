# Kilix Workflows

Kilix Workflows makes terminal work easier to start, coordinate, verify, and resume—for people and agents.

Kilix remains the terminal and pane manager. Workflows connect its visible controls and direct CLI with optional language and MCP tools, structured action receipts, explicit tmux control, conversation recovery, and claudex session routes. Availability depends on the components selected on the host; source files in a checkout do not prove that a command or backend is installed. See the [agent guide](../AGENTS.md) for the current toolkit selection notes.

## Choose a route

| Need | Route |
| --- | --- |
| Work directly in the terminal | Use Kilix's pane and page controls, or the basic `kilix ls`, `kilix ls --panes`, `kilix focus`, `kilix new-pane`, and `kilix watch` verbs. |
| Run a known terminal operation | Use a direct Kilix verb. For a child command, `--` separates its argument vector: `kilix new-pane down -- htop`. |
| Run a read-only system question with the explicit grammar | When claudex is available, `kilix claudex route grammar` opens `kilix-needle system --baseline`. That grammar does not fall back to a model. |
| Phrase a request for Needle | `kilix claudex route needle` opens the `kilix-needle` prompt. Its grammar reads an exact request, and the model is the fallback. When the job is already known, use that job's CLI or MCP tool. `kilix ask` shows a person the route and the plan and acts only on a typed yes. |
| Search files or read system state from a shell | Use `find`, `rg`, `ps`, `df`, and the other shell tools. `kilix claudex route shell` opens a shell for that work. |
| Open a coding session from remaining usage | `kilix claudex route max`, `kilix claudex route security`, or `kilix claudex route worker low\|mid\|high\|max`. `kilix claudex usage` reads the remaining Codex, Claude, Grok, and Qwen numbers those routes consult. |
| Automate a known Kilix action | Use the structured action CLI or its MCP tools. Supply one versioned request with exact identities and keep its receipt. |
| Work with an existing tmux server | Use `kilix tmux` or Needle's tmux job with that server's explicit socket on every request. |
| Find and resume a saved agent conversation | Use the rollout-resume picker or CLI to list candidates, inspect one exact session, then resume it. |

For ordinary inspection, `kilix ls --panes` lists pane IDs and `kilix watch --once PANE_ID` reads one pane snapshot. Use the current pane ID from the listing; numeric row positions and display titles are not stable identities. Kilix's basic `focus` verbs qualify targets as `pane:ID` or `tab:ID` when needed.

## Choose the Ask model

Model Setup's Local Workflows page offers LFM2 by default, Needle3 as an
alternate, and grammar without a model download. Older Needle2 jobs have their
own Legacy Needle workflows page. Accepting one model does not verify another.

`kilix-needle workflows status --json` checks the selected Ask backend without
loading a model, downloading files or changing state. Its result names the
backend and verifies its release pins, installed files, licence and platform.
Use `--legacy` for the separate Needle2 job checks.

`kilix-needle workflows setup --model needle3` installs through the normal
licence flow and saves the preference after verification. Use `--model grammar`
for download-free setup, or `workflows select --model lfm2` to select an already
installed model. Ask and the desktop Workflows check read the same preference.
An explicit `kilix ask --model MODEL` takes priority over
`KILIX_ASK_COMMANDS_BACKEND`, which takes priority over the saved selection.
File readiness does not establish inference speed or successful task completion.

## Human and agent workflows

People can use the visible pane chrome and switcher to choose where work goes, then read the pane itself to see what happened. Direct verbs are useful when the operation is already clear. The `--` separator keeps a program and its arguments distinct from Kilix's own options.

An agent should first identify the selected stack and its current pane context. Where available, `kilix agent-control context` gives a bounded snapshot; `kilix action capabilities` reports the selected action interface. These are observations, not authorization, readiness checks, or proof that an optional component can run. Refresh pane and broker identities after a restart or stale-identity error.

For a known structured action, pass the complete JSON body in the same invocation, for example `kilix action --request-json - < action.json`. A request names its schema, unique operation ID, operation, exact source and target pane/broker identities, and parameters. Titles are labels, not selectors. The controller rechecks identities before a side effect; a preview is optional. Needle's structured MCP surface is `kilix_action_plan`, `kilix_action_act`, and `kilix_action_status`; `kilix-needle mcp --tools actions` selects only those tools. Tool selection does not change identity checks or receipt guarantees.

For a request phrased in natural language, use its matching Needle job rather than treating every request as one shared grammar. When the job is unclear, Needle's `ask` router chooses one from the request text alone and hands the request to it unchanged; the job's own grammar, target checks and consent still apply. An unsure route never acts: it names candidate jobs with one accepted phrasing each. `kilix ask` shows a person the route and plan and acts only on a typed yes; `kilix ask --voice` dictates the request first and lets the person correct the transcript. MCP plan tools resolve a request without mutation; act tools perform it subject to their consent rules. Ambiguous or unsupported targets should be resolved explicitly before the action is retried.

## Select exact targets

Structured actions bind the caller and destination by pane ID and broker identity. Reuse the identities from the selected live snapshot, and use the same operation ID when recovering that request. A title can help a person distinguish panes, but it is not an identity check.

Tmux control is independent of the caller's ambient tmux environment: every `kilix tmux` request requires an explicit absolute `--socket`. The controller accepts exact session names, stable session IDs such as `$0`, stable pane IDs such as `%3`, or numeric `NAME:WINDOW.PANE` addresses for I/O. A session name is insufficient for I/O when it has multiple panes. `send` writes literal text without Enter; `type` sends text and then Enter. A response can verify submission while leaving command completion unknown. Needle's `kilix_tmux_plan` and `kilix_tmux_act` tools take the same explicit socket.

## Read the lifecycle correctly

Treat four events separately:

1. **Opened:** the pane or session exists. A creation receipt can verify this; it does not prove the launched program is ready.
2. **Submitted:** the requested text reached the target input path. A delivery receipt or tmux response can verify submission; it does not prove the agent read or accepted the request.
3. **Acknowledged:** the agent explicitly recognizes the task. Structured action receipts do not infer this from screen activity or silence.
4. **Completed:** the requested result has been checked against the task. A pane opening, message submission, or agent acknowledgment alone does not establish completion.

If a structured call is interrupted or its result is lost, query `operation.status` with the original operation ID and identities. Status reads the receipt; it does not repeat the mutation. Use it for missing or uncertain results, not as routine preflight. If a result is uncertain, inspect the exact target before deciding on any new action.

For pane and tmux input, read the target after submission and check the actual effect. A successful transport response only describes that transport's verified step.

## Open a claudex route

When claudex is available, `kilix claudex` lists the routes. Kilix verbs stay the pane commands in the table above. Claudex does not open a pane for them.

```sh
kilix claudex route grammar          # kilix-needle system --baseline
kilix claudex route needle           # kilix-needle prompt
kilix claudex route shell            # bash
kilix claudex route worker low       # Codex and Claude; low, mid, high, or max
kilix claudex route max --dry-run    # print the choice; open nothing
kilix claudex usage                  # remaining Codex, Claude, Grok, and Qwen
```

Grammar, needle, and shell do not read usage and do not take a tier. Worker does not read usage and takes `low`, `mid`, `high`, or `max`. Max and security open one session from remaining usage. A reading stays valid for 30 minutes. A missing or signed-out reading is not zero. `--dry-run` prints the choice and opens nothing. An opened pane is the opened event. It does not show that the session finished the task.

## Resume saved conversations

When rollout-resume is available, begin with a candidate list and inspect the exact session before launching it:

```sh
kilix-rollout-resume list --state candidates
kilix-rollout-resume show SESSION_ID
kilix-rollout-resume resume SESSION_ID --dry-run
```

After reviewing the plan, `kilix-rollout-resume resume SESSION_ID` launches that selected conversation. The utility also exposes states such as `idle`, `cut-off`, `live`, `orphaned`, and `invalid`; exact filters and options depend on the selected version. A launch is an opened session, not evidence that the resumed model continued the task correctly. Check the new work against the saved request and expected result.

See [workflow evaluation](benchmarking.md) for the distinction between shipped interfaces, deterministic fixture validation, and real continuation evidence.
