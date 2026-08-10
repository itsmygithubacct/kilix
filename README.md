# kilix — kitty that looks & behaves like Tilix, with clickable pane buttons

`kilix` is a self-contained wrapper around a **fork of kitty** that gives
each pane's title bar clickable **keyboard, `+ - ← ↑ ↓ → ▢ ✕` buttons** —
synchronized input, local text size, four-way splits, maximize, and close —
just like Tilix's pane headers, on top of kitty's GPU-rendered speed. For Tilix
users who want kitty underneath, and anyone who wants clickable pane chrome on
kitty.

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

## Release 0.1.8

Prepared 2026-08-07. **Not published yet.**

Upgrades from 0.1.7, the previous published release. Adds `kilix install`: one
list of everything this system can install — the pinned content catalog and the
coding agents together — with `--update`, `--json`, and entries in both the
Kilix TUI desktop and the Kilix 95 Start menu that drive that same command.

Adds the host verbs every desktop provider was reimplementing (SDK 1.8):

- `kilix games play GAME [--setup-only]` — install-and-boot by catalog id,
  backed by the same content module as `kilix install`; desktops delegate here
  instead of shelling into each other's checkouts.
- `kilix launcher` — the launcher catalog TUI (stack programs, discovered XDG
  applications, the user's `.desktop` launchers, stack scripts, a
  run-a-command row), installed on first use from the pinned kilix-tui-utils.
- `kilix power logout|reboot|poweroff` — the frozen session/power argv list,
  no prompt and no UI: desktops own their confirm UX. `kilix status` reports
  availability.
- `kilix update --stack` — the one blessed stack-update surface: runs
  `plebian-os-update`, else `pleb update`, else updates kilix alone.
- `kilix security password-status` — one line + exit code 0 only when the
  login password is confirmed to still be the shipped default, so every
  desktop can nag idiomatically.
- `kilix laptop list|open PROFILE|status|close PROFILE` — the laptop session
  profiles every desktop's laptop object reads
  (`~/.local/gpu_terminal/laptop/<id>.profile`), opened and closed host-side
  with a shared run registry (`run/<id>.pid`, real process-liveness checks),
  so a session opened from any desktop shows as running — and can be closed —
  from every other.
- `kilix_sdk.xdgapps` — the freedesktop `.desktop` scanner Kilix 95's Start
  menu always had, promoted into the SDK for every consumer (SDK 1.7 → 1.8);
  now also the one authored source of the copy the TUI stack mirrors, gaining
  the mirror's `entries_in()` folder reader and `grouped(force=)` cache
  refresh (SDK 1.8 → 1.9).

Two more places a choice or a device is now reachable by name:

- `kilix default-desktop show|list|set NAME` — the desktop a session starts
  with, kept in one place and written by one writer. The desktops and the
  first-boot provisioner call it instead of each formatting the same line into
  the same file; an unknown name is refused with the list rather than written
  through.
- `kilix volume` — resolves the mixer this stack ships the way `kilix launcher`
  resolves its own tool: an installed command first, the pinned installer if
  the utilities are not there yet, a clear refusal rather than a guess. The
  chrome's volume widget and the launcher both ask for it by name.

### Fixed by the 0.1.7 review

Everything below came out of running 0.1.7 by hand on two machines rather than
from a test that was already failing.

- **Dictation refused every ordinary shell prompt.** The hidden-prompt guard
  treated "echo off" alone as a password prompt, so the microphone was refused
  in any readline-style shell at click time and again at delivery. A hidden
  prompt is canonical mode with echo off — the kernel reading a line it never
  shows — and only that is refused now. Both deliver paths are driven over a
  real pty in the chrome tests: a readline-state pane receives the transcript,
  a `getpass`-state pane still visibly discards it.
- **`TERM=xterm-kitty` resolved nowhere.** Every pane advertises it, but the
  terminfo entry existed only inside the engine build tree, so strict ncurses
  programs reported an unknown terminal until someone exported `TERM=linux` by
  hand. Promotion now installs the tree's compiled entry into `~/.terminfo`
  (both database layouts, compiling the shipped source with `tic` when no
  compiled entry exists) inside the same transaction: an engine whose terminfo
  cannot be installed is not promoted.
- **The voice tooling contradicted itself.** `kilix voice doctor` probed bare
  `PATH` and printed "kilix-voiced: not found" one line under "daemon:
  running"; the lazy daemon path reinstalled the pinned closure on every start
  from a `PATH`-less context; and a read-aloud-only run treated a full
  install's stamp as stale, reinstalling forever while telling a user with
  working dictation to rerun without `--without-dictation`. Doctor now reports
  the tool the launch paths actually resolve, resolution runs an installed
  prefix entry instead of reinstalling it, and a full stamp of the same pins
  satisfies the read-aloud-only check.
- **`~/.local/bin` was on no execution path in the stack.** Kilix panes run
  non-login bash, so Debian's `~/.profile` addition never applied and a tool
  installed at the user prefix was "command not found" in the very terminal
  that installed it; a launcher verb trusting `PATH` spawned a childless tab
  that died with no words at all. `kilix.bashrc` now makes that guarantee
  idempotently for every pane shell, `rollout-resume` became a real verb
  resolved at the installed prefix, and `volume`, `switch`, `bonsai`, `mask`, `rtsp` and
  `status` check the prefix before giving up or reinstalling. A missing tool
  fails with the path named, never a corpse.
- **The coding agents read as absent where their own installers put them.**
  Resolution now checks the known landing spots after `PATH` — the same
  contract the rollout tool follows — and `--update` runs the resolved binary
  absolutely. A vendor install must leave something that actually resolves
  before success is claimed.
- **The agent definitions had already drifted.** `kilix install` carried its
  own copy of each agent's install command, update argv and documentation URL,
  and said `kimi update` where Kimi wants `kimi upgrade`. They are taken from
  the rollout checkout when it is present, and the local fallback is pinned
  field by field against it. The search honours `KILIX_TUI_UTILS_DIR` — the
  installer's own override — instead of guessing the default clone location
  and silently falling back.
- **Two games in every desktop's menu died wordlessly.** `minesweeper` and
  `solitaire` are windows of the bundled desktop rather than catalog content,
  so the games backend fell into the unknown-game `SystemExit`, which flew out
  of `main()` and took the tab with it. They are known by name now, always
  ready, and boot the bundled desktop with the window already open.
- **A moved component pin reached only fresh machines.** The installers
  downloaded to the resolved ref on first use but reinstalled an existing
  checkout from whatever it happened to hold, so an update was exactly where a
  moved pin did not arrive. Both paths resolve the same ref under the existing
  immutable-SHA validation, the move is reported (`advanced`, or `REWOUND`
  when the pin is older than what is installed), a checkout with local
  modifications is kept and says so, and `*_KEEP_EXISTING_CHECKOUT=1` keeps a
  clean one deliberately.
- Installs `cmake` with the build toolchain, which the CPU model runtime needs.

### Component pins moved for this release

Kilix owns the immutable revision of every optional component, so these are the
whole of what a release installs:

- **Kilix Voice** → `eda9ca90eed677fa4fca383e7b8ad2fc85e54b0e`: the microphone
  test explains a suspended source over its level meter instead of showing a
  flat bar and saying nothing.
