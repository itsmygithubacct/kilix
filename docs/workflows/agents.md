# Workflow agent routing

`kilix agent` selects a model and reasoning effort for an approved workflow using
verified benchmark results. It compares total cost divided by successful tasks,
including money and time spent on failed attempts. Each workflow has its own
evidence and reliability requirements. No workflow or winner is approved by
default.

```sh
kilix agent list
kilix agent --workflow file-review select
kilix agent --github select
kilix agent --github launch 'Read BRIEF.md and task.json' --cwd "$PWD"
```

`--github` names the `github-actions` workflow. `select` returns JSON without
connecting to a terminal. `launch` uses the selected client in a new tab anchored
to the caller, with the existing `agent.launch` identity checks and receipt.
If no candidate qualifies, it returns `insufficient_evidence` and launches
nothing. The installed client version must equal the measured version.

Global options precede the command. `--objective usd`, `subscription`, or `time`
overrides the workflow's objective. Launch options include `--operation-id`,
`--caller-pane`, `--task` (default `task.json`), and `--dry-run`. A launch preview checks terminal identity;
`select` is the independent, read-only way to inspect the decision.

Retain the operation ID from a launch receipt when recovering an interrupted
request. Reusing it with the same arguments retrieves the existing receipt;
changed arguments are refused. A created tab establishes creation, not client
readiness or task completion. Use `complete` below to verify the matching client turn and resulting artifacts.
If the policy or client version changed, recover the saved receipt through
`kilix action` with `operation.status` and its original source/target identities.

## Registry

By default the registry is `$XDG_CONFIG_HOME/kilix/agent-workflows.json`, falling
back to `~/.config/kilix/agent-workflows.json`. Use `--registry PATH` for a separate
file. The following is an **unapproved template**, with placeholder evidence and
client version; it cannot launch an agent as written.

```json
{
  "schema": "kilix.agent-workflows/v1",
  "profiles": {
    "luna-max": {
      "agent": "codex",
      "model": "gpt-6-luna",
      "reasoning_effort": "max",
      "service_tier": "standard",
      "client_version": "REPLACE WITH MEASURED VERSION"
    }
  },
  "workflows": {
    "github-actions": {
      "approved": false,
      "profiles": ["luna-max"],
      "evidence": "github-actions-evidence.json",
      "evidence_sha256": "REPLACE WITH SHA256 OF THE EVIDENCE FILE",
      "cohort_id": "github-actions-confirmation-1",
      "objective": "usd",
      "max_age_days": 30,
      "min_trials": 20,
      "min_cases": 20,
      "min_success_rate": 0.9,
      "min_success_lower_95": 0.8,
      "max_p90_s": 180
    }
  }
}
```

Profiles contain exactly the five fields above. Supported agents are `codex`,
`claude`, and `grok`. Model IDs and efforts are explicit; there is no fallback
substitution. Codex accepts low/medium/high/xhigh/max and standard/fast service
tiers. Claude accepts low/medium/high/xhigh/max; Grok accepts low/medium/high/xhigh.
Claude and Grok profiles use standard tier. The provider must support the chosen
combination; the launcher does not infer provider acceptance from tab creation.

Codex receives typed reasoning effort and a narrow service-tier configuration;
Claude and Grok receive `--effort=VALUE`. This command uses normal interactive
client configuration and permissions. Benchmark evidence must cover the working
conditions in which a route will be used, including tools, instructions, task
complexity, and context size. A sterile headless pilot alone does not qualify an
interactive production workflow.

## Evidence contract

The evidence file uses `kilix.agent-eval/v1`. Its top-level fields are:

| Field | Meaning |
| --- | --- |
| `workflow`, `cohort_id` | Match the approved registry policy. |
| `completed_at` | ISO 8601 timestamp with timezone. |
| `holdout` | Must be `true` for selection; pilot evidence is refused. |
| `cost_basis` | `billed` or `api_equivalent`; estimates are not invoices. |
| `profiles` | Exact profile map from the policy. |
| `cases` | Unique independent case identifiers. |
| `repetitions` | Positive repetition count per case and profile. |
| `trials` | Complete matched profile × case × repetition coverage. |

Every trial contains `profile_id`, `case_id`, integer `rep`, boolean `success`,
`verified`, `preservation_passed`, and `configuration_attested`, plus `wall_s`,
`cost_usd`, `subscription_units`, and `subscription_meter`. Unknown costs use
JSON `null`. Cost and elapsed time include every attempt and any declared retries
for that trial. Use the same retry policy and accounting basis for every profile.
`wall_s` includes verification. Preserve raw traces and pricing provenance in the
benchmark report; the selector checks the evidence hash, not the truth of a
self-reported result.

A success requires all four boolean checks. Cost/success is total spend across
all trials divided by verified successes. Zero successes has no finite score.
Any missing cost makes that cost objective unavailable. Subscription meters must
identify comparable account/window/units; percentages from unrelated plans are
not interchangeable. The `time` score is total seconds per success. The latency
gate uses the nearest-rank p90 of all trials, including failures and timeouts.

Reliability uses both the observed trial success rate and a 95% Wilson lower
bound over independent cases. A case counts as successful only if all its
repetitions pass. Repetition does not manufacture more independent evidence.
Any preservation or configuration failure disqualifies that profile. Among
eligible profiles the lowest score wins, with p90 then profile ID breaking ties.
This identifies the measured best eligible candidate, not a universal optimum.

The operator reviews representative held-out results and approves the policy by
setting `approved` to `true` and binding its evidence hash. Updating the model,
effort, service tier, client version, cohort, or evidence requires revalidation.
Pilot tables, missing usage, and unverified completion claims never establish an
approved route.


