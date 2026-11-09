# Development

`./src` is a submodule of the
[kitty fork](https://github.com/itsmygithubacct/kitty/tree/work/0.2.2-rc2-engine)
(branch `work/0.2.2-rc2-engine`). It's a **full fork** — kilix keeps whatever changes make the
best experience. The clickable-button feature is these Python files:

- `kitty/window_title_bar.py` — draws the keyboard and
  `+ - ← ↑ ↓ → ▢ ✕` in each pane title bar, recording which cells map to which
  kitty action and keeping selected keyboard buttons depressed.
- `kitty/kilix_battery.py`, `kitty/tab_bar.py`, and `kittens/kilix_clock/` —
  draw the clickable thermometer/network/date/time status and its Kilix Temps,
  NetworkManager, calendar/date widgets, and read Linux thermal and battery
  status for the colored indicators in the page strip.
- `kitty/tabs.py` — keeps synchronized-input membership per tab and dispatches
  normal button clicks; a keyboard double-click promotes the individual toggle
  to select-all or deselect-all. Its quadrant drag-to-split hit-test uses the
  pane's true diagonals (rejecting drops on a maximized pane).
- `kitty/keys.c` and `kitty/boss.py` — fan unconsumed native key and IME commit
  events out to the selected peers while preserving each terminal's keyboard
  modes.

The buttons reuse kitty's existing window-title-bar → Python click routing. The fork also
carries quality-of-life fixes on top — e.g. `glfw/linux_notify.c` raises the DBus
notification-server probe timeout to silence a spurious "Notify NoReply" warning at
startup. Branch history: clickable chrome, double-fire fix, DBus-warning fix.

`./third_party/kitty-frame-presenter` pins the independently tested Python
presentation library used by the browser, app panes, and desktop provider.
Keep capture, terminal input, and application policy in Kilix; reusable damage,
transport, composition, and pacing changes belong in that module first.

`../kilix-modules/kitty-pty-broker` is the independent C/POSIX pane-lifetime
library. Kilix owns policy—deciding which windows persist, restoring detached
sessions, and confirming destructive close—while the library owns PTYs,
process groups, socket authentication, resize forwarding, and byte-transparent
replay.

`./third_party/kilix-state` pins both the native crash-safe state
implementation and its Python binding. External desktop providers consume the
binding through `kilix_sdk.state`; Kilix builds the shared library into its
private build directory before launching the provider.

**Build / rebuild:** `kilix --build` (or `./build.sh`). Needs Go ≥ 1.26,
Python ≥ 3.12, plus the
system build deps from [Requirements](install.md#requirements). The binary lands at
`~/.local/gpu_terminal/kilix/build/current/src/kitty/launcher/kitty`. The build
uses an exact committed-source snapshot, refuses a dirty `./src`, and records
that commit as `source-id`, so generated objects and binaries never land in
`./src`. Generation cleanup retains any build containing a live executable, so
long-running terminals survive rebuilds; a later successful build or update
reaps the generation after its last process exits. Put a machine-specific
toolchain environment in `~/.local/gpu_terminal/kilix/config/build.env`. Go package
compilation defaults to one job so the fork can build on memory-constrained
systems; set `KILIX_BUILD_JOBS` to a larger positive integer to trade memory for
build speed. Set `KILIX_PYTHON` in `build.env` when Python 3.12+ is installed
outside the normal `PATH`; the build records its library directory in the
launcher so that an isolated interpreter remains usable at runtime.

> **Python edits are live on the next launch — no rebuild needed.** Only C changes
> require `--build`. To rebind the buttons, edit the action strings in
> `src/kitty/window_title_bar.py`.

## Layout

```
~/.local/gpu_terminal/sources/kilix/
├── kilix              # launcher (this is what you run)
├── kilix-settings     # shared chrome/game settings TUI
├── build.sh           # builds the forked kitty in ./src
├── bootstrap.sh       # pulls the prebuilt kitty (fallback engine)
├── config/            # kitty.conf + kilix icons (kitty.app*.png, kilix-512.png)
├── desktop/           # bundled Kilix 95 compatibility provider
├── src/               # tracked kitty fork; remains clean after builds
├── third_party/       # pinned presenter, content, and state libraries
├── README.md
├── LICENSE            # GPLv3 (kitty is GPLv3)
└── .gitignore
```
