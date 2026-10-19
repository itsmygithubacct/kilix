WSL companion setup

Pane/tab commands need Python 3, WSL interop and Windows PATH import. Setup uses
uv's managed Python 3.12.14 for archive extraction and building. Open a
fresh Kilix WSL window after installing the native package. Run `kilix help`.

Needle and the public game catalog need a per-user Linux installation. On Ubuntu:
  sudo apt-get install build-essential zlib1g-dev pulseaudio-utils
pulseaudio-utils provides the audio client for WSLg sound. Graphics use inline
transfers because Windows cannot access Linux shared-memory buffers.
Use a trusted Linux uv executable to provide Python 3.12.14. Nothing is installed
in the Windows Python environment.

Create a public source bundle without local checkouts or credentials:
  python3 provision.py --output companion.tar.gz
Add --with-apps to carry the five implemented application source closures for
offline installs. Downloads use full commits and source-archives.json SHA256s,
including explicit dependency pins. The exporter writes companion.tar.gz.sha256.
No models or owner acceptance receipts are included. The broker source is also
included for optional WSL session recovery.

Alternatively, create the bundle from initialized source checkouts on Linux:
  python3 bundle.py --needle /path/to/kilix-needle --games /path/to/kilix-games \
    --super-kilix /path/to/super-kilix --kilix-land /path/to/kilix-land \
    --plant-grower /path/to/pleb-plant-grower \
    --output companion.tar.gz

The exporter uses source-revisions.json, including each nested dependency pin;
uncommitted changes are excluded. It prints the SHA256. Transfer the bundle and
uv to the target machine, then run in the intended WSL distribution/user:
  sh /path/to/native-install/wsl/setup.sh /path/to/companion.tar.gz SHA256 /path/to/uv

Needle's model has its own license and verification. If the same owner already
has an accepted installation, bundle.py can import it with:
  --needle-model /path/to/content/assets/needle2 --receipt /path/to/existing-receipt.json
This includes the owner's acceptance record: keep that personal bundle private.
Without an imported model, run Needle interactively in Kilix to complete its
normal model/license setup. The companion never creates acceptance records.
Agent mode fails if model readiness or existing license coverage is missing.

Installation lives in ~/.local/share/kilix-wsl, with versioned source generations
and a private Python interpreter. Setup builds and checks the new generation
before switching the current symlink atomically; failed builds leave the previous
installation active. Existing generations remain available. Models are retained
across source-only upgrades; conflicting imported model data is rejected. Model
license receipts use the standard ~/.local/gpu_terminal/license-receipts store.

All fifteen games in game-catalog.json are installed: Solitaire, Lander,
Joustix, Brokeout, Bashed Earth, Lights, Jpak, Pong, Chess Bash, Rancher,
Fishtank, Tic-tac-toe, Super Kilix, Plant Grower, and Kilix Land. The twelve
small games come from kilix-games; the other three retain their own pinned
repositories and SDK dependencies. Sources, assets and license notices remain
in each generation. Launchers use the game's source directory so assets resolve
from any calling directory; relative file arguments are relative to that game.
User saves remain in the game's normal Linux user directories.

`kilix games list` lists enabled, installed canonical names, with
`--json` for scripts. `kilix games play NAME [ARGS]` runs in the current pane;
`kilix --launch NAME [ARGS]` opens a new tab. Both accept catalog aliases, such
as pong, chess, solitaire, lander, and plant-grower. Original binary names
terminal-lander and kitty-brokeout also work. Needle uses the same game readiness and enable/disable state. Setup applies a
hash-checked vocabulary delta to the pinned Needle parser for Plant Grower and
Tic-tac-toe, retaining its normal admission, confirmation and readiness checks.
Games outside this public catalog are not included.
Lights and Fishtank support Q to quit. Plant Grower's current upstream input
map has no quit key; close its Kilix pane when finished.

Examples:
  kilix needle "split right"
  kilix needle apps "open pong"
  kilix --launch kilix-pong
  kilix games list
  kilix --launch chess
  kilix games play solitaire --seed 7
  kilix pane list
  kilix pane right -- bash
  kilix native help

