# Help answers and documentation search

Kilix 0.2.3 RC1 includes local Help answers for everyday questions about using
Kilix and Plebian-OS. An answer provides a command when the task calls for one,
a short explanation and links to the relevant documentation. You choose
whether to run the suggested command.

## Set up Help once

Run this in a terminal with the RC1 Kilix commands available:

```sh
kilix help-setup
```

Setup requires `uv` and an internet connection for files not already installed.
It checks available memory and storage, installs the CPU runtime and downloads
about 4.6 GB of model and tokenizer files, plus the runtime packages. The RC1
default is **kilix-qwen-sft1-2b**, our trained Qwen3.5-2B Help model. It runs on
the CPU; a GPU is not required. Keep the terminal open until setup completes.

The trained adapter is hosted in the
[versioned model release](https://github.com/itsmygithubacct/kilix-help-llm/releases/tag/kilix-qwen-sft1-2b).
Setup gets the exact upstream parent separately and verifies the files against
their recorded sizes and checksums. Help model setup is separate from the OS
image's bundled models.

## Ask an everyday question

```sh
kilix help-answer "How do I launch a Kilix pane?"
kilix help-answer "How do I list panes?"
kilix help-answer "How do I launch agents?"
kilix help-answer "How do I use the TUI?"
```

For example, the pane-listing answer includes:

```sh
kilix ls --panes
```

It explains that this lists pane numbers, titles, programs and folders, then
links to the pane documentation. Links point to the exact document version
used as evidence, so they may show a pinned revision rather than the latest
repository page. On CPU, an answer can take tens of seconds.

Help explains how to use the tools. It cannot see which panes are open, inspect
your files, read your current GPU temperature or run a suggested command.
For a live list of your panes, run `kilix ls --panes` yourself.

## Search documentation without model setup

```sh
kilix help-search "How do I list panes?"
kilix help-search "How do I use the TUI?" --full
```

Search returns source passages from the bundled Kilix, Kitty, Pleb and
Plebian-OS documentation. It needs no model weights or CPU model runtime.
The Help application source can be installed on first use; without a prepared
offline source carrier, that first installation needs a network connection.
Once the source is installed, documentation search works offline.

Search is also useful when an answer is unavailable or the question needs more
detail. Read the matching passages and their source-file references before
acting on an uncertain answer.

## Check the installation

```sh
kilix app run kilix-help-llm -- model-status
```

This reports the selected model, whether its weights verify, whether the
runtime is installed and whether Help is ready. It does not download weights.
It checks files, rather than running an inference benchmark.

| What happens | What to do |
| --- | --- |
| Help asks you to run setup, or reports missing or damaged model files | Run `kilix help-setup` again. Verified files are reused. |
| Setup says `uv` is missing | Install `uv` using your system's documented setup, then retry `kilix help-setup`. |
| The resource check declines setup or an answer | Close unneeded applications or free storage as indicated, then retry. Documentation search remains available. |
| Another Help setup or answer is already running | Wait for it to finish, then retry. |
| A question is too long or cannot be answered from the documentation | Ask one shorter question or use `kilix help-search`. |
| `help-answer` is unavailable in your installed Kilix | Check `kilix --kilix-version` and `kilix status`; these instructions require the RC1 Help integration. |

For command options without model setup:

```sh
kilix help-answer --help
kilix help-setup --help
```

## Structured output and the shared LLM command

```sh
kilix help-answer --json "How do I list panes?"
```

The JSON `presentation` object contains the command, explanation and related
help links. If `kilix-llm` is installed, its default Help route uses the same
model after setup:

```sh
kilix-llm ask-help "How do I list panes?"
```

Historical research runs require their explicit run IDs; ordinary Help use
does not need a training corpus or run selection.

## RC1 scope

The default model is integrated into the RC1 source candidate. It can still
give an incomplete or incorrect answer; source integration does not establish
independent answer-quality or final OS image qualification. The
[model card](https://github.com/itsmygithubacct/kilix-help-llm/releases/download/kilix-qwen-sft1-2b/MODEL_CARD.md)
records the development results and limitations. See the
[RC1 release notes](https://github.com/itsmygithubacct/plebian-os/blob/df8bbcf6bc5fb03944f67469080cf98f27811b22/releases/0.2.3-notes.md)
for release status.

Continue with [Operating Kilix](operations/README.md),
[managed coding sessions](../session.md) or the
[preferred workflow guide](../AGENTS.md) for detailed command usage.
