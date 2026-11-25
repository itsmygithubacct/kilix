# Ask for help

For 0.2.3 RC1, `kilix help-setup` installs the default **kilix-qwen-sft1-2b**
model and its CPU runtime. This explicit setup downloads about 4.6 GB of
checkpoint files; the shared model sizer checks available memory and storage.
Then run `kilix help-answer "How do I list panes?"` for a command, a brief
explanation and related documentation links. Answers run locally. The model
does not inspect live machine state or execute the commands it suggests.
Use `--json` for structured output.

```sh
kilix help-setup
kilix help-answer "How do I launch a Kilix pane?"
kilix help-answer "How do I list panes?"
kilix help-answer "How do I launch agents?"
kilix help-answer "How do I use the TUI?"
```

See [Help answers and documentation search](help/answers.md) for setup,
installation checks, examples and troubleshooting.

The model is an RC1 default selected from exposed development results, with
known errors. It is not independently qualified. Setup is separate from the
OS image's existing bundled models.

Run `kilix help-search "how do I split a pane"` to search the bundled Kilix,
Kitty, Pleb and Plebian-OS documentation. The pinned Help app installs on
first use. Search uses no model weights and returns source passages.
