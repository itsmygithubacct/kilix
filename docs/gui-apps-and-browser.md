# Web URLs and GUI apps in a pane

## Open web URLs

```bash
kilix open-url wikipedia.org        # canonical URL-opening command
kilix browse --incognito site.com   # compatible spelling; same policy
```

Both commands launch the first installed browser in this fixed order:
`google-chrome`, `chromium`, `chromium-browser`, then `firefox-esr`. `--incognito` is
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
catalogue. Pleb also installs session PATH launchers so non-interactive scripts
use the same routing; terminal-only modes stay direct. Set
`KILIX_RUN_ALIASES=0` for native X11 windows, add commands with
`KILIX_RUN_ALIAS_APPS="foo bar"`, or exclude one with
`KILIX_RUN_ALIAS_EXCLUDE_APPS="foo"`.

Chrome (including `google-chrome-stable`) and the installed x86, ARM and
AArch64 QEMU system commands use this route even without a desktop entry.
For example, `qemu-system-x86_64 -m 256 -nodefaults -display gtk` opens a
Kilix tab. In a Pleb session, graphical QEMU commands using `-daemonize`
are converted to foreground execution owned by the tab, with an explanatory
message. Direct `kilix run` still requires foreground QEMU. Headless VM commands
keep their original behavior. Use `KILIX_RUN_ALIASES=0` for an external launch.

Closing a QEMU pane (or its single-pane tab) offers **Shut down VM**,
**Force close**, and **Cancel**, with Cancel selected by default. Shut down VM
sends QEMU's ACPI power-button request through a private monitor and keeps the
pane open until QEMU exits. A guest that ignores ACPI, or QEMU running with
`-no-shutdown`, can leave the pane open; there is no automatic forced shutdown.
Monitor failures leave the pane open with an error. Force close explicitly
terminates the VM and can lose unsaved guest data. Existing user QMP monitors
are preserved; Kilix adds its own endpoint and removes it during pane cleanup.
These choices require the matching RC1 engine and app runner and apply to
new QEMU panes launched through `kilix run`.

Automation can set `KILIX_RUN_READY_FILE` to a nonexistent file in its own
private directory. Kilix creates that file with mode 0600 and contents
`content-ready\n` only once the pane has rendered changing content or its
initial frame has settled. This is a startup receipt, not a liveness check;
callers must also check their app's control endpoint. Existing files and
symlinks are refused.

RC2 uses bare Openbox on each app's private display when it is installed.
Normal app windows fill the pane; dialogs retain their own size and focus.
Window decorations use Openbox's packaged dark Nightmare-02 theme.
The private profile has no launcher or root menu, and never starts another
Kilix. `KILIX_RUN_WM=openbox` requires this behavior; `KILIX_RUN_WM=none`
selects the older WM-less fallback. The default `auto` uses Openbox when
available. Install `openbox` and `dbus-daemon` for this path.

App launches get a private session bus, so desktop singletons cannot send a
request to an existing host process and open outside the pane. Apps keep their
normal files and configuration, but host session-bus services are not shared.
Browser links use this same pane route in Pleb. Host remote-control and Wayland
display variables are removed from the app environment to prevent recursion
and display escapes. A deliberate nested desktop requires
`kilix run --desktop-session ...`; ordinary `kilix run kilix`/`kitty` is rejected.

This is launch-time routing, not adoption of existing native windows. Scripts
that directly execute an absolute app binary on the host display still create
native windows. Use `kilix run /absolute/path/to/app` in those scripts. The
host Openbox stays available to manage such windows without hiding them behind
Kilix.

Contained Chromium- and Firefox-family launches receive a private, disposable
per-tab profile so an already-running native browser cannot capture the URL and
escape Kilix. That also means every launch is a fresh login. For a persistent
session set `KILIX_RUN_BROWSER_PROFILE=<directory>` in `kilix.env`. Kilix creates
the profile with mode 0700 or makes an existing user-owned profile private.
It refuses symlinks and unsafe writable or foreign-owned ancestors without
changing existing parent permissions. Every launch then
shares that profile, and one profile means one browser -- a second launch joins
the first (Chromium) or refuses (Firefox). Pass an explicit Chromium
`--user-data-dir` or Firefox `--profile` to choose per launch; explicit
profiles are never replaced and never deleted.

Modifier keys are forwarded with a keyboard or mouse gesture:
a bare Alt press whose release went to another pane -- because the `alt+arrow`
that followed it was a kitty binding that moved focus -- used to leave Alt
latched in the app's private display, so every later key arrived as an Alt
chord until something released it. Now the injector presses modifiers around
each key and releases them with it. Modified clicks and drags retain their
own modifier state, including changes made while dragging, and everything is
released on focus-out and exit.

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
The bundled policy permits reload, font-size, self-fullscreen, and bounded
same-OS-window `send-text` without a password. Broadcast, windowless and
cross-window send requests are refused. Launch/list/focus/watch use a private,
locally generated credential; uncredentialed launch/read/close requests remain
denied. Override those
settings in your XDG `kilix/kitty.conf` and the app runs in-place in the current
pane instead.

## Terminal images and video

Use `kilix image -- photo.png` to display images and `kilix video FILE_OR_URL`
to play local video or direct streams in the current terminal. See
[terminal media](terminal-media.md) for controls and renderer options.
