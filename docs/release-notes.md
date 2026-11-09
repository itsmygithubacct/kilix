# Kilix release notes

Release history moved here from the README. Each section describes the version it names; for current behaviour start at the [README](../README.md) and the topic documents it links.

## 0.2.3 RC1 power settings

`kilix power-controls` opens Kilix Power Controls, the Kilix GUI for power
preferences, with plugged-in and battery policies, display power, dimming,
device information, and the shared Screen Lock page. `--native` opens it on the
current display. The pinned application uses the existing desktop power service.

- `kilix tui screen-lock` — automatic screen-lock settings shared with Pleb's
  Display Settings and Power Manager. Use `--show`, `--set off`,
  `--set on --idle-seconds 600`, or `--set session` to follow session defaults.
  Saved changes apply at the next login; manual locking remains available.

## 0.2.2.1 maintenance candidate

Coordinates the Model Setup password-disclosure fix and four-part maintenance-release support.

This source is a release candidate; installation-media and upgrade acceptance are pending.

## Release 0.2.2 — Linux

Pane logs open faster. The Log button starts the viewer without preparing a
second runtime, and transcript replay reuses unchanged rows and batches plain
text writes while preserving the recording's rendered output.

Kilix 0.2.2 ships in the coordinated Plebian-OS Linux release published
November 3, 2026. The `v0.2.2` tag identifies the released source; this branch
can contain later development. Supported upgrade paths are defined by the
[release's OS upgrade policy](https://github.com/itsmygithubacct/plebian-os/blob/v0.2.2/UPGRADING.md).

Linux installation and lifecycle qualification covers QEMU/KVM BIOS and EFI
virtual machines. Bare-metal USB installation and physical GPU operation were
not qualified. Windows remains at `windows-v0.2.2-preview.4`; ARM64 is not a
supported OS release target. See the [published release notes](https://github.com/itsmygithubacct/plebian-os/releases/tag/v0.2.2)
for known issues and deferred feature qualification.

`kilix fingerlock` finds a USB fingerprint reader, installs Debian `fprintd`
on first use after an explicit yes, and enrolls, verifies, lists, or deletes
fingers through fprintd. A verify result checks the reader. It does not unlock
the screen, change login, or install `libpam-fprintd`.

Amp and `kilix-music` share one per-user playback service, queue, transport
controls and EQ. General music uses the selected `kilix-mpv` runtime; native
EnCodec files and live streams retain their decoder and admission path.
Closing either controller leaves the shared session running. The pinned Amp
build includes the spectrum library and checks the installed mpv filter chain
before it is reported installed.

The Windows preview adds desktop automation through `kilix desktop`:
application/control selectors, condition waits, local screenshots, UI Automation
inspection/actions, keyboard and mouse input, application launch, and window
management. Native Windows and WSL share SDK 1.18's `kilix_sdk.windows` client. Linux hosts can use
`kilix windows --host SSH_ALIAS` through an existing SSH connection. See the
[Windows desktop commands](../windows/README.md#windows-desktop-automation).

See the [0.2.2 help guide](help/README.md) for current commands, storage,
model setup, speech and troubleshooting. Older release sections below describe
their named versions; they are not a current component inventory.
For the human and agent workflows that connect terminal work, automation,
verification and recovery, see [Kilix Workflows](workflows/README.md).

`kilix ask` is the person-facing request command. It shows the route and the
plan and acts only on a typed yes. The interpreter is the saved Ask selection,
or LFM2 when none is saved. `--model grammar` uses the exact grammar and skips
model loading. `--model lfm3` and `--model needle3` select those interpreters
for one request; install Needle 3 with
`kilix-needle install --commands needle3`, then run
`kilix ask --model needle3 "open calculator"`. Every interpreter uses the same
request validation and confirmation. `kilix ask --yes REQUEST` acts without
the yes/no prompt. A refusal or an unsure route still does not run, and
`--yes` cannot be combined with `--voice`. Model Setup and
`kilix-needle workflows setup --model needle3` save the Ask preference after
verification. An explicit `--model` overrides that preference for one request;
`KILIX_ASK_COMMANDS_BACKEND` overrides it in a shell. The installer presents
the model and runtime licences before downloading. An agent with a known job
uses that job's route, described in
[Kilix Workflows](workflows/README.md) and the
[agent guide](AGENTS.md). An unclear job uses
`kilix-needle ask --agent --json`. `--plan` reports the route, and `--yes`
acts under that job's own consent rules.

Ask for current city weather with `kilix ask "What's the weather in Tokyo?"`
or `kilix ask "weather in Paris, France in Celsius"`. This also works from the
desktop Run dialog and Ask Kilix by voice after confirming the transcript.
City weather uses a direct lookup without loading a language model. For a
literal city name, use `kilix weather "New York"` or
`kilix weather "London, United Kingdom" --unit celsius`.
Answers identify the resolved city, country and conditions timestamp. A major
city is selected when it clearly dominates same-name matches; otherwise add a
country or state to distinguish them. These lookups use the widget's temperature
unit by default and leave its saved ZIP, schedule and cached conditions alone.
Only current conditions are supported, with stale data labeled and unavailable
or timed-out requests reported. City names are sent to
[Open-Meteo geocoding](https://open-meteo.com/en/docs/geocoding-api), then the
resolved coordinates to its [weather API](https://open-meteo.com/en/docs).

The `kilix session` paragraph of this release is kept in the [README](../README.md#what-it-does).

`kilix pty` manages persistent terminal sessions and their journals; the PTY
manager in Kilix 95 uses the same host interface. `kilix ralph` provides durable
mailboxes and bounded follow-ups. The host also selects the reviewed multiplexer,
agent-state tools, IceWM integration and Content catalog. Runtime qualification
of each external agent/client remains separate from the local source tests.

PTY selection checks apply to the selector supplied by the caller; they do not
authenticate the user's original intent. Consumer qualification remains open.
The host's ARM64 engine support does not imply that the complete Plebian-OS
0.2.2 binary closure supports ARM64; its selected runtimes currently target AMD64.

- The three-line Start button and status widgets open floating drop-downs above
  the panes. Panes keep their size and continue updating behind the popup.
- Popups follow the page-strip position, fit the window when resized, and
  return focus to the original pane when dismissed. Outside clicks close the
  popup without activating the pane beneath it.
- Right-click CPU/RAM, temperature, read-aloud, microphone or Wi-Fi for an
  explanation and that widget's settings. Temperature offers Celsius and
  Fahrenheit directly; microphone settings link to input devices and the
  dictation model selector/installer. The CPU/RAM indicator measures logical
  CPU cores and memory in use, not GPU utilization.
- Weather accepts a five-digit US ZIP and shows temperature and conditions
  after loading. Click the unconfigured Weather control or run
  `kilix settings --widget weather`. Settings are saved in
  `~/.local/gpu_terminal/settings.conf` (or `GPU_TERMINAL_SETTINGS_FILE`), with
  a private last-result cache in the adjacent `kilix/weather.json`. No location
  is detected or requested until a ZIP is supplied. A ZIP is sent to the
  [Open-Meteo geocoder](https://open-meteo.com/en/docs/geocoding-api), and its
  coordinates are sent to the [weather API](https://open-meteo.com/en/docs).
  Weather-model estimates and GeoNames location data are attributed in the
  card. Open-Meteo's free API is for non-commercial use; weather data is CC BY 4.0.
  Choose refresh on load/manual, every 15/30/60/180 minutes, or daily at a
  local system time. Schedules run while Kilix is open. Failed refreshes keep
  the last reading marked stale; readings over two hours old are also marked
  stale. Changing ZIP clears the previous location, and hiding the widget
  cancels its fetch. Weather and the Linux device menus are hidden on native
  Windows, where their settings tools are unavailable.
- A lone left or right Windows/Super key opens the top-left Start menu on
  release. Holding or repeating the key does not open multiple menus;
  Super combinations keep their normal behavior. A second tap toggles the
  menu, and focus changes cancel a pending tap. This default can be disabled
  with `kilix settings --set super_start=off`. Native Windows and macOS keep
  their normal key behavior; disabling Start also disables this shortcut.
  A window manager or compositor that intercepts Super must forward the key
  to Kilix for this shortcut to work.

The RC5 desktop-completion candidate also routes desktop portals from managed
private-X applications to the physical session. Application singleton names
stay on their private D-Bus; each portal caller gets a separate host connection,
translated request/session handles, and Unix file-descriptor forwarding.
Disconnecting a client or killing its app releases its capture sessions. This
relay requires the system Python's GIO bindings (`python3-gi` on Debian); the
stream dependency installer includes them. A private XID is not forwarded as
the parent of a physical-session dialog. Firefox and Chromium browsers in private panes use the
system portal for screen capture while their GUIs stay on X11. This browser-only
compatibility context uses an unreachable Wayland display path and preserves
the normal browser and desktop permission dialogs. The SDK launcher and
`kilix open-url` from an existing private pane share this policy. Chromium's Ozone
platform is explicitly X11; its profile, sandbox and permission settings are retained.
Firefox ESR and Chromium capture, cancellation, Stop sharing and source closure
have bounded installed default-desktop VM evidence. Chromium also shares native
dialogs within a minimized pane. Its browser preview and actual capture open
separate portal sessions; this candidate asks for system consent for each, so
answer the second picker after the browser's Share button. Multiple-source,
display, lock and broader daily-use acceptance remains pending.

Managed GTK applications using `GtkFileChooserNative` select desktop portal
file choosers when a physical-session bus route is available. An explicit `GTK_USE_PORTAL=0` is
preserved. The same private bus exposes native desktop notifications through
the physical daemon, with separate identifiers for each caller. Replacement,
closure and action signals stay with their owning application; closing the
sender leaves an already displayed notification available, as with
`notify-send`. Notification image hints keep their binary data, while private
window IDs are removed. Pending service payloads are bounded to 16 MiB per
message and 64 MiB across the relay. Native GTK Open, Save, Cancel and
application-close checks, and Libnotify show/update/close/action checks, have
component VM evidence; installed default-desktop acceptance remains pending.

Managed local application panes now mirror ordinary clipboard formats through
SDK 1.16: Unicode text, PNG/JPEG images, and local file URI lists. The relay
supports incremental X11 transfers, limits each clipboard value to 64 MiB, bounds in-flight transfers, and stops
with its pane. Network broadcast modes leave the host clipboard disconnected.
Paste requests made during a copy wait for the complete requested format, with bounded
waits and request counts. A failed acquisition refuses the waiting request
instead of returning the previous clipboard as the new copy.
Known targets and completed formats become available while other representations
are still being collected. The hub retains its previous complete bundle until
the new bundle finishes; all alternate-format bytes remain preserved.
New empty panes initialize their clipboard endpoint. Pending copies refresh
clipboard ownership so browser and GTK format caches discover the new value;
older ownership notifications cannot overwrite a newer claim.
Reply-buffered X11 events drain within the callback's event budget, so the
desktop does not sleep between already available clipboard chunks.
`KILIX_HOST_CLIP=0` disables host clipboard exchange. Kilix 95 and its built-in
fallback use the same transport, including file-manager copy/cut/paste. Two
GTK windows on separate private displays have component VM evidence for large
text and image exchange; cross-app drag/drop and installed default-desktop
acceptance remain pending.

Managed private application buses also forward the physical session's
accessibility discovery and status service. GTK/ATK applications can join the
same AT-SPI registry as the session's screen reader while their application
singleton names remain private. Status properties and change signals retain
their types; discovery calls are bounded. The matching Kilix 95 candidate
exposes its live pixel-shell tree separately. Native Kilix chrome, broader
toolkit and input workflows, and complete screen-reader acceptance remain RC5 work.

The native candidate also provides `get-pane-geometry`, consumed through
`kilix_sdk.geometry.current()`. It reports the actual pane content bounds,
cells, visibility, OS window origin and framebuffer/window sizes, including
the space occupied by native pane chrome. The SDK refreshes a surviving
broker client's route before selecting its pane, validates identity and bounds,
and maps canvas rectangles to physical X11 coordinates. Hidden panes, changed
terminal grids and unavailable screen origins are refused. Installed coordinate
and text hit-testing qualification remains pending. X11 button events update
the cursor from their own client coordinates before dispatch, so a click after
a pointer warp or window move cannot reuse an older motion sample. Relative
input with a disabled cursor retains its accumulated position.

Broker-backed SDK clients can refresh their remote-control route after the
original frontend exits. The replacement frontend's live attach process must
match the caller's exact private broker runtime and session, executable, user,
and process identities. Only the route, window identity, public key, and
password-file path are refreshed; normal remote-control authentication remains
required. Kilix 95's matching candidate applies this before launching tabs.
`agent-control` applies the same refresh, recovering the broker identity with
the route from its own process ancestors, so an agent that outlived its
terminal keeps working from the restored pane.

The matching Pleb supervisor also associates an ordinary initial page with one
login. A crash retry attaches that surviving PTY as the first active page
before starting a new desktop. The association persists across repeated
frontend crashes; a missing or ambiguous match keeps the usual startup.
Explicit startup-session definitions and URL launches keep their own commands.

Managed local application panes register with the matching Pleb capture picker
as individual Application pane sources. Private runtime records identify the
live owner, application, X server and authority file without storing cookies or
remote-control credentials. Every screenshot or sharing request still needs
physical-desktop consent. Closing a pane removes its registration; stale
process identities are refused. Network broadcast launches remain outside this
local capture catalogue.

## Release 0.2.1

Historical changes for the coordinated Plebian-OS 0.2.1 release, prepared
August 25, 2026. Its supported upgrade source was 0.2.0. For the current Linux
release and supported upgrade paths, see the 0.2.2 section above.

- Kilix Techno soundbanks install lazily from immutable, checksum-bound CC0
  source archives. The bundled catalog also carries compact CC0 instrument and
  effect packs without making any audio download part of base installation.
- Laptop sessions detach without leaving wrapper processes behind.
- Stream connection advice keeps mpv rendering inside the terminal with the
  ordered `kitty,sixel,tct` video-output fallback instead of opening a GUI
  window.

## Release 0.2.0

Published 2026-08-19 as the Kilix component selected for the coordinated
Plebian-OS 0.2.0 release. It upgrades from 0.1.9.

### Kilix Start menu and page-strip placement

The optional three-line Start button (☰) at the far left of the page strip opens
a drop-down above the panes. The panes retain their size and continue updating
behind it. The menu opens upward when the page strip is at the bottom, and
fits within the terminal window. It uses the same dark blue, white,
and highlighted-blue palette as Kilix's clickable chrome. The menu includes new
pages, settings, session tools, system monitors, desktop providers, applications,
packages, browser actions, power/session actions, and the most complete updater
available on the host. Each menu level sizes itself to its labels, with
keyboard scrolling when the window is too short to show all entries.

The menu supports mouse selection, arrow-key navigation, `Enter`, `Esc`, and
mnemonic letters. Clicking outside dismisses it and returns focus to the
original pane. Clicking Start again or pressing `Ctrl+Alt+M` toggles it.
The left or right Windows/Super key opens this same top-left menu on a lone
key release. The explicit `kilix_windows_key` action remains available for
desktop launchers that want to delegate Start to Kilix 95 or IceWM.

Configure the shared behavior with:

```bash
kilix settings --set start_menu=on       # or off
kilix settings --set tab_bar_edge=top    # or bottom
```

An explicit setting wins everywhere. With no explicit choice, standalone Kilix
keeps the 0.2.0 page strip at the top, while Pleb and Plebian-OS sessions default
it to the bottom. The Start badge defaults on for the Pleb desktop shell and off
for a standalone terminal. These are presentation defaults, not separate forks;
all hosts read the same non-executable `settings.conf` contract.

### GPU application and streaming efficiency

The 0.2.0 candidate adds a capability-gated GPU host for graphical applications.
Compatible Wayland applications share one private Weston/PipeWire host instead
of starting one compositor and capture stack per pane. Each output is paced to
its requested frame rate, hidden outputs stop producing frames, and unchanged
surfaces emit no duplicate frame. Pointer motion and buttons travel through the
dedicated input channel instead of forcing display polling.

On compatible hardware, PipeWire exports DMA-BUF frames and the Kitty fork
imports them as EGLImages for direct texture sampling. Reference-counted leases
keep producer buffers alive through presentation, and large image edits use
asynchronous GPU upload buffers. Streaming probes a real FFmpeg/VAAPI encode,
then encodes H.264 once and lets HLS, MSE, and WebRTC copy that shared stream
instead of running independent encoders.

Every accelerated path is optional. Probe results are cached privately and
invalidated by helper, driver, device, or build changes. Software renderers,
cross-device video pipelines, missing DMA-BUF support, and failed real-frame
encode probes retain the authenticated X11/raw-frame path; codec names alone do
not qualify a host. The first-use GPU-host installer provisions and verifies the
native capture, input, kiosk-shell, and encoder helpers required by the selected
path.

The exact candidates and verification evidence are maintained outside the
published repository until integration. Release adoption must preserve the
Kilix-to-Kitty pin, rerun both full test matrices, prove fallback on unsupported
hardware, and demonstrate a real shared encode on a same-device VAAPI host.

### Terminal calculator

`kilix calculator` opens a calculator over the current session in the same
native overlay theme as the Copy/Paste context window. Every button is
clickable; digits, the decimal point, arithmetic operators, percent and `=`
also work from the keyboard, `Enter` calculates, `Backspace` edits, `C` clears
and `Esc` closes. It carries memory controls, square root, reciprocal, sign
change, clear-entry and divide-by-zero handling, adapts when the pane is
resized, and reports the minimum usable size instead of drawing a clipped
keypad.

### Interactive top bar widgets

The top bar's status items are controls rather than read-outs. The calendar
icon opens a navigable month and the date/time text opens a live local-date,
clock and timezone widget. The volume icon opens a compact slider on one click,
the full clickable `kilix-volume` output selector on a double-click, and a
settings card with a live Mute checkbox on right-click. The network icon shows
compact connection status on one click and opens `nmtui` on a double-click.
Right-click CPU/RAM, GPU, temperature, read-aloud, microphone, Wi-Fi or weather
for its own menu. Clock and calendar open clock settings; battery opens
Kilix Settings. All widget menus are also accessible with
`kilix settings --widget system|gpu|temperature|speech|microphone|network|weather`.

Calendar, clock, volume, network, battery and temperature cards use the same
popup layer as Start. They open next to the clicked control, above the panes,
and stay within the window when resized. Clicking the same control again or
outside its card dismisses it. Moving to another page or application also
closes the popup. Double-clicking temperature still opens its full dashboard.

### Desktop application launching

A desktop session now keeps sole ownership of its X windows. Ordinary X
applications have no window manager on their private display, so the app runner
continues to size, focus and clamp their top-level windows; a session that
supplies its own window manager keeps that runner out of its windows entirely.

The pinned application catalog advances to a revision whose desktop launchers
all start their application, and the utility installer stays on the catalog's
pinned revision instead of resolving a moving one. The IceWM provider advance
carries its terminal launch support.

Optional desktop pins carried by this Kilix release advance to:

- **Kilix TUI utilities** — `dc462372aa7417fa9bfccd82b8312d62d1077f82`;
- **Kilix Land** — `631b0d7f6da1cc1b8ff8656fffd8f2a9195df39d`;
- **Kilix IceWM** — `00e570ceff4d5c1374e8f9a92f01cedb620dfe40`.

Kilix Cap stays on the pin 0.1.9 selected.

### Component pins moved for this release

Kilix owns the immutable revision of every optional component, so these are the
whole of what a release installs:

- **Kilix Bonsai** → `b54e617968f63594bb5e6b887b4ed4e5a8b7f055`: the generated
  `kilix-bonsai` and `bonsai-cpu` launchers quote the checkout path they exec,
  so a store installed below a directory with a quote or a space in its name
  runs instead of executing something else. An unknown model variant now says
  so and exits, a gallery entry with malformed saved metadata no longer takes
  the browser down with it, and an incomplete runtime pin is refused rather
  than fetched.

## Release 0.1.9

Published 2026-08-14 as part of the coordinated
[Plebian-OS 0.1.9 release](https://github.com/itsmygithubacct/plebian-os/releases/tag/v0.1.9).
Kilix's public `v0.1.9` tag identifies the terminal component selected by that
release.

Kilix now has one shared application boundary across the CLI, TUI, Kilix 95,
IceWM, Cap, and Land. The host catalog currently exposes 24 applications.
`kilix-content` schema 3 declares fixed named actions, accepted input types,
capabilities, system commands, geometry, and lifecycle policy; SDK 1.14 turns
each declaration into current-terminal, pane, and desktop-window plans.

One immutable `kilix-tui-utils` checkout provides File Manager, System Center,
Kilix Settings, Software Center, Session Center, Voice Studio, Camera Manager,
VirtualBox Manager, Weather, Calculator, Music Control, coding-session
recovery, Character Map, Notepad, Find Files, and Session Log Viewer. Package
readiness verifies the shared checkout once and then checks each executable,
instead of cloning or validating the same source for every menu row. Model
Store, Region Painter, and Chawan reuse fixed host-command vectors. Existing
PDF, media, camera, and DOS applications retain their independent launch
contracts.

The bundled Kilix 95 fallback now builds its Programs submenu from the same
catalog as the external provider, renders terminal apps as managed XPane
windows, and refocuses single-instance apps. Terminal-native PDF viewing also
uses the catalog boundary, while PDF Conversion retains its frozen uv-managed
runtime. It also binds Print Screen: a shot of the screen — with `Alt`, of
just the active window — lands as a timestamped PNG in the desktop folder,
where it appears as an icon immediately.

Pane chrome now completes the paired CPU/RAM indicator originally specified
for the prior release. The CPU value on the left is the sampled CPU use of
that pane's shell and descendant process tree, expressed in logical-core
equivalents (`1.0` is one fully busy core); proportional process-tree memory
remains on the right. CPU visibility defaults to `auto` above `1.0`; either
side can be set independently to `auto`, `always`, or `off`, and the chip
appears whenever either value is visible.

Kilix pane shells now alias `tb` to the tmux-cli logger delivered by the
pinned `tmux-tui` closure once `kilix tmux` has installed it, so the logger
works without the separately published `--with-tb` command. An existing `tb`
command, alias, or function always wins: the alias is skipped with a note —
silently when `tb` already resolves to the delivered tb.py.

Launching a newer Kilix no longer rewrites an existing shared
`settings.conf` merely to materialize newly introduced defaults. Missing keys
still take their documented effective defaults and are persisted when the
operator changes them explicitly. This keeps an in-place release transaction's
operator settings byte-exact, including when the previous release's updater is
the process coordinating the first hop.

The uv-managed `kilix-telemetry` 0.1.2 component now supplies CPU, memory,
pressure, temperatures, fan speeds, process records, and recent history from
one private mmap ring. One per-user sampler serves terminal chrome and the
System Center CPU, Memory, and Temperatures apps shared by the CLI, TUI,
Kilix 95, IceWM, Cap, and Land. Consumers keep direct local fallbacks, and
Kitty's rendering thread only reads an already-running ring. SDK 1.14 exposes
the pinned client as `kilix_sdk.telemetry`; `kilix telemetry status`,
`snapshot`, `pane PID`, and `history` inspect the same source. Writer status is
lock-backed, oversized process tables compact without stopping the sampler,
and pane CPU includes commands that exit between process-table scans.

Automatic samplers now share registered process owners and exit after five
seconds without a live consumer. Identity includes the boot, PID namespace and
start tick, so a reused PID does not retain a sampler. Explicit `kilix telemetry
serve` remains persistent for service management. Automatic startup requires
consumer lifetime support in the host-selected `kilix-telemetry` component.
The transcript budget worker checks the frontend's start tick each second and
exits when that frontend ends; budget passes remain once per minute. The host
native-window observer binds its parent through a kernel process handle; session
callers supply `--parent-start-tick` with `--parent-pid` to reject a PID reused
before observer startup. An observer without a parent remains persistent.

Optional desktop pins carried by this Kilix release advance to:

- **Kilix TUI utilities** — `dbdf8e5d8f3d4ab64bb555acd7c8f53cd0241c0a`;
- **Kilix Cap** — `074e9c1559a696c95095215d12c21795452f1933`;
- **Kilix Land** — `78f601542432e814c0e4af1ae06850737442106c`;
- **Kilix IceWM** — `61160987a23c647a2d27cd049ad320e0f798cf52`.

## Release 0.1.8

Prepared 2026-08-07 and published 2026-08-09 as part of the coordinated
[Plebian-OS 0.1.8 release](https://github.com/itsmygithubacct/plebian-os/releases/tag/v0.1.8).

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
  resolved at the installed prefix, and `volume`, `switch`, `bonsai`, `mask`, `rtsp`, `nvr` and
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

Published 2026-08-02 as part of the coordinated
[Plebian-OS 0.1.7 release](https://github.com/itsmygithubacct/plebian-os/releases/tag/v0.1.7).
That release established the fresh-install upgrade baseline and superseded the
older Kilix-only `v0.1.4` component milestone described below.

Version 0.1.7 is the coordinated stack release covering everything since 0.1.2,
and is the first release Kilix shares with
[Plebian-OS](https://github.com/itsmygithubacct/plebian-os),
[Pleb](https://github.com/itsmygithubacct/pleb), and
[Kilix 95](https://github.com/itsmygithubacct/kilix-95) since then. It adds
crash-persistent panes backed by `kitty-pty-broker`, an explicit capture
readiness protocol with an XDamage first-paint fix, and live-generation
preservation so long-running terminals survive a rebuild. It also turns on
[session logging](session-logs.md#session-logging-on-by-default) by default: the same broker
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
new [`Ctrl+Shift+B` leader](keybindings.md#tmux-style-leader--ctrlshiftb)
puts tmux's pane and window keys on Kilix's panes and pages without shadowing the
real tmux prefix that `kilix serve` depends on. `F12` now opens
[a real switcher](keybindings.md#pane-center--f12-or-kilix-panes) — one tree of pages and panes
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
