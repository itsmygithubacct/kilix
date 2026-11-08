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

Immediately right of it is total GPU use: percent busy, the GPU card glyph,
then VRAM in use, for example `37% 󰢮 2.2` (GiB; `400M` below 1 GiB). On a
machine with several GPUs it shows the discrete one; clicking it lists each
GPU, and right-clicking opens its menu (`kilix settings --widget gpu`). It is
hidden when no GPU can be read, and a GPU that is runtime-suspended reads
`sleep` and is not read at all. On a GPU whose power is managed at runtime
(`power/control` is `auto`), a reading that finds it idle (0% busy, and no
process's GPU memory changing; a display server's few resident MiB do not
count) is shown for at least 30 s, or twice the GPU's autosuspend delay, up
to 120 s, before the GPU is read again, so that polling lets it sleep. During
that time the readout and the pane chips can be up to that long out of date;
short bursts can be missed entirely. A job that wakes a sleeping GPU shows
at the next refresh unless another NVIDIA GPU is asleep or still in its idle
backoff. Opening NVML attaches every NVIDIA GPU, so in either case the
others are left unread too. An awake GPU withheld for this reason ranks above
its idle or sleeping NVIDIA sibling and shows `held` in both `always` and `auto` mode; its pane
memory is unavailable until sampling resumes. This also delays a busy GPU's
readout when its idle sibling uses runtime power management. The click card
runs a separate sampler: opening it can take an initial reading during the
bar's backoff, after which it follows the same policy. New pane processes
can have their DRM fdinfo read once during a backoff; existing clients reuse
the cached reading. Run
`kilix settings --set system_gpu=auto` to show it only at 10% busy or more
than 512 MiB of VRAM, `system_gpu=off` to hide it, or `system_gpu=always` to
restore the default. The choice is **System GPU** in the same Top bar
sections.

When a pane's own processes hold more than 512 MiB of GPU memory, the pane
chip shows it right of the RAM measurement after the same glyph. Set
`kilix settings --set pane_gpu=always` to show any amount, `pane_gpu=off` to
hide it, or `pane_gpu=auto` for the default. The 512 MiB threshold is fixed,
like the RAM and CPU thresholds. Memory is attributed by process: a service
started outside every pane (such as a model server) counts in the top-bar
total but in no pane, and a pane running only a client of that service shows
no GPU chip. Like the RAM chip, a process that belongs to two panes' trees
(a terminal started inside a pane) is counted in both.

Temperature displays default to Fahrenheit. Set the shared preference with
`kilix settings --set temperature_unit=celsius` or `temperature_unit=fahrenheit`.
The **Top bar** section of `kilix settings` and the **Temperature units** choice
in Kilix95 Settings offer the same persistent control. `kilix temps --celsius`
or `--fahrenheit`, and its `u` key, override only that dashboard invocation.
Sensor readings, alert thresholds, JSON and CSV remain in Celsius.