## Typed tasks and completion records

Tracked launches currently support Codex CLI 0.162.1 and these small-task scopes:

| Workflow | Contract scope | Independent verification |
| --- | --- | --- |
| `file-summary` | `structured-facts-v1` | Exact typed facts and per-field file/line citations. |
| `github-actions` | `offline-workflow-v1` | Exact YAML workflow structure, rejecting duplicate keys. No live dispatch. |
| `bug-fix` | `python-apply-v1` | `engine.apply(*args)` regression vectors and caller-input preservation. |
| `test-writing` | `python-unittest-mutants-v1` | Correct implementation passes; tests detect all three supplied behavioral mutants. |

Add `"contract": {"schema": "kilix.workflow-contract/v1", "scope": "structured-facts-v1"}`
to an approved summary policy, using the corresponding scope for other workflows.
Approval must cover that scope and its working conditions. A workflow name does
not qualify arbitrary repository-wide work. File review has no built-in contract.

Prepare an isolated task directory containing the brief, protected inputs and
this `task.json`:

```json
{
  "schema": "kilix.agent-task/v1",
  "workflow": "file-summary",
  "scope": "structured-facts-v1",
  "brief": "BRIEF.md",
  "inputs": ["BRIEF.md", "source.txt"],
  "output": "answer.json",
  "acceptance_file": "/absolute/operator-data/summary-acceptance.json",
  "acceptance_sha256": "REPLACE WITH SHA256 OF ACCEPTANCE FILE"
}
```

`inputs` enumerates every supplied file except the task manifest and editable
output. The manifest is automatically protected. Include `AGENTS.md` if supplied.
The limit is 32 input files and 2 MiB of protected contents; paths must be relative
without dot components or symlinks. Scratch files may be created. Supplied input
contents must remain unchanged. This is a verification contract, not a filesystem
permission grant; the agent retains its normal client sandbox and approval policy.

Acceptance data must be a regular JSON file outside the task directory. The
launcher binds its hash and stores a private copy before dispatch. Its exact
shape depends on the scope:

- Summary: `{"facts":{"release":3},"sources":{"release":{"file":"source.txt","line":1}}}`.
  Every fact has a citation to a protected input, with a positive integer line.
- CI: `{"workflow":{...}}`, the expected parsed workflow mapping. Quote `"on"`
  in JSON. YAML's unquoted `on` key is normalized for comparison.
- Python repair: `{"vectors":[{"args":[[1,2]],"expected":3}]}`. Here `args`
  contains one positional argument, itself a list. The output is `engine.py`.
- Tests: `{"reference":"...Python source...","mutants":["...","...","..."]}`.
  The reference must equal the protected `engine.py`; the output is
  `tests/test_engine.py`. Supply three distinct behavioral mutants. Source
  inspection and dynamic evaluation in generated tests are rejected by a limited
  static screen; import/runtime errors do not count as behavioral mutant kills.

Python artifact checks require `/usr/bin/bwrap` and run with a private PID/mount
namespace, no network or host home, read-only fixture files, and bounded CPU,
memory, output and elapsed time. CI checking requires PyYAML. Acceptance data is
operator-controlled; these checks do not prove an arbitrary oracle is correct.

```sh
kilix agent --workflow file-summary launch 'Read BRIEF.md and task.json' \
  --cwd /absolute/task-directory --operation-id summary-001
kilix agent status summary-001
kilix agent complete summary-001 --session /absolute/codex/rollout.jsonl
kilix agent report
```

The launch prepends a unique run marker to the literal prompt. `complete` requires
one fresh Codex turn with that exact prompt, matching cwd/client/model/effort and
a matching `task_complete` event. An unrelated or resumed multi-turn session is
refused. An unfinished matching turn remains pending. Artifact failures are
recorded as failures even when the client reports completion. Completion is an
explicit operation; there is no background watcher or automatic retry/escalation.

`status` reads the saved record without a terminal or current registry. For a
lost launch response use `kilix agent recover summary-001`; it queries the original
`operation.status` and never repeats the launch. Reusing a launch ID retrieves its
existing record and refuses changed arguments or task manifest. Completed records
are immutable: a new attempt needs a new operation ID.

Records live under `$XDG_STATE_HOME/kilix/agent-runs` (normally
`~/.local/state/kilix/agent-runs`), with private directories and files. They bind
the selected profile, benchmark hash, contract, inputs, original launch receipt,
client session/turn, artifact hash, verification checks, token totals and time.
Raw conversation text is not copied into records. The stored brief is represented
by its input hash; acceptance data and the launch prompt remain private local data.

Optional policy `api_pricing` maps each profile ID to
`{"max_input_tokens":272000,"per_million":{"input":0.2,"cached_input":0.02,"cache_write_input":0.25,"output":1.0}}`.
These are example operator-supplied rates, not an automatically updated price list.
The launcher freezes rates per run. Estimates use final cumulative usage once,
include failed attempts, and remain unknown for missing/reset counters or inputs
outside the price context limit. Requested Fast is recorded separately from
unreported served tier. Actual billing and subscription deductions remain unknown.

`agent_wall_s` measures launch through client completion; `verification_s` measures
artifact checking. `wall_s` sums them and excludes a delay before manually invoking
`complete`; `finished_at` records the actual verification timestamp. `report`
groups only matching workflow/scope/profile/evidence/pricing, retains unresolved
and unknown-cost attempts, and never turns them into zero-cost successes. These
observational records do not automatically become held-out benchmark evidence or
change the approved registry.