Linux app argv, Linux user and the invoking cwd are passed through wsl.exe.
Remote launch --env NAME=value sets a Linux variable; NAME= sets it empty and
NAME removes it. Values stay literal, including spaces, quotes and dollar signs.
Use `kilix @ --help` or `kilix @ launch --help` for remote command help.
Pane/tab help and launch help after options work without a live connection.
`kilix fullscreen` toggles the native window containing the invoking WSL pane.
Raw launch
options that have no supported translation fail explicitly. Prompt-sensitive
Needle typing still requires shell integration. Linux desktop services remain outside the companion's scope.

Public application catalog and individual games

  kilix apps list
  kilix apps info browser
  kilix apps install browser
  kilix apps launch browser https://example.com
  kilix apps install pdf
  kilix apps launch pdf /path/to/document.pdf
  kilix apps install voice
  kilix apps launch voice --speak "Hello" --output /path/to/speech.wav
  kilix apps install graphs
  kilix apps launch graphs --help
  kilix apps install media
  kilix --launch media /path/to/video.mp4
  kilix games list --all --json
  kilix games install tic-tac-toe
  kilix games update plant-grower
  kilix games disable pong
  kilix games enable pong
  kilix apps choose
  kilix games choose --gui

apps/app and games share list, info, install, update, enable, disable, check,
launch/run/play, choose and export. App list includes readiness and unsupported
reasons. games list keeps the original enabled-name list; --all shows every
entry and its status. info shows source, prerequisites, runtime and model limits.
check runs a bounded launch probe; list/readiness never downloads anything.
--launch opens a new native tab; apps launch and games play run in this pane.
Known disabled/unsupported names fail explicitly, even if PATH has that name.
Enable/disable changes catalog availability; it is not an executable access rule.
WSL games share the game toggles used by kilix settings and Needle, including
legacy aliases and GPU_TERMINAL_SETTINGS_FILE. Native/app selections remain in
their catalog state. Existing disabled games stay disabled until an explicit
shared preference or catalog enable changes them.

The implemented WSL app sources are Chawan (text browser), kilix-pdf (in-pane
viewer), kilix-graphs (text/SVG), kilix-voice (eSpeak settings/export/interactive
read-aloud), and kilix-mpv (terminal media). Additional source adapters provide
Amp's shared terminal player, Techno, the three tmux tools, procedural Soundgen,
and Voicegen's developer frontend. Needle remains part of companion setup,
including its independent model/license readiness. The document/model providers
below expose usable core commands with explicit limitations. Readiness "limited"
means the named core works; it does not mean that inference, hardware or optional
providers have been qualified. info and the chooser display those capabilities.

  kilix apps install amp
  kilix apps launch amp /path/to/music.wav
  kilix apps launch amp --desktop
  kilix apps install techno
  kilix apps launch techno --new --headless --save /path/to/set.ktech
  kilix apps launch techno --export /path/to/set.ktech /path/to/set.wav --bars 4
  kilix apps install soundgen
  kilix apps launch soundgen car-engine /path/to/engine.wav --duration 2
  kilix apps install tmux-cli
  kilix apps launch tb ls --json
  kilix apps install tmux-tui
  kilix apps launch tmux-manager
  kilix apps install tmux-browse
  kilix apps launch tmux-dashboard serve --port 8096

Keep Techno projects in a private directory on the Linux filesystem, such as
~/Music/kilix. Its state library checks Unix ownership and permissions; a Windows
drive mounted without Unix metadata can reject a project with security-error.
Completed WAV exports can be copied to the Windows filesystem.

Amp and Techno need the sndfile/FluidSynth development packages listed by info;
Amp also needs SDL2/SDL2_image. They do not fetch optional SoundFonts, soundbanks
or EnCodec models. Amp defaults to the existing Music terminal UI; --desktop
opens a separate Linux SDL editor and requires WSLg/X11. Ubuntu22 libsndfile1.0
cannot decode MP3; use mpv for that format. WSL audio must already work.
Soundgen's standard-library WAV generators also run natively on Windows. Its
NumPy/SciPy transforms require separately installed dependencies; doctor reports
each provider. No model is used. Voicegen needs Git and a C++17 compiler; it uses
hash-pinned private CMake3.31.6 and builds its exact public utf8proc dependency.
Ubuntu22 needs only `sudo apt install build-essential git ca-certificates`, then
`kilix apps install voicegen`; no system CMake upgrade is required. The temporary
build tool is removed after use, retaining its notices and verified cached wheel.
Use `kilix apps launch voicegen frontend --profile prose --text TEXT` for the trace. It has
no published production voice and this build excludes neural research synthesis.

