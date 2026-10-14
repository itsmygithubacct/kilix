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
`--caller-pane`, and `--dry-run`. A launch preview checks terminal identity;
`select` is the independent, read-only way to inspect the decision.

Retain the operation ID from a launch receipt when recovering an interrupted
request. Reusing it with the same arguments retrieves the existing receipt;
changed arguments are refused. A created tab establishes creation, not client
readiness or task completion. Verify the resulting work separately.
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
