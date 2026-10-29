# Kilix 0.2.2 Start menu and settings

Run `kilix settings` to open the shared settings interface. The three-line
Start button opens the Kilix menu; `Ctrl+Alt+M` toggles the same menu. Use arrow
keys and Enter to choose an item, or Escape to dismiss it. Outside clicks
dismiss floating menus without activating the underlying pane.

Run `kilix settings --set start_menu=on` to enable the Start button, or use
`start_menu=off` to hide it. Run `kilix settings --set tab_bar_edge=bottom`
to move the page strip to the bottom; use `tab_bar_edge=top` for the top.
An explicit setting wins across hosts. With no explicit choice, standalone
Kilix puts the strip at the top and Pleb/Plebian-OS sessions put it at the
bottom. The Start button defaults off for standalone Kilix and on in Pleb.

Shared chrome and game preferences live in
`~/.local/gpu_terminal/settings.conf`. This file is a non-executable settings
contract read by the desktop stack. The Start menu opens upward from a bottom
page strip and sizes itself to fit the window without resizing terminal panes.

Use `kilix screen-size larger` or `kilix screen-size smaller` to change terminal
scale. `Ctrl+Shift+Backspace` resets the terminal scale.

The status area shows whole-system CPU and RAM just left of the temperature,
at all times by default, in the same form as each pane's resource chip:
logical cores of CPU in use, the chip, then GiB of RAM in use, for example
`6.4 󰍛 12.3`. Clicking it opens Kilix Memory. Run
`kilix settings --set system_load=auto` to show it only above the pane
thresholds (CPU above 1.0 cores, RAM at 1 GiB or more), `system_load=off` to
hide it, or `system_load=always` to restore the default. The **Top bar**
section of `kilix settings`, the desktop Settings app and Kilix95 Settings
offer the same choice as **System CPU and RAM**.

Temperature displays default to Fahrenheit. Set the shared preference with
`kilix settings --set temperature_unit=celsius` or `temperature_unit=fahrenheit`.
The **Top bar** section of `kilix settings` and the **Temperature units** choice
in Kilix95 Settings offer the same persistent control. `kilix temps --celsius`
or `--fahrenheit`, and its `u` key, override only that dashboard invocation.
Sensor readings, alert thresholds, JSON and CSV remain in Celsius.