The tmux tools require Linux tmux. tmux-browse also requires ttyd; Ubuntu22's
ttyd1.6 is supported by adapting its older writable-by-default interface. The
dashboard defaults to loopback and disables the unbundled federation extension.
Explicit --bind selects another interface. Optional extension installers and
standalone tb web features are upstream tools, not catalog-qualified features.
Distribution ttyd packages can start a system service; manage that separately
if installing ttyd solely for the catalog's own loopback dashboard process.

Additional WSL runtimes

  kilix apps install dosbox
  kilix apps launch dosbox
  kilix apps install land-agent
  kilix apps launch land-agent resident --provider deterministic --headless
  kilix apps install multiplexer
  kilix apps launch multiplexer serve --socket /path/to/owned.sock -- bash
  kilix apps install playalong
  kilix apps launch playalong --list
  kilix apps install nvr
  kilix apps launch nvr cameras

DOSBox builds the public terminal-enabled DOSBox-X and needs a TTY for interactive
Kitty graphics; DOS software is user supplied. Land Agent provides its studio and
deterministic resident. Its MIT code and CC BY-NC-SA4.0 assets retain their notices.
Resident speech is off by default; resident --enable-speech explicitly enables an
installed provider. Qwen needs an explicitly approved SSH/Ollama host. The Plant
Grower handoff requires upstream Linux terminal ownership and remains unavailable
across the WSL/native bridge; launch the game directly with kilix games play.

Multiplexer provides standalone serve/attach/bench sessions with PCM audio. It
does not capture native panes or build the optional EnCodec provider. Playalong's
native player and offline renderer work with existing projects. Its explicit
setup-pipeline command uses existing uv0.12.x/Python3.10 to install only the locked
base packages, with no interpreter downloads or ML extras. pipeline then exposes
upstream commands and media-rights/model-download choices. ML providers and
approved weights are separate. The optional Python runtime stays outside exported
generations; run setup-pipeline again after a source update or binary relocation.

NVR provides its recorder core; recording needs FFmpeg and your camera settings.
The adapter blocks default model loading and requires explicit approved provider
commands in KILIX_OBJECT_DETECTOR/KILIX_SOUND_CLASSIFIER for detection. It does not
connect to cameras during install/check. Use foreground run: upstream service
installation bypasses this provider gate and is refused. No service is installed.

Document and model provider commands

  kilix apps install kilix-pdf-conversion
  kilix apps launch kilix-pdf-conversion convert /path/to/input.pdf --profile fast --output-root /path/to/output --json
  kilix apps install kilix-image-shop
  kilix apps launch kilix-image-shop --help
  kilix apps install kilix-background-remover
  kilix apps launch kilix-background-remover inspect /path/to/image.png
  kilix apps install kilix-bonsai
  kilix apps launch kilix-bonsai pull bonsai-8b --dry-run
  kilix apps install kilix-ollama
  kilix apps launch kilix-ollama library --json

PDF Conversion installs only its hash-locked native-text extraction runtime.
OCR/vision engines are excluded. Image Shop provides checked project storage,
document edits and export presets; no pixel renderer, GUI or production model
provider is supplied. Background Remover provides bounded image inspection;
its public source has no production cutout profile. These are limited source
adapters, including tested Python3.12 core paths for the image projects whose
published wheel metadata requires3.13. Their metadata is retained unchanged.
Bonsai provides store tools; its inference runtime is separate. Ollama provides
the public private-service client, requiring a separately running approved
daemon and matching model manifests. Installing either app starts no service.
Each provider exposes --capabilities and model-setup, which delegates to the
same Content license/receipt authority as kilix models. Model acquisition is an
explicit operation; existing models and receipts remain in their original store.
Ambient Python/model packages cannot turn ordinary launch into a model download.

Waydroid needs Linux Android-container/desktop services unavailable through this
companion. Voicebox needs its Tauri/WebKit desktop and ML service; its automatic
upstream model loading has no shared receipt adapter here. Both remain explicitly
unsupported, with their public source links retained for inspection.

