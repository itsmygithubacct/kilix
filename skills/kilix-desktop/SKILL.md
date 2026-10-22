---
name: kilix-desktop
description: Control X11 desktop windows, pointer, keyboard and scrolling through Kilix desktop actions. Use for explicit desktop input or window manipulation; use pane tools for terminal sessions and file tools for files.
---

Use the interface the user requested. For ordinary desktop actions, use
`kilix desktop-action`; `kilix-desktop` provides the same typed commands.
`kilix desktop-action --help` lists the families; subcommand `--help` gives
arguments. Calling with no arguments or `--help` only prints help. `schema` prints
the action contract.

Inspect windows with `kilix desktop-action --json window list`. Reuse fresh
known IDs and readiness rather than repeating discovery. Select `--target
'id:DECIMAL_ID'`, `--target 'title:EXACT TITLE'`, or explicitly `--target active`.
An ambiguous title is refused. Resolve the intended window before mutation.
Focus and input may be combined in a batch. Input goes to the focused window;
an arbitrary window title does not identify a text field inside it.

Preview is the default. For an action already authorized by the user, pass
`--yes`; no additional permission conversation is needed. Example syntax:

```sh
kilix desktop-action --yes window focus --target 'title:Notes'
kilix desktop-action --yes window resize --target active --width 800 --height 600
kilix desktop-action --yes pointer move --x 300 --y 200
kilix desktop-action --yes pointer click --button left
kilix desktop-action --yes keyboard type --text 'literal text'
kilix desktop-action --yes keyboard press Return
kilix desktop-action --yes scroll down --steps 3
```

Pass literal text as one safely quoted argument, or use `keyboard type
--text-file FILE` (`-` for stdin). Never interpolate the payload as shell code.
Typing does not submit; press Return separately only when requested. Text is a
single line, up to 512 UTF-8 bytes. Do not trim it or append a newline.

`batch FILE` accepts 1–6 actions: `[{"name":"window","arguments":{"action":
"focus","target":"title:Notes"}},{"name":"keyboard","arguments":{"action":
"type","text":"hello"}}]`. `--parse-only` validates without a desktop connection.
All targets are resolved before any action. Execution stops on the first failure;
previous effects remain. Inspect an `uncertain` receipt before retrying input.

For natural language use `kilix-needle desktop --parser grammar|needle|hybrid
--yes REQUEST`. Grammar accepts direct clauses such as `focus window "Notes"
then type "hello"`, `move the pointer to 300, 200`, and `wait 100 ms`. Quote exact
titles and literal text. Hybrid tries grammar, then the selected Needle model.
Needle mode always uses the model; model availability is a separate prerequisite.
Use the requested mode, without silently substituting another route on failure.

MCP desktop read/plan/act mirrors these operations. Act needs
`confirm_risky: true` and either `request` (optional `parser`) or `actions`,
exclusively. A submitted receipt proves event submission, not application success.
Verify the requested result with an application observation or fresh window
state. Do not infer saved data or completed work from exit status alone.

This backend requires X11/EWMH and a recent xdotool. It does not identify visual
controls, invent coordinates, implement native Wayland input, or support loops,
conditions and arbitrary commands. Resolve those requirements before sending input.
