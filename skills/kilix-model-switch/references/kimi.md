# Kimi Code

Open `/model` in the existing TUI and select from its configured model aliases.
Inspect its confirmation and any thinking/effort controls; do not assume the
Codex or Claude menu layout or keystrokes apply. Model aliases and API model
IDs need not be identical. Check the displayed model after the picker closes.

Keep the current conversation. `/new`, `/clear`, logout/login, or launching
`kimi --model` would not perform the requested in-place change. Do not modify
`default_model` in configuration as a workaround. If the installed picker also
persists a default and has no session-only control, disclose that limitation
before affecting future sessions.

Switching to a smaller context or a model without a modality already in the
conversation can require compaction or fail. Explain the specific limitation;
do not silently clear context or compact it merely to make the switch succeed.

Kimi Code and legacy kimi-cli differ. Confirm the actual installed program and
its `/help`; do not transfer flags, discovery directories, or picker behavior
between them without checking.

Official references, checked 2026-09-08:

- [Slash commands](https://www.kimi.com/code/docs/en/kimi-code-cli/reference/slash-commands.html)
- [Model configuration](https://www.kimi.com/code/docs/en/kimi-code/models.html)