Source installs use the catalog's reviewed commit, never a moving branch.
Update rebuilds that pin; newer pins arrive with a companion update. Only the
selected app/game is built. A failing build/probe leaves the active version,
models, receipts, saves and enable/disable choice intact. Complete prior versions
remain under ~/.local/share/kilix-wsl/catalog. The state file is
catalog-state.json; the original full companion generation stays under current.
Apps resolve relative file arguments from the invoking directory. Games retain
their original asset directory as cwd. No system package is installed implicitly.

Use info to see missing build commands/libraries and Debian/Ubuntu package names.
Browser: build-essential pkg-config libssl-dev libbrotli-dev; a checksum-pinned
Nim 2.2.10 toolchain is downloaded on an explicit source install. SFTP is disabled.
PDF: build-essential pkg-config libpoppler-glib-dev libcairo2-dev zlib1g-dev.
Voice: espeak-ng, and pulseaudio-utils for direct playback. Catalog requests use
eSpeak/en-us by default, without changing saved settings. --voice overrides a
request. Neural voices, dictation and the voice daemon require separate setup;
no model weights or acceptance receipts are installed by the catalog.
Graphs: Python 3.12+; optional raster output is not bundled.
Media on Ubuntu 22.04: install these prerequisites explicitly, then build:

  sudo apt install build-essential ninja-build nasm pkg-config patchelf \
    libass-dev libluajit-5.1-dev libpulse-dev libgnutls28-dev zlib1g-dev
  kilix apps install kilix-mpv
  kilix apps launch kilix-mpv -- /path/to/video.mp4

The catalog uses the companion's managed Python 3.12+, downloads SHA256-pinned
FFmpeg 6.1.4, libplacebo 6.338.2, Meson and generator sources, and builds two jobs
at a time into its private generation. System FFmpeg/libplacebo/Meson packages
are not used or upgraded. Allow several minutes and about 2 GiB temporary space.
Original sources and license notices remain with the installed private runtime
and binary exports. Inline video uses CPU decoding; OpenGL, Vulkan, hardware
decoding and optional external codecs are disabled. Built-in FFmpeg decoders,
HTTPS through GnuTLS, subtitles and PulseAudio are enabled. A working WSL audio
server is required for playback; installation never configures one. Dependency
checks run before source downloads. Binary exports need a matching distribution
ABI even though their private media libraries relocate with the executable.

The terminal chooser accepts a number and an action (launch/install/update,
enable/disable/details). --gui uses tkinter/Tk and a display, including WSLg;
it reports missing GUI support when that interpreter has no Tk. The terminal
chooser requires no GUI dependencies. Install/update/export/choose use the
managed interpreter when the distribution's Python is older than 3.12.

Offline source and verified binary installation

  kilix games install pong --source-bundle companion.tar.gz --sha256 HASH
  kilix apps install graphs --source-bundle companion.tar.gz --sha256 HASH
  kilix apps export graphs graphs-binary.tar.gz
  kilix apps install graphs --binary graphs-binary.tar.gz --sha256 HASH
  kilix apps update graphs --binary https://example.com/graphs.tar.gz --sha256 HASH

A source bundle must contain the selected source closure (use --with-apps when
provisioning apps). Binary bundles use the catalog export format, retaining the
corresponding source and notices; no public binary release is implied. Verify
the supplied SHA256 with its publisher. Import checks the exact app, source pins,
architecture and runtime, then probes before activation. The receiving system
must have compatible runtime libraries. Game launchers and Python app paths are
relocated at import. Models/receipts are never imported by per-item operations.

Native Windows

From a native PowerShell/cmd tab the catalog uses a separate per-user store at
%LOCALAPPDATA%\Kilix\catalog. Graphs supports native Python text/SVG output;
the embedded Kilix Python launcher is handled without requiring python.exe.
windows-browser, windows-pdf and windows-media open the registered Windows
application outside the terminal. windows-voice uses the installed SAPI voice.
These handlers do not install a replacement browser, reader or player. Example:
  kilix apps launch windows-browser https://example.com
  kilix apps launch windows-voice "Hello from Windows"
