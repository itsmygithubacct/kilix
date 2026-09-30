# Workflow evaluation

Evaluate the observable task result and recovery path, not the wording of a tool receipt alone. Keep capability documentation, deterministic fixture checks, and live model continuation comparisons as separate evidence.

**Shipped interfaces** describe what the selected CLI, MCP server, action controller, tmux controller, and rollout-resume utility can do. Their contracts establish supported inputs, selection rules, and what each response verifies. They do not by themselves show that a workflow saves time, improves task success, or is easy for people to use.

**Synthetic fixture validation** is the scope of the local rollout-resume battery. It uses generated records and a synthetic provider to exercise CLI listing and inspection, refusal cases, launch arguments and working-directory selection, picker key handling, detached tmux recovery, and structured-resume CLI/MCP adapters. These model-free checks cover transport and state wiring; they do not prove that an original conversation's context was recovered or that a resumed model continues its task. Automated picker input is not human usability evidence.

**Real continuation comparison is planned and has no result reported here.** It would compare resumed work with a matched continuation route, use the same task and available context, and judge the resulting artifact or action independently. Any later report should separate launch success from task completion and disclose recovery attempts, latency, and usage only when measured.

The point-in-time route tables in the [agent guide](../AGENTS.md) are task samples under their stated configurations. They are not a human usability study or a rollout-continuation comparison. No new paid results, human usability findings, or real continuation measurements are claimed by this documentation.
