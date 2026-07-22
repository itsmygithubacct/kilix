# kilix — kitty that looks & behaves like Tilix, with clickable pane buttons

`kilix` is a self-contained wrapper around a **fork of kitty** that gives
each pane's title bar clickable **`+ - ← ↑ ↓ → ▢ ✕` buttons** — local text
size, four-way splits, maximize, and close — just like Tilix's pane headers,
on top of kitty's GPU-rendered speed. For Tilix users who want kitty
underneath, and anyone who wants clickable split/maximize/close chrome on kitty.

It runs its own kitty binary with its own config and icon, so it leaves any
kitty you already have completely untouched. Tracked defaults stay in
`config/`; every Kilix-owned writable file lives below
`~/.local/gpu_terminal/kilix`. Stack-wide chrome and game-availability
preferences are the intentional exception: every GPU Terminal project reads
`~/.local/gpu_terminal/settings.conf`.

The default layout is `config/` for user settings, `state/` for persistent
state, `cache/` for regenerable data, `session/` for sockets and frame files,
`data/` for optional downloads, `build/` for compiled fork generations, and
`prebuilt/` for the fallback kitty bundle. `KILIX_STORAGE_HOME` relocates the
complete tree. Freedesktop launchers/icons are the intentional exception:
`--install-desktop` uses the standard XDG application paths.

![kilix — pages strip with + button, per-pane title bars with clickable split/maximize/close buttons, splits, and icat](config/kilix_demo.png)

## Release 0.1.3

Version 0.1.3 ships the Kilix 1.3 provider SDK. A shared immutable content
catalog now drives both desktop providers, while `XAppSession` owns private X
display authentication, application/capture processes, XDamage-to-ffmpeg
fallback, input injection, and teardown. These boundaries keep provider code
focused on presentation and make every catalog checkout recursive, pinned,
verified, and atomically selected. SDK 1.2 also gives the providers and both
settings interfaces one game-availability contract. SDK 1.3 adds the shared
volume-widget setting used by Kilix, Kilix 95, Pleb, and Plebian-OS.

## Release 0.1.2

Version 0.1.2 standardizes source checkouts under `~/gpu_terminal`, keeps all
writable state under `~/.local/gpu_terminal`, isolates bundled Kilix from the
external Kilix-95 provider, makes browser/session data private, and records
builds from exact committed kitty-fork sources. Fork builds publish one
canonical, contained generation and source stamp; direct Kilix, Pleb,
Plebian-OS update, and first-boot paths share one private transaction lock.
Failed updates restore the exact source, `current` and `previous` generation
links, and stamp before safely collecting unreferenced generations; both
`kitty` and `kitten` must pass bounded launcher probes before commit. It
retains the origin/ref-aware updates, pinned downloadable assets, versioned
host SDK, and provider contract introduced in 0.1.1.

## Features

- **Clickable pane buttons** `+ - ← ↑ ↓ → ▢ ✕` — local font size, four-way
  split, maximize, and close controls that highlight on hover.
- **Network/Wi-Fi-in-chrome** — a network item immediately left of the calendar
  opens NetworkManager's `nmtui` in an overlay pane.
- **Battery-in-chrome** — on laptops, a green/yellow/red battery item appears at the
  far right of the page strip while the battery is discharging, with the percentage
  shown to the left of the battery icon; click it to hide/show the percentage.
- **Date/time-in-chrome** — the page strip shows a high-contrast local date and
  time immediately to the left of the battery item. Click its calendar icon for
  a navigable month widget, or the date/time text for a live date widget.
- **Pane title menu** — click a pane's title for Tilix-style actions: rename, copy title,
  reset, clear, split right/down, close.
