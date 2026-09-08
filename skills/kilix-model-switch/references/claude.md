# Claude Code

Open `/model` without an argument and inspect the live picker. Current Claude
Code distinguishes **Enter** (switch and save as default) from **s** (switch
for this session only). Highlight the intended model and, when the displayed
controls confirm this behavior, send the single letter without Enter:

```sh
kilix agent-control send "$PANE" --expect-broker "$BROKER" --text 's'
kilix agent-control dump "$PANE" --lines 80
```

Typing `/model NAME` directly also saves the default in current versions; it
is not the session-only shortcut. Older versions had different persistence
controls, so follow the installed UI and supported documentation. Do not
rewrite `~/.claude/settings.json` or change provider environment variables.

Verify the model confirmation and `/status`. Aliases resolve according to the
account/provider and can change over time; retain an exact model ID when the
user supplied one. Respect unavailable/restricted models and billing consent
dialogs. Changing the main session's model does not prove every already-running
subagent changed models.

Official reference, checked 2026-09-08:
[Model configuration](https://code.claude.com/docs/en/model-config).
