# Stream sessions to other devices (experimental)

kilix can share a session over the network so you can pick it up — or just watch
it — from another laptop, a phone, or a browser. There are four tiers, from
crisp-text-cheap to full-pixel-faithful. **All of them bind to loopback by
default** (reach them over SSH); LAN exposure is opt-in and gated by TLS + a
token. Everything here is *opt-in* — plain kilix is unchanged.

## 1. Text sessions — persist + attach from many devices

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

## 2. A live Kilix pane — semantic text, graphics and audio

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

Use `--audio-codec auto|encodec|pcm` and `--audio-bitrate 3|6|12` on either
side to select the existing audio transport policy (defaults: `auto`, `6`).
Automatic selection retains PCM when model admission is unavailable; explicit
EnCodec selection refuses. Model acquisition remains a separate setup action.

A non-loopback `--socket ADDRESS:PORT --lan` is direct TLS: the server prints
both a token and a certificate fingerprint, and the client requires both. An
SSH tunnel to loopback remains the smaller exposure surface.

Kilix pins and builds the multiplexer, broker-v2 observer, and presenter tap
from their submodules. The multiplexer build requires the source-bound shared
`libkilix-encodec` package built with ONNX and Content, ORT API21,
`libsamplerate0-dev`, and `libssl-dev`. Install these through explicit system
setup before launching remote audio. The build records the native package and
embedded Content identity, compiler, flags, source commit, and output digests;
missing dependencies or a source without the enabled backend produce a setup
error. Amp and the multiplexer use the same installed native library.

`KILIX_MULTIPLEXER_HOME` selects a clean development checkout such as
`~/.local/gpu_terminal/sources/kilix-apps/kilix-multiplexer`; also set
`KILIX_MULTIPLEXER_COMMIT` to its full commit hash. This does not change the
release pin.

The multiplexer compiles a private snapshot of the exact Git objects, after
checking every working file independently of Git index optimization flags.
Builds use the provisioned GCC toolchain and a fixed compiler environment;
ambient `CPATH`, `C_INCLUDE_PATH`, `LIBRARY_PATH`, compiler search overrides,
loader injection and make environment overrides are not inherited. Use explicit
`CFLAGS`, `CPPFLAGS`, `LDFLAGS` and `LDLIBS` for supported flags, and
`PKG_CONFIG_PATH`/`PKG_CONFIG_LIBDIR` to select the installed package. Compiler
plugins, response files, alternate sysroots/specs, profiling/module/LTO inputs,
precompiled headers, linker scripts and dependency-output overrides
are refused. No model setup occurs in this build path.

Reuse checks the compiler/tool bytes, actual GCC header dependencies and linker
inputs, native/Content package identity and all output digests. GCC's reported
include search directories, including absent and earlier directories, are also
recorded. Adding an earlier or nested header invalidates reuse even if the
previously consumed header is unchanged. Before each actual compile, the
captured helper discovers GCC's search with a preprocessing-only pass that
preserves its search/language arguments. The build owner registers continuing
kernel history before acknowledging compilation. This includes absent paths,
symlink targets and negative `__has_include` decisions. Actual compiler logs
must match the admitted population; discovery output cannot authorize a binary.
Changed or unavailable relevant history refuses before fresh publication.
Unchanged search paths share the owner's existing watches across translation
units; ordinary complete generations still support reuse. Changed inputs
force a fresh generation; a dependency changed during compilation refuses the
build. Unprovable ancestor stability disables reuse. Failed or interrupted
compilation preserves previous published outputs, and the dedicated owner
retains both the directory flock and named build lock through descendant cleanup.
Held directory descriptors and kernel mutation events protect the generated
source hierarchy, including a directory swap followed by restoration. Lock name
or directory changes refuse; a replacement lock cannot create two publishers
in the same directory. Detected boundary loss during publication restores this
transaction's replaced outputs while the directory remains locked. Replacement
of the two binaries and stamp is not crash atomic: a crash or filesystem error
can leave a partial pair, which the next identity validation rejects.
The private build command
has one 240-second budget including lock wait and raw Git capture.

## 3. A GUI app — view + control from a browser or VNC client

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

## 4. The whole kilix — every pane, graphics and video included

```bash
kilix share                       # whole kilix on a headless screen -> your browser
kilix share --size 1600x900 --lan
kilix share --audio --debug       # desktop audio in the stream + encode metrics
```

(*Renamed from `kilix desktop` when the
[desktop providers](desktops.md#desktops-in-a-tab) claimed that name.*)

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