- **Drag-to-split by quadrant** — drag a pane's header onto another pane's edge to split it (Tilix's model).
- **Pages (Tilix sessions)** — each page is a kitty tab, with an always-on page strip and a `+` button.
- **Input broadcast** — `Ctrl+Alt+B` mirrors your typing to every pane in the page
  (Tilix's "synchronize input").
- **Tilix look & keys** — per-pane title bars, active-pane highlight, dimmed inactive panes, Tango palette, Tilix keybindings.
- **Own taskbar identity** — groups separately from plain kitty, with its own icon.
- **Stream to other devices** — persist a session and attach (or watch read-only)
  from another machine, share a GUI app to a browser/VNC client, or stream the whole
  kilix — graphics and video included — to any browser. Loopback-first, opt-in.
- **kilix 95** — a Windows 95-style desktop environment in a tab (`kilix desktop`):
  start bar, launchers, file manager, and a Settings app that edits the kilix
  config live.
- **Host SDK for desktops** — external desktop providers import stable helpers
  from `config/kilix_sdk` instead of depending on raw `config/browse.py` /
  `config/gfx.py` internals. SDK 1.2 includes shared content installation,
  authenticated private-X-application sessions, and game availability.
- **Self-contained** — prefers its bundled fork build, and falls back to a prebuilt kitty if you haven't built it.

## Requirements

- **Linux only**, x86_64 or arm64 for the prebuilt engine. The clickable-chrome
  fork build currently supports x86_64. (No macOS/Windows.)
- A running graphical session — **X11 or Wayland** (`$DISPLAY` or `$WAYLAND_DISPLAY`).
- NetworkManager's **`nmtui`** for the clickable network/Wi-Fi item. Pleb and
  Plebian-OS install it; standalone Kilix shows an explanatory error if it is
  unavailable.
  It's a GUI terminal; it won't run headless / over plain SSH.
- **To run the prebuilt kitty** (no buttons): `git`, `curl`, `tar`.
- **To build the fork** (the buttons): **Go ≥ 1.26**, **Python ≥ 3.12**, a C compiler, `pkg-config`, and
  kitty's build deps — `x11 xrandr xinerama xcursor xi xkbcommon xkbcommon-x11
  x11-xcb dbus-1 gl fontconfig libpng lcms2 cairo-fc harfbuzz libcrypto`,
  `libxxhash`, Wayland protocols/headers, and SIMDe headers. By default the
  build uses these signed package-manager
  dependencies and downloads only the immutable, SHA-256-pinned Symbols Nerd
  Font release. An offline/release build may instead set
  `KILIX_BUILD_MODE=bundle` with an immutable `KILIX_KITTY_DEPS_URL` and matching
  SHA-256; mutable kitty CI bundle URLs are rejected. **`scripts/install-build-deps.sh` installs
  all of that** on Fedora/RHEL (dnf), Debian/Ubuntu (apt), Arch (pacman), and
  openSUSE (zypper). Where the distro's Go is older than the fork needs (e.g. Fedora
  ships 1.25), it configures the exact `toolchain` version from `go.mod` so Go
  can fetch that checksum-verified toolchain on demand. `build.sh` forces that
  exact version even if the host has a newer Go — no open-ended latest lookup
  and no manual Go install. Current kitty source also uses Python 3.12 syntax;
  `build.sh` selects `python3.14`, `python3.13`, or `python3.12` in that order.
  Set `KILIX_PYTHON=/path/to/python3.12+` when the desired interpreter is not
  on `PATH`.
- The same dependency installer also includes kilix-amp's SDL/libsndfile/
  FluidSynth packages, so the desktop Media Player can build and play MIDI.
- **For the pixel desktop and web-in-a-pane** (`kilix desktop` / `kilix browse`):
  **Python 3 + Pillow** (also installed by `scripts/install-build-deps.sh`).
- kitty **≥ 0.47** (the fork is 0.47.x) — required for the per-pane title bars.

## Quick start

```bash
mkdir -p ~/gpu_terminal
git clone --recursive https://github.com/itsmygithubacct/kilix.git ~/gpu_terminal/kilix
~/gpu_terminal/kilix/kilix
```

(`--recursive` pulls the Kitty fork and the pinned
`kitty-frame-presenter` and `kilix-content` submodules. Cloned without them? Run
`git submodule update --init --recursive`; the base terminal can use its
prebuilt fallback, but pixel applications need the presenter.)

On the **first run**, kilix tries to build the fork. If build dependencies are
missing it falls back to `bootstrap.sh`. For supply-chain safety, downloading a
prebuilt now requires a pinned version + SHA-256 (recommended) or explicit
`--allow-unverified` consent; Kilix never silently executes an unverified asset.

| Engine | Buttons? | Needs |
|---|---|---|
| **Fork build** (`kilix --build`) | ✅ `→ ↓ ▢ ✕` | Go ≥ 1.26 + Python ≥ 3.12 + X11 build deps |
| **Prebuilt fallback** (`bootstrap.sh`) | ❌ no buttons | `git`, `curl`, `tar` |

To skip the build attempt and go straight to a verified prebuilt engine:

```bash
KILIX_PREBUILT_VERSION=0.47.0 \
KILIX_PREBUILT_SHA256=<sha256-of-kitty-txz> \
~/gpu_terminal/kilix/bootstrap.sh
~/gpu_terminal/kilix/kilix          # run it (no buttons until you build the fork)
```

The version and checksum are mandatory unless a user explicitly passes
`--allow-unverified` after reviewing the printed release URL:

```bash
KILIX_PREBUILT_VERSION=0.47.0 \
KILIX_PREBUILT_SHA256=<sha256-of-kitty-txz> \
~/gpu_terminal/kilix/bootstrap.sh
```

To get the buttons, install the build deps and build the fork:

```bash
~/gpu_terminal/kilix/scripts/install-build-deps.sh   # Go + X11 dev libs + Python/Pillow
~/gpu_terminal/kilix/kilix --build                    # compile the clickable-chrome fork
```

(`scripts/install-build-deps.sh --verify` re-checks without installing.)

Then, optionally:

```bash
~/gpu_terminal/kilix/kilix --install-desktop   # app-menu entry + taskbar icon
```

To pull the latest kilix into your checkout:

```bash
kilix update                      # verified fast-forward in ~/gpu_terminal/kilix
```

To inspect a running kilix instance:

```bash
kilix ls                          # list live pages/tabs
kilix ls --panes                  # list individual pane IDs
kilix focus <tab-or-pane-id>      # jump to a live tab or pane
kilix watch <pane-id>             # best-effort read-only text watch
kilix screen-size larger          # increase terminal scale (font_size +2pt)
kilix screen-size smaller         # decrease terminal scale (font_size -2pt)
kilix settings                    # shared chrome/game settings TUI
kilix games list                  # show games available in Kilix 95
kilix games settings              # open the TUI directly on Games
kilix games disable doom          # hide a game (enable reverses it)
kilix status                      # version/commit, engine, writable config, provider contract
```

Put `~/gpu_terminal/kilix` on your `PATH` (or
`ln -s ~/gpu_terminal/kilix/kilix ~/.local/bin/kilix`) to just type `kilix`.

## Clickable buttons (the headline feature)

Every pane's title bar shows these font/split/maximize/close buttons on the right (bold):

| Button | Click does | Same as |
|---|---|---|
| `+` | increase font size for this Kilix window | `change_font_size current +2.0` |
| `-` | decrease font size for this Kilix window | `change_font_size current -2.0` |
| `←` | split left — new pane to the left | split right, then swap |
| `↑` | split up — new pane above | split down, then swap |
| `↓` | split down — new pane below | `Ctrl+Alt+D` |
| `→` | split right — new pane to the right | `Ctrl+Alt+R` |
| `▢` | maximize / zoom the pane | `Ctrl+Alt+Z` |
| `✕` | close the pane | `Ctrl+Alt+W` |

The buttons are drawn as text or **Nerd Font icons** — `+`/`-` for local font
size, bold arrows for splits (pointing where the new pane lands), a maximize
glyph, and a close ✕ — and they **highlight under the cursor**. Clicking a
header focuses the pane, and a click on the title itself opens the **pane action
menu** — rename, copy title, reset, clear, split right/down, close (maximize
also lives on the `▢` button and `Ctrl+Alt+Z`).
The active pane's header is highlighted (bright blue); inactive panes are grayed —
matching Tilix's active-pane cue.

The far right of the page strip shows volume, network, calendar, local
date/time, and (when applicable) battery items. The volume icon opens
`pulsemixer` in an overlay pane (`alsamixer` is used as a fallback). It sits to
the left of the network/Wi-Fi icon, which remains immediately left of the
calendar and opens `nmtui`. Click the calendar icon for a navigable month
widget, or click the date/time text for a live local-date, clock, and timezone
widget.
When Linux reports a laptop battery is **discharging**, a battery status item appears to its right.
It is green above 50%, yellow at 50% and below, red at 20% and below, and
shows the percentage to the left of the battery icon. Clicking it toggles the
percentage on/off. Use `kilix settings` or Start ▸ Settings ▸ Top bar / Pane
buttons in Kilix 95 to remove and re-add every status item and title-bar button.
Both interfaces update the single non-executable source of truth at
`~/.local/gpu_terminal/settings.conf` (override with
`GPU_TERMINAL_SETTINGS_FILE`); the GUI also edits `KILIX_CHROME_CLOCK_FORMAT`.

**Drag-to-split by quadrant** (Tilix's model): drag a pane by its title bar onto another
pane and drop on that pane's **top / bottom / left / right** triangle — a live half-pane
preview shows where it lands, and the target splits 50/50 in that direction (near side =
before, far side = after). Quadrants are bounded by the pane's true diagonals; dropping
on a maximized/stacked pane is rejected.

The buttons only exist in the **fork build** — the prebuilt fallback is a plain
(Tilix-themed) kitty with no buttons. See [Development](#development) for how the fork works.

## Pages (Tilix sessions)

Tilix groups panes into **sessions**; kilix maps each session to a kitty **tab** —
a "page" you flip between. The page strip (kitty's powerline tab bar) is always
visible across the top and ends with a clickable **`+`** to open a new page. You can
**drag a tab to reorder** it, press **`F12`** for a visual page chooser (kilix's
stand-in for Tilix's session sidebar), and **`F2`** to rename the current page.
Run `kilix ls` from inside kilix to list the live pages, their tab IDs, pane
counts, titles, and current working directories. The page
shortcuts are in [Keybindings](#keybindings-tilix-layout).

### Live tab and pane control

```bash
kilix ls                  # tabs/pages
kilix ls --panes          # individual pane IDs
kilix focus 45            # focus tab or pane 45
kilix focus pane:74       # disambiguate when needed
kilix watch 74            # poll pane 74 as read-only text
kilix watch --once 74     # one snapshot
```

These commands use kitty remote control against the current live GUI instance.
`kilix focus` can jump to a tab or pane; `kilix watch` is intentionally
read-only and polls `kitten @ get-text`, so it is useful for shell output and
simple full-screen programs but is not real multiplexing. It does not carry
graphics, mouse state, or a second interactive PTY. For true attach/view, start
the session under tmux with `kilix serve` or `kilix mux <name>`.

## Browse the web in a pane (experimental)

```bash
kilix browse wikipedia.org        # any URL or hostname (Ctrl+L bar also searches)
kilix browse --incognito site.com # throwaway profile: nothing survives the session
```

`kilix browse` renders **real Chrome inside the pane**: page pixels (images,
video, layout) stream in at full resolution via the kitty graphics protocol,
while **page text is drawn as live terminal glyphs** — crisp, and selectable
like any terminal text (shift+drag). Mouse clicks, wheel scrolling, and typing
are forwarded to the page, a software pointer tracks the mouse (headless
Chrome draws none; `--no-cursor` opts out), and hovering triggers real hover
effects. Normal sessions keep history/cookies in
`~/.local/gpu_terminal/kilix/state/browse-profile`; `--incognito` uses a throwaway profile
deleted on exit.

| Key | Action |
|---|---|
| `Ctrl+L` | edit the URL (bare words search DuckDuckGo) |
| `[<]` / `[>]` toolbar, `Alt+←` / `Alt+→` | history back / forward |
| `Backspace` | history back when the page is not editing text |
| `[R]` toolbar, `Ctrl+R` | reload |
| `Ctrl+C` | copy the mouse-drag selection (OSC 52 → clipboard) |
| `Ctrl+Q` | quit |

Requires `google-chrome`/`chromium`, Python 3, and Pillow. The default
`KILIX_BROWSE_BACKEND=presenter` implementation drives headless Chrome over the
DevTools protocol and updates one stable Kitty image through the shared
`kitty-frame-presenter` module. This avoids a visible image-plane gap between
full-frame replacements, uses exact damage and scroll composition, and works
with either the fork or prebuilt engine. The older built-in Go kitten remains
available as an explicit `KILIX_BROWSE_BACKEND=go` compatibility option on fork
builds. During sustained animation (video), the default renderer adaptively
halves capture resolution and lets the GPU scale it back, keeping CPU in check.
Known limits: no audio, no DRM video, and dense typography quantizes to the
character grid.

## Run a GUI app in a pane (experimental)

```bash
kilix run xterm                           # app screen = the pane's pixel size
kilix run --size 640x400 dosbox           # …or fix it (e.g. a DOS game's native res)
```

`kilix run` puts a real X11 app **inside the pane**: the app gets its own
private off-screen X server (Xvfb), its frames are streamed into the pane via
the kitty graphics protocol, and the pane's keyboard and mouse are forwarded
back with XTest — key *releases* included, so games can hold keys. It's
`kilix browse` generalized from Chrome to anything with an X window; think of
it as a tiling WM turned inside-out — the app's pixels come to the pane
instead of the WM arranging app windows. Proven by playing X-COM: UFO Defense
under DOSBox entirely through a pane.

**Tab-fill & scalable.** With no `--size`, the app's screen *tracks the pane*:
it starts at the pane's exact pixel size and a pane resize **resizes the
app's display** (RandR on the private Xvfb, debounced), refits the app window,
and restarts the capture — so GUI apps fill the tab 1:1 and re-tile with your
splits exactly like terminal programs. `--size WxH` pins the app resolution
(a DOS game's native res); the picture is then GPU-scaled and letterboxed
into the pane as before. `KILIX_RUN_MAX` (default `3840x2160`) caps how large
the pane-tracked display can grow.

**Efficient (event-driven, tiled updates).** XDamage wakes Kilix only when the
private X display changes, and MIT-SHM reads the damaged region without a
fixed-rate full-screen capture. Consecutive snapshots are reduced to exact
rectangles and composed onto one stable image with Kitty `a=f` frame edits.
Local pixels use a bounded three-slot POSIX shared-memory ring (`t=s`);
streamed sessions use compressed inline data (`t=d`). The Kilix Kitty fork
uploads frame edits with `glTexSubImage2D`, so a cursor, caret, or exposed
scroll strip no longer reallocates the full GPU texture. It can also shift an
overlapping region of the current frame for scrolling and upload only the
residual pixels. Full placements are reserved for startup, resize, and
recovery keepalives. `kilix browse` and `kilix desktop` use the same standalone
[`kitty-frame-presenter`](https://github.com/itsmygithubacct/kitty-frame-presenter)
module. Run `scripts/render_benchmark.py` for deterministic scroll, cursor,
video, idle-wakeup, frame-pacing, output-integrity, and bandwidth metrics.

| Key | Action |
|---|---|
| `F10` | toggle app-window auto-fit when enabled (for Steam/VM fullscreen tests) |
| `Ctrl+Q` | quit (everything else is forwarded to the app) |

Requires `python3-pil`, `python3-xlib`, and `Xvfb` with XDamage/MIT-SHM;
`ffmpeg` is retained as the capture fallback and is also used by broadcast
encoders. Dependencies can be on `PATH` or unpacked
without root into `~/.local/gpu_terminal/kilix/data/xvfb`:
`apt-get download xvfb && dpkg -x xvfb_*.deb ~/.local/gpu_terminal/kilix/data/xvfb`.
Python prototype (`config/apprun.py`). Known limits: no sound routing; apps
that grab the pointer (DOSBox's autolock) see relative motion, so the app
cursor and the pane cursor can drift; with `--size` or the broadcast tiers
(`--serve`/`--hls`/`--mse`/`--webrtc`) the app's screen size stays fixed at
launch — those pane resizes rescale the picture instead of the app.

**Their own window.** `browse` opens in a kitty **overlay window** — a pane
with its own title bar and a clickable close (`✕`) button — so closing the
app exits it and drops you back to the shell underneath. `run` opens in a
**new tab** (titled after the app), so the launching shell stays visible in
its own tab and closing the app's tab exits the app. Either way the shell
session is never taken over. This uses kitty remote control, which kilix's
config enables in password-policy mode with a per-instance `listen_on` socket.
The bundled policy permits only reload, font-size, and self-fullscreen without
a password. Launch/list/focus/watch use a private, locally generated credential;
uncredentialed launch/read/send/close requests remain denied. Override those
settings in your XDG `kilix/kitty.conf` and the app runs in-place in the current
pane instead.

## Screensaver

```bash
kilix screensaver            # matrix digital-rain (the default)
kilix screensaver matrix     # …or by name
```

Terminal screensavers live in `config/screensavers/` as small, self-contained
C programs. kilix compiles the one you ask for on first use (cached under
`~/.local/gpu_terminal/kilix/cache/screensavers`) and runs it in the current pane — press `q` or `Ctrl-C`
to quit. `matrix` is efficient green digital-rain: diff-rendered with one
synchronized write per frame, so it's a couple of percent of a core even
full-screen. Drop another `<name>.c` into that directory and
`kilix screensaver <name>` picks it up. Needs a C compiler (the same one the
fork build uses).

## Desktop — a Windows 95-style desktop in a tab (experimental)

```bash
kilix desktop                # opens "kilix 95" in a new kilix tab
```

`kilix desktop` is a versioned provider facade. The separate `kilix-95`
repository is the authoritative desktop. `auto` prefers an installed external
checkout; the bundled `desktop/` tree is an explicitly reported compatibility
fallback. Both must pass the same provider API, Kilix SDK, and security-feature
contract before execution (`kilix status` shows the selected provider).

```bash
KILIX_DESKTOP_PROVIDER=external \
KILIX95_AUTO_INSTALL=1 \
KILIX95_DIR=~/gpu_terminal/kilix-95 \
kilix desktop
```

By default the checkout is discovered as the sibling
`~/gpu_terminal/kilix-95`, while its writable XP desktop state (including its
wallpaper selection) stays under `~/.local/gpu_terminal/kilix-95`. The bundled
fallback keeps independent state under `~/.local/gpu_terminal/kilix`.

Relevant knobs: `KILIX_DESKTOP_PROVIDER=auto|builtin|external|command|none`,
`KILIX_DESKTOP_COMMAND`, `KILIX_DESKTOP_NAME`, `KILIX_DESKTOP_FLAVOR=95|xp`,
`KILIX95_DIR`, `KILIX95_REPO`, `KILIX95_BRANCH`, `KILIX95_REF`, and
`KILIX95_AUTO_INSTALL=1` to allow a missing external checkout to be cloned.
Automatic installs require `KILIX95_REF` to be a full immutable commit SHA;
mutable tags/branches require the explicit `KILIX95_ALLOW_MUTABLE_REF=1` trust
override. `kilix update` similarly honors `KILIX_REF` by fetching it from the
validated origin and checking out the resolved commit detached.
Direct updates and fork builds serialize on the private
`~/.local/gpu_terminal/kilix/state/build-update.lock`. An outer installer that
already holds this lock must pass its open, locked descriptor to Kilix as
`KILIX_TRANSACTION_LOCK_FD` (and preserve that descriptor across `exec`);
Kilix validates that it names the canonical lock before treating it as
reentrant. The resolved path is exported to children as
`KILIX_TRANSACTION_LOCK_PATH`.

![kilix 95 — the desktop with the media player, file manager and Notepad open](config/kilix95_with_amp.png)

![kilix 95 — from a shell to the desktop to Doom, all in kilix tabs](docs/kilix95-doom-demo.gif)

A full little desktop environment rendered as pixels in a kilix pane (same
graphics path as `browse`/`run`): teal wallpaper, desktop icons, overlapping
draggable/resizable windows, a start bar with a Start menu and clock, and a
right-click menu everywhere. Built in:

- **File Manager** — browse, open, rename, delete, new folder/file,
  properties, "open terminal here".
- **kilix Settings** — edits this user's private `kitty.conf`, `kilix.env`, and
  shared `~/.local/gpu_terminal/settings.conf` (GUI tabs for terminal, top-bar
  widgets, pane buttons, game availability, desktop, app, storage and
  build/update knobs, plus a raw `kitty.conf` editor). `kitty.conf` changes apply
  **live** via remote control (fallback: SIGUSR1); `kilix.env` changes are used
  by new launches.
- **Notepad** and an **image viewer**.
- **Games** — Start ▸ Programs ▸ Games. Each entry plays immediately if
  `~/.local/gpu_terminal/kilix-95/config/games.conf` already points at a working install, otherwise
  one consented click sets it up (paths saved to that file) and launches it in
  a tab: **Doom** downloads the official shareware episode plus a
  dosbox-staging build if no dosbox is installed (fullscreen, fire on Space,
  sound on); **Bashed Earth** clones + builds
  [itsmygithubacct/Bashed-Earth](https://github.com/itsmygithubacct/Bashed-Earth).
  The Games tab, `kilix settings`, and `kilix games enable|disable NAME...`
  all select which entries appear, using the root-level shared settings file.
- **Media Player** — Start ▸ Programs ▸ Media Player. The skin sits *directly
  on the desktop* with no kilix window frame (Winamp-on-Win95 style): an SDL2
  app on a private display whose background is chroma-keyed away, so only the
  skin composites onto the desktop — drag it by its own titlebar; clicks on the
  gaps fall through to the desktop icons. First run clones + builds
  [itsmygithubacct/kilix-amp](https://github.com/itsmygithubacct/kilix-amp),
  a Winamp 2.x clone, into `~/.local/gpu_terminal/kilix-95/data/apps`.
- **Create Launcher…** (Start menu or right-click the desktop) writes
  freedesktop-style `.desktop` files into the desktop folder
  (`~/.local/gpu_terminal/kilix-95/data/desktop`, override with `$KILIX_DESKTOP_DIR`); plain
  files and folders dropped there show up as icons too. Launchers open in a
  new kilix tab / OS window, through `kilix run` for X11 apps, or in
  `kilix browse` for URLs.

Quit via Start ▸ Shut Down… (or `Ctrl+Alt+Q`); the terminal underneath is
untouched. All artwork is drawn in code — no Microsoft assets are bundled.
Modules currently live in `desktop/` or an external `kilix-95` checkout. The
desktop draws its own Win95 mouse pointer (pass `--no-cursor` if you'd rather
not).

## Stream sessions to other devices (experimental)

kilix can share a session over the network so you can pick it up — or just watch
it — from another laptop, a phone, or a browser. There are three tiers, from
crisp-text-cheap to full-pixel-faithful. **All of them bind to loopback by
default** (reach them over SSH); LAN exposure is opt-in and gated by TLS + a
token. Everything here is *opt-in* — plain kilix is unchanged.

### 1. Text sessions — persist + attach from many devices

```bash
kilix serve            # start (or re-attach) a persistent session named "main"
kilix serve work       # …or a named one
kilix mux work         # open/create-or-attach that tmux session in a kilix tab
kilix mux a work       # same, explicit attach/create form
kilix attach work      # attach read-write (from anywhere, incl. over SSH)
kilix view work        # attach READ-ONLY (a safe way to let someone watch)
kilix serve ls         # list sessions   ·   kilix serve kill work
```

This runs a **private tmux server** (its own socket under the kilix runtime dir —
your `~/.tmux.conf` is never touched) that keeps the session alive across
disconnects and lets several devices attach at once. Text, colors, and inline
images (`kilix icat`) come through; kilix forces images to inline transmission so
they survive the hop. This is separate from top-level `kilix ls`, which lists
the live tabs in the current GUI instance. `kilix mux <name>` is a convenience
for opening a new GUI tab whose shell is born inside `kilix serve <name>`; it
creates the tmux session if missing or attaches it if already running, so it can
later be reattached or viewed. From another machine it's just SSH:

```bash
ssh -t you@host ~/gpu_terminal/kilix/kilix attach work     # take over
ssh -t you@host ~/gpu_terminal/kilix/kilix view  work      # watch, read-only
```

Needs `tmux`. Animated `browse`/`run` panes work but are throttled over tmux —
for those, use the pixel tiers below.

### 2. A GUI app — view + control from a browser or VNC client

`kilix run` can expose the app it's already running on its private display:

```bash
kilix run --serve xclock          # local pane + remote VNC (loopback)
kilix run --hls mpv-app           # fMP4-HLS broadcast (scales out, ~1.5-2.5 s)
kilix run --mse mpv-app           # MPEG-TS over WebSocket -> MSE (~0.3-1 s)
kilix run --webrtc mpv-app        # WebRTC via MediaMTX (sub-500 ms)
kilix run --mse --audio mpv-app   # …any of them + the app's audio (AAC)
kilix run --lan  --size 1024x768 someapp   # expose on the LAN over HTTPS+token
kilix run --no-pane --mse cmd     # headless: network tiers only (e.g. over SSH)
```

`--serve` swaps the app's off-screen display for **Xvnc**, so the same picture
your local pane shows is available to remote devices with native view **and
control** (VNC/Tight is also the most bandwidth-efficient tier for terminal
content). The broadcast tiers are view-only and combinable: **--hls** for many
viewers behind dumb caches, **--mse** for ~half-second latency in any browser
(vendored mpegts.js), **--webrtc** for the lowest latency (MediaMTX,
auto-downloaded on first use; viewers authenticate as `kilix` + the printed
token). On launch kilix prints ready-to-paste connect lines — an SSH tunnel
for a native VNC viewer, a browser URL (bundled **noVNC**, no install), the
`/watch` low-latency page, and an `mpv`/HLS watch URL. Two VNC passwords are
minted: a **control** one and a **view-only** one (the server enforces the
difference).

With a local pane, every encoder is fed from the pane's event-driven capture;
an idle XDamage source does no polling work and static screens cost almost
nothing on the wire. The ffmpeg fallback uses one x11grab total and drops to
2 fps when idle. `KILIX_HW=1` prefers
VAAPI hardware encoding when a render node exists; `--debug` overlays
capture/blit fps + wire bandwidth and logs `metrics.jsonl`, and
`scripts/stream-stats.sh <url>` measures what a viewer actually receives.

### 3. The whole kilix — every pane, graphics and video included

```bash
kilix share                       # whole kilix on a headless screen -> your browser
kilix share --size 1600x900 --lan
kilix share --audio --debug       # desktop audio in the stream + encode metrics
```

(*Renamed from `kilix desktop` when the [desktop environment](#desktop--a-windows-95-style-desktop-in-a-tab-experimental) claimed that name.*)

This runs the *entire* kilix (all panes, splits, `browse`/`run` video, images) on
a headless display and streams the composited picture as **H.264/HLS** to any
number of browsers or players, with keyboard/mouse control forwarded back. Since
it ships pixels, this is the only tier that carries graphics **and** video with
full fidelity to any device. It prints a bold warning — it shares your whole
desktop — and, like the others, stays on loopback unless you pass `--lan`.

**Requirements for the pixel tiers:** `ffmpeg` (libx264, libopenh264 or
h264_vaapi — auto-detected; `x11grab`), `Xvfb`, `python3-xlib`, and the
`websockets` Python module; `Xvnc` (TightVNC/TigerVNC) only for `--serve`;
`pactl` (PulseAudio/PipeWire) only for `--audio`; `openssl` for `--lan` TLS.
First use vendors noVNC, hls.js, mpegts.js and (for `--webrtc`) the MediaMTX
binary into `~/.local/gpu_terminal/kilix/data/` (one-time network). The implementation
rationale is captured in the nearby source comments and regression tests.

## Keybindings (Tilix layout)

| Action | Shortcut |
|---|---|
| Split right (side-by-side) | `Ctrl+Alt+R` |
| Split down (stacked) | `Ctrl+Alt+D` |
| Split (auto orientation) | `Ctrl+Shift+Enter` |
| Close pane | `Ctrl+Alt+W` |
| Focus pane ↑ ↓ ← → | `Alt+Arrows` |
| Resize pane | `Ctrl+Shift+Arrows` |
| Increase / decrease terminal scale | `Ctrl+Shift+=` / `Ctrl+Shift+-` |
| Reset terminal scale | `Ctrl+Shift+Backspace` |
| Move/swap pane | `Ctrl+Alt+Arrows` |
| Zoom/maximize pane (toggle) | `Ctrl+Alt+Z` |
| Broadcast input to all panes in page | `Ctrl+Alt+B` |
| Cycle layout | `Ctrl+Alt+L` |
| Next / previous pane in page | `Ctrl+Tab` / `Ctrl+Shift+Tab` |
| New page (session) | `Ctrl+Shift+T` |
| Close page | `Ctrl+Shift+Q` |
| Next / previous page | `Ctrl+PgDn` / `Ctrl+PgUp` |
| Reorder page right / left | `Ctrl+Shift+PgDn` / `Ctrl+Shift+PgUp` |
| Jump to page 1–10 | `Ctrl+Alt+1` … `Ctrl+Alt+0` |
| Page chooser (Tilix sidebar) | `F12` |
| Rename page | `F2` |
| Content-only fullscreen (hide page strip and pane chrome) | `F11` |
| Toggle this tab's OS window fullscreen from a shell | `kilix fullscreen` |
| New OS window (same dir) | `Ctrl+Shift+N` |

## Taskbar identity & icon

kilix launches kitty with `--class kilix`, so its windows get their own
`WM_CLASS`/`app_id` and **group separately from any plain kitty instances** in your
taskbar/dock. It sets `KITTY_CONFIG_DIRECTORY` to the XDG Kilix config directory;
its generated `kitty.conf` includes tracked defaults from `./config` and keeps
user overrides outside the checkout.

- **On X11**, the window icon is the config-dir `kitty.app.png` / `kitty.app-128.png`
  (the kilix "kitty-on-fire" icon) — it works even without installing anything.
- **On Wayland**, the window icon is resolved from the installed `kilix.desktop` by
  `app_id`, so you must run `kilix --install-desktop` to get the themed icon.

`kilix --install-desktop` installs `kilix.desktop` (with `StartupWMClass=kilix`) and
the icons into `~/.local/share`, so kilix appears in the app menu and the taskbar
shows its icon instead of kitty's. Log out/in or restart your panel if the icon
doesn't appear immediately (icon caches are lazy).

## Troubleshooting

- **No buttons / plain title bars?** You're on the prebuilt fallback, not the fork.
  Run `kilix --which` — if it prints a `…/kitty.app/bin/kitty` path, install the build
  deps (see [Requirements](#requirements)) and run `kilix --build`, then relaunch.
- **Taskbar shows kitty's icon, not the flame?** Run `kilix --install-desktop`, then log
  out/in or restart your panel. On **Wayland** the icon comes only from the installed
  `.desktop`, so `--install-desktop` is required there.
- **`kilix` exits with no window / over SSH?** It's a GUI terminal and needs a local
  graphical session (`$DISPLAY` / `$WAYLAND_DISPLAY`); it won't run headless.
- **First run spews a compile and fails?** That's the fork build failing for lack of
  deps — Kilix then attempts the prebuilt fallback. Supply a pinned version and
  SHA-256 to `bootstrap.sh` to skip the build attempt entirely.

## Uninstall

```bash
rm -rf ~/gpu_terminal/kilix                          # project source
# Optional: remove settings/state only if you do not want to preserve them:
rm -rf "$HOME/.local/gpu_terminal/kilix"
# only if you ran --install-desktop:
rm -f  ~/.local/share/applications/kilix.desktop
rm -f  ~/.local/share/icons/hicolor/*/apps/kilix.png
update-desktop-database ~/.local/share/applications 2>/dev/null || true
gtk-update-icon-cache -f ~/.local/share/icons/hicolor 2>/dev/null || true
```

## FAQ

- **Why a fork of kitty?** Stock kitty can't put clickable buttons in its window chrome,
  so kilix ships a fork (the `./src` submodule) that wires title-bar clicks to kitty
  actions. It's a full fork kilix evolves freely — the buttons plus quality-of-life fixes.
- **Does it touch my normal kitty?** No. kilix runs its own binary, its own XDG
  config directory, and its own `--class`, so your system kitty
  and `~/.config/kitty` are untouched.
- **Does it work on Wayland?** Yes — splits, buttons, and keybindings all work; only the
  icon mechanism differs (see [Taskbar identity & icon](#taskbar-identity--icon)).
- **Performance?** It's kitty — GPU-rendered, same speed. The buttons are drawn into the
  existing title-bar cells, so there's no extra overhead.
- **Windows/macOS?** No — Linux only (see [Requirements](#requirements)).

## Development

`./src` is a submodule of the
[kitty fork](https://github.com/itsmygithubacct/kitty/tree/clickable-chrome)
(branch `clickable-chrome`). It's a **full fork** — kilix keeps whatever changes make the
best experience. The clickable-button feature is these Python files:

- `kitty/window_title_bar.py` — draws `+ - ← ↑ ↓ → ▢ ✕` in each pane title bar,
  recording which cells map to which kitty action.
- `kitty/kilix_battery.py`, `kitty/tab_bar.py`, and `kittens/kilix_clock/` —
  draw the clickable network/date/time status and its NetworkManager,
  calendar/date widgets, and read the Linux battery status for the conditional
  battery item at the far right of the page strip.
- `kitty/tabs.py` — `handle_window_title_bar_mouse` dispatches a button's action on a
  single left-click (`boss.combine`), double-click toggles maximize, and the quadrant
  drag-to-split hit-test uses the pane's true diagonals (rejecting drops on a maximized
  pane). kitty ≥ 0.47 already ships the drag-split machinery; the fork only refines the
  hit-test and adds the maximized-target rejection.

The buttons reuse kitty's existing window-title-bar → Python click routing. The fork also
carries quality-of-life fixes on top — e.g. `glfw/linux_notify.c` raises the DBus
notification-server probe timeout to silence a spurious "Notify NoReply" warning at
startup. Branch history: clickable chrome, double-fire fix, DBus-warning fix.

`./third_party/kitty-frame-presenter` pins the independently tested Python
presentation library used by the browser, app panes, and desktop provider.
Keep capture, terminal input, and application policy in Kilix; reusable damage,
transport, composition, and pacing changes belong in that module first.

**Build / rebuild:** `kilix --build` (or `./build.sh`). Needs Go ≥ 1.26,
Python ≥ 3.12, plus the
system build deps from [Requirements](#requirements). The binary lands at
`~/.local/gpu_terminal/kilix/build/current/src/kitty/launcher/kitty`. The build
uses an exact committed-source snapshot, refuses a dirty `./src`, and records
that commit as `source-id`, so generated objects and binaries never land in
`./src`. Put a machine-specific toolchain environment in
`~/.local/gpu_terminal/kilix/config/build.env`. Go package
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
~/gpu_terminal/kilix/
├── kilix              # launcher (this is what you run)
├── kilix-settings     # shared chrome/game settings TUI
├── build.sh           # builds the forked kitty in ./src
├── bootstrap.sh       # pulls the prebuilt kitty (fallback engine)
├── config/            # kitty.conf + kilix icons (kitty.app*.png, kilix-512.png)
├── desktop/           # the "kilix 95" desktop environment (kilix desktop)
├── src/               # tracked kitty fork; remains clean after builds
├── third_party/       # pinned shared presenter submodule
├── README.md
├── LICENSE            # GPLv3 (kitty is GPLv3)
└── .gitignore
```

## Tweaks

Use Start ▸ Settings in kilix 95, or edit
`~/.local/gpu_terminal/kilix/config/kitty.conf`. It includes the tracked
`config/kitty.conf` defaults; add overrides to the user file.

Use `kilix settings` for clickable chrome and Kilix 95 game availability.
Volume, network, calendar, date/time, battery, font-size, four-way split,
maximize, close, and game toggles all live in
`~/.local/gpu_terminal/settings.conf`, which Kilix, Kilix 95, Pleb, and
Plebian-OS share.

The TUI separates Top bar, Pane buttons, and Games. Switch sections with
Left/Right, `h`/`l`, Tab/Shift-Tab, or `1`–`3`; use lowercase `a`/`n` for all
items in the current section and uppercase `A`/`N` for every setting. Run
`kilix games settings` to open Games directly.

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

## License

kilix is **GPLv3** — see [`LICENSE`](LICENSE). It embeds and builds on a fork of kitty,
which is GPLv3, so the whole project is GPLv3.

## Credits

- **kilix** by *itsmygithubacct*.
- `./src` is a fork of [kitty](https://github.com/kovidgoyal/kitty) by Kovid Goyal
  (GPLv3), modified to add clickable pane-title-bar buttons.