- **Kilix Bonsai** → `6550fb37b323ffd6d89072ce0b1cd254dc50fbfe`: an accepted
  CPU build offer actually builds — the store preflights the toolchain, prices
  the missing packages into the confirm, and reopens chat afterwards, while
  `bonsai-cpu` refuses a doomed build before fetching anything.
- **kilix-tui-utils** → `467e311d115a409b07e02660d7185ed71729852c`:
  `plebian-os --version` answered by crashing on a machine with no screen and,
  once it answered, read only the development workspace — so a provisioned
  machine's components read as "not present" or as the wrong version entirely.
  It answers without curses now and names the directory every answer came
  from. Transcript labels fit the terminal's real width and shrink from the
  head, so panes that differ only in their last argument stop rendering
  identically. Launches resolve their program before the terminal spawns it.
- **Kilix Cap** and **Kilix Land** advance to their published tips for the
  mansion's laptop, ladders, breaker and authored face, and the walkable
  house's fixes.

## Release 0.1.7

Prepared 2026-08-02. **Not published yet.** The source closure is final and
this repository's `VERSION` reads 0.1.7, but no 0.1.7 artifact or release tag
has been published: publication requires an ISO built from this exact closure
and accepted under Plebian-OS's
[RELEASING.md](https://github.com/itsmygithubacct/plebian-os/blob/main/RELEASING.md)
procedure. Until that lands, **0.1.2 remains the last published coordinated
release**, and the newest tag on this repository is the Kilix-only `v0.1.4`
described below. Read the sections that follow as what 0.1.7 contains, not as
something you can install today.

Version 0.1.7 is the coordinated stack release covering everything since 0.1.2,
and is the first release Kilix shares with
[Plebian-OS](https://github.com/itsmygithubacct/plebian-os),
[Pleb](https://github.com/itsmygithubacct/pleb), and
[Kilix 95](https://github.com/itsmygithubacct/kilix-95) since then. It adds
crash-persistent panes backed by `kitty-pty-broker`, an explicit capture
readiness protocol with an XDamage first-paint fix, and live-generation
preservation so long-running terminals survive a rebuild. It also turns on
[session logging](#session-logging-on-by-default) by default: the same broker
that owns each pane's PTY records that pane's output to a bounded, private
transcript, with kitty graphics payloads elided so a pixel desktop cannot flood
the log. SDK 1.5 adds the shared session-logging settings contract used by both
SDK-backed Python providers, including the two directory budgets that bound the
transcript tree: dead-pane logs are compressed promptly, older ones are
recompressed more densely, and history is dropped only when both budgets are
full.

It also adds **read-aloud and dictation**: two top-bar widgets over a local
speech stack, with capture click-to-talk and never listening on its own. SDK 1.6
is the shared voice settings contract behind them — engines, voice, rate,
extent, devices, and a dictation history that is off by default, because a
record of what the user said is a different privacy class from a record of what
the terminal printed. The desktop facade gains native optional providers for
the **Kilix Cap** graphical mansion, the **Kilix TUI** text-native desktop, and
the **Kilix Land** walkable desktop, each inherited through an immutable
Kilix-owned first-install pin. Rounding out the release: **Tmux Manager** and
the `tb` command from the pinned
`tmux-tui`/`tmux-cli` closure, the **pane memory chip** and its monitor launcher,
and SDK 1.7's shared coding-agent policy — the setting that decides whether a
resumed coding agent starts with its own approval prompts disabled. Page
switching also returns to `Ctrl+Shift+←`/`→`, kitty's own default cluster, with
pane resize moving to kitty's interactive resize mode on `Ctrl+Shift+R`; and a
new [`Ctrl+Shift+B` leader](#tmux-style-leader--ctrlshiftb)
puts tmux's pane and window keys on Kilix's panes and pages without shadowing the
real tmux prefix that `kilix serve` depends on. `F12` now opens
[a real switcher](#going-to-a-page-or-a-pane--f12) — one tree of pages and panes
with each pane's process, directory and live screen — in place of the numbered
title list kitty ships; the scoped remote-control credential gains
`close-window`, `close-tab` and `set-tab-title` so it can act on what it lists,
and still admits nothing that can type into a pane. Release tags for this
repository are created only by the coordinated
release procedure — see Plebian-OS's
[RELEASING.md](https://github.com/itsmygithubacct/plebian-os/blob/main/RELEASING.md).
0.1.7 is the fresh-install upgrade baseline. Older installations are
reinstalled; later coordinated releases must support a preserving upgrade from
the immediately previous published release under Plebian-OS's
[upgrade policy](https://github.com/itsmygithubacct/plebian-os/blob/main/UPGRADING.md).

Web URLs now go through one explicit dispatcher: Kilix prefers an installed
Chrome, Chromium, or Firefox browser and uses the experimental in-pane renderer
only when none is available. Kilix 95 consumes the same dispatcher, so desktop
links and `kilix open-url` follow one visible policy.

> The `0.1.3` and `0.1.4` sections below are **component milestones**, not
> shipped stack releases: they mark the SDK contract levels those changes
> introduced. No Plebian-OS image was ever built or published for either. The
> `v0.1.4` tag on this repository predates the current rule and is a Kilix-only
> tag; it is left in place rather than moved. `0.1.5` was prepared as a
> coordinated release and never cut. `0.1.6` was an unpublished failed
> candidate; all of that work is folded into 0.1.7.

## 0.1.4 — SDK 1.4

Version 0.1.4 adds Tilix-style synchronized keyboard input, including
whole-tab double-click selection, and the configurable per-pane memory chip.
The new pinned Kilix Memory dashboard provides graphical and text views of
RAM, swap, pressure, paging, and process use. SDK 1.4 adds the shared native
state binding and keeps the built-in and standalone Kilix 95 providers on the
same declared contract.

## 0.1.3 — SDK 1.3

Version 0.1.3 ships the Kilix 1.3 provider SDK. A shared immutable content
catalog now drives both SDK-backed Python providers, while `XAppSession` owns
private X display authentication, application/capture processes,
XDamage-to-ffmpeg fallback, input injection, and teardown. These boundaries
keep provider code focused on presentation and make every catalog checkout
recursive, pinned, verified, and atomically selected. SDK 1.2 also gives the
providers and both settings interfaces one game-availability contract. SDK 1.3
adds the shared volume-widget setting used by Kilix, Kilix 95, Pleb, and
Plebian-OS.

## Release 0.1.2

Version 0.1.2 standardizes source checkouts under `~/.local/gpu_terminal/sources`, keeps all
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

## Watch the episodes

**Part two — Kilix: pages, panes, and clickable chrome** (1920×1080, 3m32s,
[full file](https://github.com/itsmygithubacct/kilix/releases/download/media-v1/02-kilix-pages-panes-and-chrome.mp4)):

https://github.com/user-attachments/assets/b4d35ed1-4eb3-4184-92f3-34b50cc385bf

**Part four — How applications stay inside Kilix** (inline player is a 720p preview;
[full quality](https://github.com/itsmygithubacct/kilix/releases/download/media-v1/04-applications-inside-kilix.mp4)
is 1920×1080, 3m23s, 33 MB):

https://github.com/user-attachments/assets/4cdd423e-4958-477d-8a85-b78049c46610

Both are parts of *Kilix, Pleb, and Plebian-OS: A Desktop Built Inside a Terminal*, published as a
[media release](https://github.com/itsmygithubacct/kilix/releases/tag/media-v1) so a clone stays
small. The [full series](https://github.com/itsmygithubacct/plebian-os#watch-the-series) (31m22s)
lives on `plebian-os` and plays at [plebian-os.com](https://plebian-os.com/#watch).

## Features

- **Clickable pane buttons** keyboard, `+ - ← ↑ ↓ → ▢ ✕` — synchronized
  keyboard input, local font size, four-way split, maximize, and close controls
  that highlight on hover.
- **Crash-persistent panes** — each normal pane runs behind its own independent
  `kitty-pty-broker` process. A Kilix crash or ordinary frontend loss detaches
  the pane without killing its shell; the next Kilix process restores detached
  panes as recovered tabs. `exit` ends the session naturally, while the pane
  `✕` warns before deliberately terminating it.
- **Thermometer-in-chrome** — an optional hottest-sensor indicator opens the
  graphical Kilix Temps dashboard in a new tab. It is green below 80°C, yellow
  at 80–89°C, and red from 90°C; the shared setting defaults to off.
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
- **Kilix Cap** — an optional full-color mansion desktop (`kilix cap`) with
  physical app launchers, live system rooms, housekeeping, and the Kilix 95
  game catalog.
- **Kilix TUI** — an optional text-native desktop (`kilix tui`) whose
  complete control surface works over SSH, tmux, or a bare console and gains a
  graphical Tango rendering when Kitty graphics are available.
- **Kilix Land** — an optional walkable graphical desktop (`kilix land`) with
  an immutable Kilix-owned source pin and first-use native build.
- **Kilix IceWM** — a pinned IceWM desktop provider (`kilix icewm`) rendered
  through Kilix's private X application surface, with catalog apps opened as
  ordinary desktop windows.
- **Host SDK for Python desktops** — Kilix 95 and the bundled compatibility
  provider import stable helpers from `config/kilix_sdk` instead of depending
  on raw `config/browse.py` / `config/gfx.py` internals. SDK 1.2 includes shared
  content installation, authenticated private-X-application sessions, and game
  availability. SDK 1.10 adds catalog application plans for current-terminal,
  pane, and desktop-window presentation; SDK 1.11 exposes shared package and
  install identities from content schema 2. Native executable, TUI, and
  command providers use the launcher’s executable/pin boundary instead.
- **Self-contained** — prefers its bundled fork build, and falls back to a prebuilt kitty if you haven't built it.

### Persistent pane processes

Kilix builds its pinned
[`kitty-pty-broker`](https://github.com/itsmygithubacct/kitty-pty-broker)
submodule on demand (a sibling checkout remains a development fallback) and
places its artifacts below the Kilix build directory. The broker owns the real
PTY and process group; Kilix attaches through a lightweight client over a
private Unix socket. Terminal bytes remain untouched, so live Kitty graphics,
including file and POSIX shared-memory transfers, do not pass through a tmux
parser.

### Session logging (on by default)

Because the broker already owns every pane's PTY, it also records that pane's
output to a durable log — including for a pane that is detached, recovered, or
whose frontend crashed. Logs live in `~/.local/gpu_terminal/kilix/state/transcripts/`
as `<session-id>.log`, mode `0600`, one per pane.

```bash
kilix transcript                  # newest-first index, live and archived
kilix transcript show <session>   # write one transcript to stdout
kilix transcript path             # print the directory
kilix transcript prune            # apply the size budgets now
kilix transcript archive          # move dead logs into the denser older tier
```

Each log is bounded (8 MiB by default); on overflow the newest three quarters
are kept and the oldest bytes are dropped, so a busy pane cannot fill the disk.

That cap bounds one file, so the **directory** has its own two budgets. A log is
plain only while its pane is live. Within a minute after the pane exits, the log
is compressed with `zstd -3` into
`transcripts/recent/<session-id>.log.zst`; the recent tier is 5 GiB by default.
When that tier fills, the oldest transcripts are recompressed with `zstd -9`
into `transcripts/archive/<session-id>.log.zst`, up to a second 1 GiB budget.
Only when both tiers are full are the oldest archives dropped. A live pane's log
is never touched — the broker holds that descriptor. The budgets are enforced
periodically while the Kilix frontend runs, and on demand with `prune`. Set the
older tier to `off` for a hard ceiling at the recent-tier budget. The 6 GiB
combined default fits the release image's 20 GiB minimum disk while leaving the
larger presets available to operators with room for more history.

Reading an archived transcript is the same command: `kilix transcript show`
decompresses transparently, and `kilix transcript path` resolves to whichever
tier a session ended up in, so nothing needs to know where a log lives. Archiving
is lossless — an archived transcript reads back byte-for-byte identical.
Kitty **graphics payloads are elided** by default — a pane running Kilix 95,
`browse`, `run`, or `icat` emits base64 pixel data at megabytes per second, and
recording it verbatim would evict every readable line within seconds. The log
keeps a `[kitty-pty-broker: N bytes of graphics elided]` marker instead, which
makes a default transcript a faithful record of *text*, not a byte-exact
capture. Choose `keep` when the graphics bytes are themselves the subject.

Only pane **output** is recorded. Input appears solely through terminal echo,
so a password prompt that disables echo is not captured.

Turn it off, or change its two knobs, from any settings interface — they all
write the same shared file:

```bash
kilix settings                            # TUI, "Session logging" section
kilix settings --set transcript=off
kilix settings --set transcript_size=32M          # 2M | 8M | 32M | 128M
kilix settings --set transcript_graphics=keep     # elide | keep
```

Closing the whole frontend, closing a page, or a Kilix crash detaches the
client. Detached sessions are discovered on the next startup and opened in
`recovered:<id>` tabs. The per-pane `✕` and `Ctrl+Alt+W` are the explicit
destructive path and always ask before asking the broker to terminate that
pane. Set `KILIX_PTY_BROKER=0` to disable persistence,
`KILIX_PTY_BROKER_AUTO_RECOVER=0` to leave detached sessions for manual
attachment, or `KILIX_PTY_BROKER_JOURNAL_LIMIT` to change the bounded replay
journal from its 64 MiB default.

Run `kilix pty` to open the interactive session manager. It lists detached and
attached panes, attaches a selected detached pane with Enter, and offers an
explicitly confirmed termination action.

## Requirements

- **Linux only**, x86_64 or arm64 for the prebuilt engine. The clickable-chrome
  fork build currently supports x86_64. (No macOS/Windows.)
- A running graphical session — **X11 or Wayland** (`$DISPLAY` or `$WAYLAND_DISPLAY`).
- NetworkManager's **`nmtui`** for the clickable network/Wi-Fi item. Pleb and
  Plebian-OS install it; standalone Kilix shows an explanatory error if it is
  unavailable.
  It's a GUI terminal; it won't run headless / over plain SSH.
- **`zstd`** for compressing dead-pane transcripts and enforcing their
  directory budgets. The dependency installer below includes it.
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
- **For read aloud:** `espeak-ng` plus `pacat`, `paplay`, or `aplay`. **For
  dictation:** x86_64, `parec` or `arecord`, and the pinned local Vosk closure
  installed by `kilix voice install`. Run `kilix voice doctor` to see the exact
  missing engine, device, library, or model without opening the microphone.
- **For the pixel desktop and the no-real-browser in-pane fallback**
  (`kilix desktop` / `kilix open-url`):
  **Python 3 + Pillow** (also installed by `scripts/install-build-deps.sh`).
- kitty **≥ 0.47** (the fork is 0.47.x) — required for the per-pane title bars.

## Quick start

```bash
mkdir -p ~/.local/gpu_terminal/sources
git clone --recursive https://github.com/itsmygithubacct/kilix.git ~/.local/gpu_terminal/sources/kilix
~/.local/gpu_terminal/sources/kilix/kilix
```

(`--recursive` pulls the Kitty fork and the pinned
`kitty-frame-presenter`, `kilix-content`, and `kilix-state` submodules.
Cloned without them? Run
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
~/.local/gpu_terminal/sources/kilix/bootstrap.sh
~/.local/gpu_terminal/sources/kilix/kilix          # run it (no buttons until you build the fork)
```

The version and checksum are mandatory unless a user explicitly passes
`--allow-unverified` after reviewing the printed release URL:

```bash
KILIX_PREBUILT_VERSION=0.47.0 \
KILIX_PREBUILT_SHA256=<sha256-of-kitty-txz> \
~/.local/gpu_terminal/sources/kilix/bootstrap.sh
```

To get the buttons, install the build deps and build the fork:

```bash
~/.local/gpu_terminal/sources/kilix/scripts/install-build-deps.sh   # Go + X11 dev libs + Python/Pillow
~/.local/gpu_terminal/sources/kilix/kilix --build                    # compile the clickable-chrome fork
```

(`scripts/install-build-deps.sh --verify` re-checks without installing.)

Then, optionally:

```bash
~/.local/gpu_terminal/sources/kilix/kilix --install-desktop   # app-menu entry + taskbar icon
```

To pull the latest kilix into your checkout:

```bash
kilix update                      # verified fast-forward in ~/.local/gpu_terminal/sources/kilix
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
kilix settings --section tools    # memory monitor, Tmux Manager, or tb installer
kilix transcript                  # list recorded pane session logs
kilix transcript show <session>   # print one pane's transcript
kilix games list                  # show games available in Kilix 95
kilix games settings              # open the TUI directly on Games
kilix games disable doom          # hide a game (enable reverses it)
kilix pty                          # manage, attach, or terminate persistent panes
kilix temps --graphics            # install/verify the pinned dashboard, then run it
kilix memory --graphics           # install/verify the pinned monitor, then run it
kilix tmux                         # install/verify the pinned Tmux Manager, then run it
kilix tmux --with-tb               # also publish tmux-cli as the `tb` command
kilix cap                          # install/build the optional mansion desktop, then open it
kilix tui                          # install/verify the text-native desktop, then open it
kilix land                         # install/build the walkable desktop, then open it
kilix bonsai                       # the BitNet model store: browse, download, verify
kilix bonsai list                  # one line per model, with size and state
kilix bonsai pull vibevoice-asr-bitnet   # download one — this one is the dictation model
kilix rtsp list                    # cameras this machine is configured for
kilix rtsp view poolcam --tab      # one camera filling a new page
kilix rtsp mosaic yard             # several in a grid
kilix mask --image plate.png room.mask.png   # paint a region map over a picture
kilix mask --render frame.ppm room.mask.png  # compose one frame, no terminal
kilix voice install               # pinned Kilix Voice + default Vosk model
kilix stt --models                # all speech models, sizes, install/runtime state
kilix stt --install lgraph-en-us --default lgraph-en-us
kilix voice doctor                # dependency and audio-device diagnostics
kilix tts                         # read-aloud settings and test-phrase TUI
kilix stt                         # dictation settings and microphone-level TUI
kilix speak "hello"               # read explicit text aloud
kilix dictate                     # recognize one utterance; never presses Enter
kilix status                      # version/commit, engine, writable config, provider contract
```

Put `~/.local/gpu_terminal/sources/kilix` on your `PATH` (or
`ln -s ~/.local/gpu_terminal/sources/kilix/kilix ~/.local/bin/kilix`) to just type `kilix`.

## Read aloud and dictation

The speaking-head and microphone controls in Kilix's page strip are the actual
voice actions. The matching desktop-menu entries open settings and diagnostics:
they do not themselves read or dictate. Both actions operate on terminal text.
A pixel application such as the Kilix 95 desktop has neither readable terminal
cells nor a visible text input target, so Kilix refuses both actions there with
an explanation; open or switch to a terminal pane first.
Dictation records the pane selected at the first click and rechecks it before
inserting anything: if it becomes a pixel application or reaches a hidden
password prompt while listening, the transcript is visibly discarded. A
second microphone click requests stop but keeps the private result socket open
for the recognizer's final answer; stale partial text is never substituted.

Opening `kilix stt` installs only the small settings/runtime closure when it is
missing; it does not fetch a recognizer library or model. A click on the
disabled microphone offers the selected model with its download size and asks
before opening a visible installer terminal. The Voice pages in `kilix
settings`, Kilix 95 Settings, and the built-in WM Settings offer the same
install-and-default action. The equivalent scriptable interface is `kilix stt
--models`, `kilix stt --install MODEL`, and `kilix stt --default MODEL`; install
and default may be combined in one invocation.

The catalog contains `small-en-us` (Vosk, 39.3 MiB), `lgraph-en-us` (Vosk,
124.5 MiB), and `vibevoice-asr-bitnet` (VibeVoice, about 1.6 GiB). VibeVoice's
weights are shared with Kilix Bonsai, so installing them does not create a
second copy. They can be selected as a future-compatible default, but this
version of the live voice runtime cannot dictate with them; the UI and CLI
report that distinction instead of calling the weights runnable.

`kilix voice install` installs the immutable `kilix-voice` 0.1.2 source at
commit `3244b3f4a1811ba0bf84cffb90509be85a329536`, the official Vosk 0.3.45
x86_64 wheel, and either the default `vosk-model-small-en-us-0.15` or the
`--model lgraph-en-us` dynamic-graph model. Downloads are SHA-256 verified.
The installer extracts only the wheel's fixed `vosk/libvosk.so` member, checks
its ELF architecture and complete required Vosk API, loads the pinned acoustic
model through that API, and runs every installed tool's `--version` probe before
promotion. Library and model generation names include their full archive
digest. Their active links, the runtime link, command entrypoints, and install
stamp are promoted as one rollback-safe transaction, so a pin change can never
relabel an old payload. `--without-dictation` retains a smaller read-aloud-only
path for non-x86_64 machines.

The native library and acoustic model each carry an adjacent
`README.kilix-provenance` recording their upstream URL and checksum, plus a
copy of the Apache-2.0 license. They remain under the private Kilix data tree;
`kilix voice doctor` prints their active paths. The daemon starts lazily and
uses one private Unix-socket connection per control request. Read-aloud status
is polled four times per second so completion clears the widget, while a new
asynchronous synthesis or playback error is shown once rather than disappearing
into daemon output. Dictation device/model errors, an empty result, or a missing
final response are likewise shown in a dialog instead of looking like a dead
click.

## Clickable buttons (the headline feature)

Every pane's title bar shows these synchronized-input/font/split/maximize/close
buttons on the right (bold):

| Button | Click does | Same as |
|---|---|---|
| memory chip | open Kilix Memory; width follows pane process-tree use | `kilix memory --graphics` |
| keyboard | join/leave the pane's synchronized-input group | Tilix input synchronization |
| `+` | increase font size for this Kilix window | `change_font_size current +2.0` |
| `-` | decrease font size for this Kilix window | `change_font_size current -2.0` |
| `←` | split left — new pane to the left | split right, then swap |
| `↑` | split up — new pane above | split down, then swap |
| `↓` | split down — new pane below | `Ctrl+Alt+D` |
| `→` | split right — new pane to the right | `Ctrl+Alt+R` |
| `▢` | maximize / zoom the pane | `Ctrl+Alt+Z` |
| `✕` | close the pane | `Ctrl+Alt+W` |

The buttons are drawn as text or **Nerd Font icons** — a RAM chip and value,
a keyboard for synchronized input, `+`/`-` for local font size, bold arrows for splits
(pointing where the new pane lands), a maximize glyph, and a close ✕. They
**highlight under the cursor**. A single keyboard click toggles that pane; its
button stays depressed while selected, and typing in any selected pane reaches
all other selected panes. Double-click the keyboard to select every pane in
the tab; double-click it again to deselect every pane. App overlays are never
included. Hiding the keyboard button in Settings also clears active
synchronized-input groups. Clicking a header focuses the pane, and a click on
the title itself opens the **pane action menu** — rename, copy title, reset,
clear, split right/down, close (maximize also lives on the `▢` button and
`Ctrl+Alt+Z`).
The active pane's header is highlighted (bright blue); inactive panes are grayed —
matching Tilix's active-pane cue.

The memory chip is enabled in `auto` mode by default. It appears when the
pane's shell and descendant processes reach 1 GiB, shows GiB to one decimal
place (`1.1`), and grows from a square chip toward a RAM-stick shape as the
number gains digits. Clicking it opens Kilix Memory in a new tab. Set the
shared `KILIX_CHROME_PANE_MEMORY_MODE` preference to `always` to keep it
visible and report smaller values in MiB/KiB, or `off` to remove it. The
sampler uses proportional memory from Linux `smaps_rollup` where readable and
RSS as a fallback, caches each process-tree sample, and refreshes title bars
only when the displayed value changes.

The far right of the page strip can show thermometer, volume, network,
calendar, local date/time, and (when applicable) battery items. The thermometer
is disabled by default; when enabled it shows the hottest readable Linux
thermal-zone/hwmon temperature to one decimal place in green below 80°C, yellow
at 80–89°C, or red from 90°C. It sits at the left edge of the status group and
opens `kilix-temps --graphics` in a new tab. A neutral `--°` remains clickable
when no sensor can be read. The volume icon opens
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
kilix new-pane            # open a pane to the right of this one
kilix new-pane left       # ...or to its left; also `right`, `down`
kilix new-pane down -- htop        # run something in it
kilix new-tab --title notes        # open a new page
kilix switch              # the page/pane switcher (same as F12)
```

`kilix new-pane` places the pane relative to **the pane the command runs in**,
not to whichever pane currently has focus, so it does the same thing from a
background pane as from the foreground one.

All four directions are exact, which needed a change in the fork. The splits
layout could always put a window on either side of either axis, but upstream
kitty named only three of the four placements: `vsplit` and `hsplit` are always
the far side of their axis, and `before` is the near side of whichever axis the
*layout* defaults to. The near side of a chosen axis was reachable only by
splitting the other way and then using `move_window` to swap — which works from
a keybinding and nowhere else, so remote control could not do it at all. The
fork adds `vsplit-before` and `hsplit-before` for those two placements; the
pane-title bar's own left and up split buttons use them now instead of the swap.

These commands use kitty remote control against the current live GUI instance.
`kilix focus` can jump to a tab or pane; `kilix watch` is intentionally
read-only and polls `kitten @ get-text`, so it is useful for shell output and
simple full-screen programs but is not real multiplexing. It does not carry
graphics, mouse state, or a second interactive PTY. For true attach/view, start
the session under tmux with `kilix serve` or `kilix mux <name>`.

## Open web URLs

```bash
kilix open-url wikipedia.org        # canonical URL-opening command
kilix browse --incognito site.com   # compatible spelling; same policy
```

Both commands launch the first installed browser in this fixed order:
`google-chrome`, `chromium-browser`, then `firefox-esr`. `--incognito` is
translated to Firefox's `--private-window` spelling when necessary.

Only when none of those real browsers is available does Kilix use its
experimental in-pane fallback. That fallback renders headless Chrome inside
the pane: page pixels (images,
video, layout) stream in at full resolution via the kitty graphics protocol,
while **page text is drawn as live terminal glyphs** — crisp, and selectable
like any terminal text (shift+drag). Mouse clicks, wheel scrolling, and typing
are forwarded to the page, a software pointer tracks the mouse (headless
Chrome draws none; `--no-cursor` opts out), and hovering triggers real hover
effects. Fallback sessions keep history/cookies in
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

The fallback requires `google-chrome`/`chromium`, Python 3, and Pillow. The default
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
back with XTest — key *releases* included, so games can hold keys. It
generalizes the in-pane browser fallback from Chrome to anything with an X
window; think of it as a tiling WM turned inside-out — the app's pixels come to
the pane instead of the WM arranging app windows. Proven by playing X-COM: UFO
Defense under DOSBox entirely through a pane.

In a Pleb desktop session, ordinary commands for installed graphical
applications are routed here automatically. Kilix combines common Debian GUI
command names with visible, non-terminal entries from the XDG `.desktop`
catalogue; scripts and terminal-only programs are not changed. Set
`KILIX_RUN_ALIASES=0` for native X11 windows, add commands with
`KILIX_RUN_ALIAS_APPS="foo bar"`, or exclude one with
`KILIX_RUN_ALIAS_EXCLUDE_APPS="foo"`.

Contained Chromium- and Firefox-family launches receive a private, disposable
per-tab profile so an already-running native browser cannot capture the URL and
escape Kilix. Pass an explicit Chromium `--user-data-dir` or Firefox
`--profile` when persistent browser state is required; explicit profiles are
never replaced.

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
recovery keepalives. The in-pane browser fallback and `kilix desktop` use the same standalone
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

## Desktops in a tab

```bash
kilix desktop                # open the configured provider (Kilix 95 by default)
kilix xp                     # open Kilix 95 with the XP flavor
kilix desktop xp             # equivalent provider-specific form
kilix cap                    # open the optional Kilix Cap mansion
kilix tui                    # open the optional text-native desktop
kilix land                   # open the optional walkable desktop
KILIX_DESKTOP_PROVIDER=cap kilix desktop
KILIX_DESKTOP_PROVIDER=tui kilix desktop
KILIX_DESKTOP_PROVIDER=land kilix desktop
```

`kilix desktop` is a provider facade. The separate `kilix-95`
repository is the authoritative desktop. `auto` prefers an installed external
checkout; the bundled `desktop/` tree is an explicitly reported compatibility
fallback. Both must pass the same provider API, Kilix SDK, and security-feature
contract before execution. `cap` selects the native Kilix Cap executable,
`tui` selects Kilix TUI, and `land` selects Kilix Land. Managed first installs
use immutable Kilix-owned pins, and the launchers validate executable paths
rather than a Python provider manifest. The generic `command` provider remains
available for other full desktops. `kilix status` shows the effective provider
and path.

These commands open a desktop in a Kilix tab. Making XP the Pleb login desktop
also requires the session switch and provider in Pleb’s persistent
`~/.local/gpu_terminal/pleb/config/session.env`:

```sh
PLEB_DESKTOP=1
KILIX_DESKTOP_PROVIDER=xp
```

`KILIX_DESKTOP_FLAVOR=xp` alone only selects Kilix 95’s first-run appearance;
it does not turn a plain Pleb shell session into a desktop session.

```bash
KILIX_DESKTOP_PROVIDER=external \
KILIX95_AUTO_INSTALL=1 \
KILIX95_DIR=~/.local/gpu_terminal/sources/kilix-desktops/kilix-95 \
kilix desktop
```

By default the checkout is discovered under
`~/.local/gpu_terminal/sources/kilix-desktops/kilix-95`, while its writable XP desktop state (including its
wallpaper selection) stays under `~/.local/gpu_terminal/kilix-95`. The bundled
fallback keeps independent state under `~/.local/gpu_terminal/kilix`.

Relevant knobs:
`KILIX_DESKTOP_PROVIDER=auto|builtin|external|xp|cap|tui|land|command|none`,
`KILIX_DESKTOP_COMMAND`, `KILIX_DESKTOP_NAME`,
`KILIX_DESKTOP_FLAVOR=95|xp`, `KILIX95_DIR`, `KILIX95_REPO`,
`KILIX95_BRANCH`, `KILIX95_REF`, and `KILIX95_AUTO_INSTALL=1` to allow a
missing external checkout to be cloned. Automatic installs require
`KILIX95_REF` to be a full immutable commit SHA; mutable tags/branches require
the explicit `KILIX95_ALLOW_MUTABLE_REF=1` trust override. `kilix update`
similarly honors `KILIX_REF` by fetching it from the validated origin and
checking out the resolved commit detached.
Direct updates and fork builds serialize on the private
`~/.local/gpu_terminal/kilix/state/build-update.lock`. An outer installer that
already holds this lock must pass its open, locked descriptor to Kilix as
`KILIX_TRANSACTION_LOCK_FD` (and preserve that descriptor across `exec`);
Kilix validates that it names the canonical lock before treating it as
reentrant. The resolved path is exported to children as
`KILIX_TRANSACTION_LOCK_PATH`.

### Kilix Cap

`kilix cap` is shorthand for selecting `KILIX_DESKTOP_PROVIDER=cap`. On its
first launch, Kilix clones an immutable Kilix-pinned commit from
`https://github.com/itsmygithubacct/kilix-cap.git` into
`~/.local/gpu_terminal/sources/kilix-desktops/kilix-cap`, builds it locally with `make`, and opens
the native executable in a new tab. Later launches reuse the checkout and
incremental build. Kilix Cap itself downloads nothing at runtime.

An existing Git checkout is moved to the resolved ref rather than rebuilt as
found: Kilix validates its `origin`, then checks out the same commit a first-use
download would land on, so a moved pin reaches machines that already have the
component instead of only fresh ones. The move is reported either way, and named
`REWOUND` when the pin is older than what is installed. A checkout with local
modifications is kept and says so; `KILIX_CAP_KEEP_EXISTING_CHECKOUT=1` keeps a
clean one deliberately, naming the ref it did not install. Override
`KILIX_CAP_DIR`, `KILIX_CAP_REPO`, or `KILIX_CAP_REF` for reviewed source;
mutable refs require `KILIX_CAP_ALLOW_MUTABLE_REF=1`. Set
`KILIX_CAP_AUTO_INSTALL=0` to forbid the first-use clone or
`KILIX_CAP_TRUST_EXISTING_CHECKOUT=1` only for a trusted packaged/nonstandard
checkout. Building requires a C11 compiler, `make`, pthreads, and zlib
development headers.

The same rule holds for every component installer of this shape —
`kilix-tui-utils`, Kilix Land, Kilix IceWM, and `kilix-chawan` — each with its own
`*_KEEP_EXISTING_CHECKOUT` opt-out.

### Kilix TUI

`kilix tui` and `kilix desktop tui` select
`KILIX_DESKTOP_PROVIDER=tui`. If no installed `kilix-tui` command exists,
Kilix clones its immutable pinned `kilix-tui-utils` commit into
`~/.local/gpu_terminal/sources/kilix-desktops/kilix-tui-utils`, runs that repository’s installer,
and opens its text-native desktop. The same interface renders as terminal cells
everywhere and as a graphical Tango desktop when Kitty graphics are available.

Override `KILIX_TUI_UTILS_DIR`, `KILIX_TUI_UTILS_REPO`, or
`KILIX_TUI_UTILS_REF` for reviewed source. Set
`KILIX_TUI_UTILS_AUTO_INSTALL=0` to forbid a first-use clone; mutable refs and
nonstandard existing checkouts require the corresponding explicit trust
overrides.

### Kilix Land

`kilix land` and `kilix desktop land` select
`KILIX_DESKTOP_PROVIDER=land`. On first use Kilix clones its immutable pinned
`kilix-land-desktop` commit into
`~/.local/gpu_terminal/sources/kilix-desktops/kilix-land-desktop`, initializes the pinned
submodules, builds it locally with `make`, and opens the native executable.
Later launches reuse and rebuild the checkout without resetting local
development changes.

Override `KILIX_LAND_DESKTOP_DIR`, `KILIX_LAND_DESKTOP_REPO`, or
`KILIX_LAND_DESKTOP_REF` for reviewed source. Set
`KILIX_LAND_DESKTOP_AUTO_INSTALL=0` to forbid a first-use clone; mutable refs
and nonstandard existing checkouts require the corresponding explicit trust
overrides. `KILIX_LAND_DESKTOP_ASSETS` selects the runtime asset root.

### Kilix IceWM

`kilix icewm` and `kilix desktop icewm` select
`KILIX_DESKTOP_PROVIDER=icewm`. On first use Kilix clones its immutable pinned
`kilix-icewm` provider into
`~/.local/gpu_terminal/sources/kilix-desktops/kilix-icewm`. The provider then
builds its own pinned IceWM source only when no usable session binary exists.
Both layers verify immutable commits: Kilix advances a clean provider checkout
to its resolved ref, and the provider reconciles its IceWM submodule to the
recorded gitlink before building it.

Override `KILIX_ICEWM_DIR`, `KILIX_ICEWM_REPO`, or `KILIX_ICEWM_REF` for
reviewed source. Set `KILIX_ICEWM_AUTO_INSTALL=0` to forbid a first-use clone;
`KILIX_ICEWM_KEEP_EXISTING_CHECKOUT=1` deliberately keeps a development tree,
while mutable refs and nonstandard checkouts require their corresponding trust
overrides.

### Kilix 95

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
- **Kilix Temps** — Start ▸ Programs ▸ Kilix Temps prefers an installed
  `kilix-temps`, then the sibling `kilix-tui-utils/tools/temps` source. On a
  fresh checkout it delegates to `kilix temps`, which installs the single
  pinned `kilix-tui-utils` checkout and opens its graphical tab.
- **Kilix Memory** — Start ▸ Programs ▸ Kilix Memory opens the graphical
  RAM/swap/pressure dashboard from that same unified checkout. Its optional
  graphics dependencies are shared with Temps rather than cloned into a second
  installer-owned source cache. The same monitor is available from the Kilix
  Settings TUI Tools section.
- **PTY Sessions** — Start ▸ Programs ▸ PTY Sessions opens the persistent-pane
  manager without placing that manager inside another brokered pane. Detached
  panes can be selected and attached, refreshed, or deliberately terminated.
- **Games** — Start ▸ Programs ▸ Games. Each entry plays immediately if
  `~/.local/gpu_terminal/kilix-95/config/games.conf` already points at a working install, otherwise
  one consented click sets it up (paths saved to that file) and launches it in
  a tab: **Doom** downloads the official shareware episode plus a
  dosbox-staging build if no dosbox is installed (fullscreen, fire on Space,
  sound on); **Bashed Earth** clones + builds
  [itsmygithubacct/bashed-earth](https://github.com/itsmygithubacct/bashed-earth).
  Native catalog games include **Kilix Lights** and **Super Kilix**, which are
  cloned recursively, built, and launched directly in their Kilix tabs.
  The Games tab, `kilix settings`, and `kilix games enable|disable NAME...`
  all select which entries appear, using the root-level shared settings file.
- **Media Player** — Start ▸ Programs ▸ Media Player. The skin sits *directly
  on the desktop* with no kilix window frame (Winamp-on-Win95 style): an SDL2
  app on a private display whose background is chroma-keyed away, so only the
  skin composites onto the desktop — drag it by its own titlebar; clicks on the
  gaps fall through to the desktop icons. First run clones + builds
  [itsmygithubacct/kilix-amp](https://github.com/itsmygithubacct/kilix-amp),
  a Winamp 2.x clone, into `~/.local/gpu_terminal/kilix-95/data/apps`. The same
  binary also runs `--headless`, serving a control socket that the TUI Music
  tool drives — one decoder behind both front ends rather than two that drift.
  `kilix amp` is the same install with a name a caller can reach: it builds the
  pinned player if needed and runs it, `kilix amp --headless` runs the backend
  with no windows, and `kilix amp --install-only` just builds it (what `pleb
  install` uses to get it in place before anyone asks). The commit comes from
  the pinned content catalog, so moving that pin rebuilds on the next call.
- **PDF Conversion** — `kilix pdf` installs the catalog-pinned
  [kilix-pdf-conversion](https://github.com/itsmygithubacct/kilix-pdf-conversion)
  provider into Kilix's private app directory, then opens its guided terminal
  interface. Pass a PDF to convert it directly, use
  `--install-only` while provisioning, or `--print-ref` to inspect the exact
  immutable source commit. Its managed runtime is synchronized from the frozen
  `uv.lock` with `uv`. `kilix app run kilix-pdf-conversion` is the shared
  in-place/pane contract; `kilix app window kilix-pdf-conversion` wraps the
  terminal UI in an `xterm` PTY for IceWM and Kilix 95/XPane. The TUI, Cap,
  and Land launch the same ID in a Kilix tab, so every surface uses one pin,
  installer, executable, and launch-mode declaration.
- **Create Launcher…** (Start menu or right-click the desktop) writes
  freedesktop-style `.desktop` files into the desktop folder
  (`~/.local/gpu_terminal/kilix-95/data/desktop`, override with `$KILIX_DESKTOP_DIR`); plain
  files and folders dropped there show up as icons too. Launchers open in a
  new kilix tab / OS window, through `kilix run` for X11 apps, or through
  `kilix open-url` for URLs.

Quit via Start ▸ Shut Down… (or `Ctrl+Alt+Q`); the terminal underneath is
untouched. All artwork is drawn in code — no Microsoft assets are bundled.
Modules currently live in `desktop/` or an external `kilix-95` checkout. The
desktop draws its own Win95 mouse pointer (pass `--no-cursor` if you'd rather
not).

## Stream sessions to other devices (experimental)

kilix can share a session over the network so you can pick it up — or just watch
it — from another laptop, a phone, or a browser. There are four tiers, from
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
ssh -t you@host ~/.local/gpu_terminal/sources/kilix/kilix attach work     # take over
ssh -t you@host ~/.local/gpu_terminal/sources/kilix/kilix view  work      # watch, read-only
```

Needs `tmux`. Animated `browse`/`run` panes work but are throttled over tmux —
for those, use the pixel tiers below.

### 2. A live Kilix pane — semantic text, graphics and audio

`kilix remote` exposes a pane that is already running in the current Kilix
frontend. The pane stays locally attached and the broker continues to own its
PTY:

```bash
kilix ls --panes
kilix remote serve PANE_ID --socket 127.0.0.1:47800

# Use the token printed by the server.
kilix remote attach --socket 127.0.0.1:47800 --token TOKEN
kilix remote view   --socket 127.0.0.1:47800 --token TOKEN
```

Run the server in a different pane from `PANE_ID`. PTY output comes from a
protocol-v2 broker **observer**, which is structurally read-only and does not
take the local frontend's control slot. Control input uses a separate,
authenticated Kitty command restricted to the exact broker-session marker and
bounded chunks. Omitting it with `serve --no-input` produces a view-only
session, and `remote view` is independently enforced by the multiplexer server.

Applications using Kilix's shared `FramePresenter` discover a private
session-specific frame tap only while a remote server is waiting. The tap has a
one-frame newest-wins queue and all socket I/O happens off the local rendering
thread; with no server, the local path does one cheap socket check per second.
Text and inline images remain semantic, while host-local shared-memory/file
graphics are represented by the tapped RGB motion plane. Audio can be added
with `serve --audio-source COMMAND`; the command writes raw signed-16-bit PCM,
and an attaching Kilix plays it through `pacat`, `aplay`, or an explicit
`--audio-output`.

A non-loopback `--socket ADDRESS:PORT --lan` is direct TLS: the server prints
both a token and a certificate fingerprint, and the client requires both. An
SSH tunnel to loopback remains the smaller exposure surface.

Kilix pins and builds the multiplexer, broker-v2 observer, and presenter tap
from their submodules. `KILIX_MULTIPLEXER_HOME` selects a development checkout
such as `~/.local/gpu_terminal/sources/kilix-apps/kilix-multiplexer` without changing the
release pin.

### 3. A GUI app — view + control from a browser or VNC client

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

Starting with Kilix 0.1.4, when `KILIX_RUN_LOG` names a private log file,
`kilix run` records capture readiness as an exact line.
`content-ready=changed` means a changed capture followed the startup snapshot;
`content-ready=initial-grace` means an initial capture was emitted and no
changed capture arrived during the three-second grace. The latter is a handoff
heuristic for fast static applications, not proof that a network page finished
loading. The legacy `content-frames=1` marker retains its original meaning and
is emitted only for the first changed capture after the startup snapshot.

### 4. The whole kilix — every pane, graphics and video included

```bash
kilix share                       # whole kilix on a headless screen -> your browser
kilix share --size 1600x900 --lan
kilix share --audio --debug       # desktop audio in the stream + encode metrics
```

(*Renamed from `kilix desktop` when the
[desktop providers](#desktops-in-a-tab) claimed that name.*)

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
| Resize pane (interactive: arrows, `Enter` keep / `Esc` cancel) | `Ctrl+Shift+R` |
| Reset pane size | `Ctrl+Shift+Home` |
| Increase / decrease terminal scale | `Ctrl+Shift+=` / `Ctrl+Shift+-` |
| Reset terminal scale | `Ctrl+Shift+Backspace` |
| Move/swap pane | `Ctrl+Alt+Arrows` |
| Zoom/maximize pane (toggle) | `Ctrl+Alt+Z` |
| Broadcast input to all panes in page | `Ctrl+Alt+B` |
| Cycle layout | `Ctrl+Alt+L` |
| Next / previous pane in page | `Ctrl+Tab` / `Ctrl+Shift+Tab` |
| New page (session) | `Ctrl+Shift+T` |
| Close page | `Ctrl+Shift+Q` |
| Next / previous page | `Ctrl+Shift+→` / `Ctrl+Shift+←`, or `Ctrl+PgDn` / `Ctrl+PgUp` |
| Scroll pane by a line | `Ctrl+Shift+↑` / `Ctrl+Shift+↓` |
| Reorder page right / left | `Ctrl+Shift+PgDn` / `Ctrl+Shift+PgUp` |
| Jump to page 1–10 | `Ctrl+Alt+1` … `Ctrl+Alt+0` |
| Page and pane switcher | `F12` |
| Rename page | `F2` |
| Content-only fullscreen (hide page strip and pane chrome) | `F11` |
| Toggle this tab's OS window fullscreen from a shell | `kilix fullscreen` |
| New OS window (same dir) | `Ctrl+Shift+N` |

### Copy and paste — including inside full-screen TUIs

In a plain shell the mouse works the way the context menu teaches: drag to
select (the selection lands on the clipboard automatically), right-click for
Copy / Paste / Select all, middle-click to paste.

A full-screen TUI (Claude Code, htop, a pager) grabs the mouse for itself, so
those plain gestures reach the app instead of Kilix. Everything still works —
hold **Shift**:

| Want | Plain shell | Inside a full-screen TUI |
|---|---|---|
| Select text | drag | `Shift`+drag |
| Select a word / line | double / triple click | `Shift`+double / `Shift`+triple click |
| Extend a selection | right-click | `Shift`+right-click |
| Copy | automatic on select | automatic on select |
| Paste into the app | middle-click | `Ctrl+Shift+V`, or `Shift`+middle-click |
| Context menu | right-click | `Ctrl+Shift+Right`-click |

Two things that look like failure but are not:

- **The highlight disappears.** Busy TUIs repaint constantly, and any repaint
  that touches a selected line clears the highlight. The text was copied the
  moment you released the button — paste it and it is there.
- **No text under the drag?** Open the scrollback pager (`Ctrl+Shift+H`)
  instead: nothing grabs the mouse there, so ordinary drag-select works on
  everything the pane has printed.

Scripts should not assume `xclip`/`xsel`; use `kitty +kitten clipboard`, which
talks to the terminal directly.

### Going to a page or a pane — `F12`

`F12` opens **`kilix-switch`**, from
[kilix-tui-utils](https://github.com/itsmygithubacct/kilix-tui-utils), over the
current pane. It replaces the two choosers kitty ships, which were the same
thing twice — a numbered list of titles, one for pages and one for panes. A
title is a poor handle on a pane, since several are `bash` and several more are
whatever directory they started in, so the list told you least exactly when you
had enough windows to need it.

The switcher shows one tree of pages and their panes with the process and
directory that actually identify each one, a filter (`/`) across all of it, and
a live view of what the highlighted pane is currently showing. `Tab` cycles
between everything, this page, and everywhere else; `Ctrl+Shift+B` `q` opens it
already scoped to this page. It can also rename and close what it lists, and
closing always asks first.

It reaches the terminal through the same scoped remote-control credential every
Kilix pane already holds — the one behind `kilix ls`, `kilix focus` and
`kilix watch` — so it can do exactly what those commands can and nothing more.
If the tool is not installed, `F12` says so rather than opening an overlay that
disappears.

### tmux-style leader — `Ctrl+Shift+B`

If tmux is already in your fingers, press `Ctrl+Shift+B` and then a tmux key.
Panes are tmux panes, pages are tmux windows. One key per press and the mode
exits, just like after a tmux prefix; an unrecognized key beeps and exits, and
`Esc` leaves early.

The leader is **not** tmux's own `Ctrl+B`, on purpose: `kilix serve` / `attach` /
`view` run a real tmux server whose prefix has to reach it untouched, and
`Ctrl+B` is readline's backward-char in every shell. `Ctrl+Shift+B` keeps the
muscle memory while leaving both alone.

| tmux | kilix | Action |
|---|---|---|
| `C-b ← ↑ ↓ →` | `Ctrl+Shift+B` then `← ↑ ↓ →` | Focus pane in that direction |
| `C-b o` | `Ctrl+Shift+B` `o` | Next pane |
| `C-b ;` | `Ctrl+Shift+B` `;` | Last (previously focused) pane |
| `C-b q` | `Ctrl+Shift+B` `q` | The switcher, scoped to this page |
| `C-b z` | `Ctrl+Shift+B` `z` | Zoom/maximize pane |
| `C-b x` | `Ctrl+Shift+B` `x` | Close pane |
| `C-b { }` | `Ctrl+Shift+B` `{` / `}` | Swap pane back / forward |
| `C-b Space` | `Ctrl+Shift+B` `Space` | Next layout |
| `C-b C-←→` | `Ctrl+Shift+B` `r` | Resize pane interactively |
| `C-b " %` | `Ctrl+Shift+B` `"` / `%` | Split stacked / side-by-side |
| `C-b c` | `Ctrl+Shift+B` `c` | New page |
| `C-b n` / `p` | `Ctrl+Shift+B` `n` / `p` | Next / previous page |
| `C-b l` | `Ctrl+Shift+B` `l` | Last (previously used) page |
| `C-b 1`…`9` | `Ctrl+Shift+B` `1`…`9` | Jump to page N |
| `C-b w` | `Ctrl+Shift+B` `w` | Page chooser |
| `C-b ,` | `Ctrl+Shift+B` `,` | Rename page |
| `C-b &` | `Ctrl+Shift+B` `&` | Close page |

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
rm -rf ~/.local/gpu_terminal/sources/kilix                          # project source
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
system build deps from [Requirements](#requirements). The binary lands at
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

## Tweaks

Use Start ▸ Settings in kilix 95, or edit
`~/.local/gpu_terminal/kilix/config/kitty.conf`. It includes the tracked
`config/kitty.conf` defaults; add overrides to the user file.

Use `kilix settings` for clickable chrome and Kilix 95 game availability.
Thermometer, volume, network, calendar, date/time, battery, pane-memory mode,
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
readout, or `pane_memory=off` to hide the chip.

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