The fifteen games and Linux app builds remain explicitly WSL-only; native
commands do not relabel or implicitly start a WSL executable. The Tk chooser
requires Tk in the native interpreter, as on WSL.

Focused source validation (no live desktop required)
  python3 -m unittest discover -s windows/tests -p 'test_wsl*.py'
  python3 windows/tests/check-catalog.py --root /path/to/kilix-wsl \
    --apps graphs voice pdf browser --needle /path/to/current/needle --output results
For an installed media build, pass --apps media to run its private-PTY graphics
protocol/input checks. Native Windows rendering, WSLg audio and registered
Windows handlers still need coordinator desktop qualification.

Recoverable WSL sessions (current companion bundle required):
  kilix session new work                 # start a persistent login shell
  kilix session new build -- make all    # or a specific program
  kilix session list [--json]            # this Linux user and distribution
  kilix session detach                  # inside work; or detach NAME elsewhere
  kilix session attach work             # in a fresh WSL pane
  kilix session restore                 # detached sessions in new native tabs
  kilix session kill work               # terminate its process tree

`kilix sessions` is an alias. Names are 1-32 letters/digits/_/-, starting with
a letter or digit. Closing a managed pane/window detaches; ordinary panes do
not become persistent. A second attachment is refused until the first detaches.
Exit ends the shell normally. Attach/restore never rerun an exited command.
Restore requires native control; attach also works from another WSL terminal.
Explicit detach requires Linux pidfd support (WSL2); on older kernels, close
the attached pane instead.

The same pinned kitty-pty-broker used by Linux Kilix owns the PTY and live
processes. There is no tmux parser in this path; inline graphics are forwarded
unchanged. At most 64 MiB of raw replay is retained. Overflow loses older
history and attach reports it; applications may need a redraw. File/shared
memory images, native tab layouts and exact renderer state are not restored.
Reboot/logoff, WSL shutdown/termination, OOM or broker death ends the work.
WSL controls VM lifetime; setup does not change it or install a keep-alive.
Native PowerShell/cmd persistence uses a separate backend: `kilix native session`.

Runtime files are private under /tmp/kilix-wsl-UID. The active attachment keeps
native control credentials and WSL_INTEROP in a mode 0600 file. The companion
refreshes them on every call, so pane/tab/fullscreen commands target the new
window after reconnect. Detached/stale attachments cannot use old credentials.
Existing process environments are otherwise unchanged; direct Windows exe
calls or applications caching control details may need restarting. Use kilix
for native control after reconnect. Retain the old native installation while
sessions created from it are still running.

Shared command tools (no live terminal needed for --help):
  kilix help settings
  kilix settings --print
  kilix settings --set pane_memory=off
  kilix models list
  kilix models show needle2
  kilix agent-control context
  kilix agent-control list
  kilix action capabilities
  kilix tmux --socket /absolute/path/to/owned.sock --json list
  kilix transcript capture --pane 2
  kilix transcript list --json

Settings here are Linux companion settings, not native renderer configuration.
Use kilix native settings --help for %LOCALAPPDATA%\Kilix\kitty.conf.
The model command uses the companion content directory and the normal explicit
licence/receipt checks; list/show/verify perform no installation. An explicit
models --root /absolute/path overrides the default store.

Agent-control/action use authenticated native control and the shared parsers.
The broker field in their schemas contains an instance/pane token on Windows,
not a persistent PTY session. Take a new list/context snapshot after restarting
the renderer. Input requires a reported shell prompt or a recognized foreground
coding agent. Same-user Linux foreground groups are observed only in this
distribution with an exact inherited native route; missing or ambiguous metadata
does not establish readiness. Verified delivery still requires the supported
Codex composer and an empty input field. Receipt retries never repeat an uncertain
action, and input acceptance is not task completion.

Tmux requires its explicit --socket and a separately installed Linux tmux.
No arguments prints the available headless verbs. Text/file/stdin input remains
literal; type sends Enter separately. It never selects an unrelated tmux server.
Transcript captures are explicit snapshots of retained pane output. list/path/
show/view/clean also read saved logs; clean --pane reads a live snapshot. There
is no automatic ConPTY recording or pruning. Archived .zst files need zstd;
interactive view uses less. Expired output cannot be reconstructed.
