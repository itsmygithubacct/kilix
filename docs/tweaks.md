# Tweaks

Use Start ▸ Settings in kilix 95, or edit
`~/.local/gpu_terminal/kilix/config/kitty.conf`. It includes the tracked
`config/kitty.conf` defaults; add overrides to the user file.

Use `kilix settings` for clickable chrome and Kilix 95 game availability.
Thermometer, volume, network, calendar, date/time, battery, pane-CPU and
pane-memory modes,
synchronized input, font-size, four-way split, maximize, close, and game toggles all live in
`~/.local/gpu_terminal/settings.conf`, which Kilix, Kilix 95, Pleb, and
Plebian-OS share.

The TUI separates Top bar, Pane buttons, Games, and Tools. Switch sections with
Left/Right, `h`/`l`, Tab/Shift-Tab, or `1`–`4`; use lowercase `a`/`n` for all
items in the current settings section and uppercase `A`/`N` for every setting.
The Tools section can download/open Kilix Memory, download/run Tmux Manager
through Kilix's pinned installers, or install tmux-cli's `tb` alias. Run
`kilix games settings` to open Games directly.

For scripts, `kilix settings --set temperature=on` enables the thermometer and
`kilix settings --set temperature=off` disables it. The interactive TUI exposes
the same **Thermal status** control in its Top bar section. Likewise,
`kilix settings --set synchronize_input=off` hides the keyboard button (and
`=on` restores it). Use `kilix settings --set pane_memory=auto` for the
default 1 GiB threshold, `pane_memory=always` for an always-visible MiB/KiB
readout, or `pane_memory=off` to hide the RAM side. The symmetric
`pane_cpu=auto` shows pane process-tree use above one core (`1.0`); use
`pane_cpu=always` or
`pane_cpu=off` to override that policy. The top bar's system CPU and RAM
readout takes `system_load=always` (the default), `system_load=auto` or
`system_load=off`. The GPU widget beside it takes `system_gpu=always` (the
default), `system_gpu=auto` or `system_gpu=off`, and the pane GPU chip takes
`pane_gpu=auto` (the default, above a fixed 512 MiB), `pane_gpu=always` or
`pane_gpu=off`.

- **Quieter page strip:** `tab_bar_min_tabs 2` (hide it until a 2nd page) and
  `tab_bar_show_new_tab_button no` (hide the `+`).
- **Title bars only when split:** `window_title_bar_min_windows 2` (default `1` = always).
- **Active-pane accent:** `active_border_color` (and `window_title_bar_active_background`).
- **Inactive dimming:** `inactive_text_alpha` (`1.0` = no dim).
- **Rebind the buttons:** edit the action strings in `src/kitty/window_title_bar.py`.

Kilix-only runtime knobs live in the XDG `kilix/kilix.env` and are also exposed in
Start ▸ Settings. That includes Kilix 95 provider and flavor selection,
desktop/recycle paths, host clipboard sync, X app
behavior, streaming/debug options and build/update pins.
