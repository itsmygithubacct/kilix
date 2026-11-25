# Installing Kilix

Requirements, engines, the first-run commands, the taskbar icon and uninstalling. The [README](../README.md#quick-start) has the short version.

## Requirements

- **Linux desktop:** x86_64 or arm64 for the prebuilt engine. The clickable-chrome
  fork build supports x86_64 and arm64; arm64 builds use the system's
  development packages (`KILIX_BUILD_MODE=system`). Native Windows preview
  builds have [separate requirements](../windows/README.md); macOS is unsupported.
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
  Font release. Every promoted generation keeps the upstream license and a
  provenance record beside the font under `src/fonts/`; the record includes
  the effective source URL plus the verified archive and extracted-file
  digests. A generation whose font has lost either notice is refused rather
  than promoted, so no built artifact ships the font without its terms. An
  offline/release build may instead set
  `KILIX_BUILD_MODE=bundle` with an immutable `KILIX_KITTY_DEPS_URL` and matching
  SHA-256; mutable kitty CI bundle URLs are rejected. **`scripts/install-build-deps.sh` installs
  all of that** on Fedora/RHEL (dnf), Debian/Ubuntu (apt), Arch (pacman), and
  openSUSE (zypper). Where the distro's Go is older than the fork needs (e.g. Fedora
  ships 1.25), it configures the exact `toolchain` version from `go.mod` so Go
  can fetch that checksum-verified toolchain on demand. `build.sh` forces that
  exact version even if the host has a newer Go — no open-ended latest lookup
  and no manual Go install. Current kitty source also uses Python 3.12 syntax:
  of `python3.14`, `python3.13`, `python3.12` and `python3`, `build.sh` takes
  the first that is 3.12 or newer and has its `Python.h`, and only when none
  has the headers the first that is new enough, with a warning. `--verify`
  reports the same interpreter and fails when its headers are missing.
  Set `KILIX_PYTHON=/path/to/python3.12+` when the desired interpreter is not
  on `PATH`.
- The same dependency installer also includes kilix-amp's SDL/libsndfile/
  FluidSynth packages, so the desktop Media Player can build and play MIDI.
  The fork build links none of them, so `--verify` reports them as the Media
  Player's and does not fail without them; the desktop builds kilix-amp on
  first use and needs them then. Kilix Amp needs only the FluidSynth library
  and a SoundFont, but on Debian the package with FluidSynth's development
  files depends on the `fluidsynth` player, so the player is installed too,
  and its package enables a daemon for every login that holds the default
  sound card, which dictation records from. When that enablement appeared
  during this installer's own apt run, the installer removes it, says so,
  and records it in `/var/lib/kilix/audio-holdoff.log` (time, link, and
  why); the player stays installed. That includes the case where the run
  completed an install of the player that you had started and left
  unfinished, since apt finishes those whatever it is asked for; the
  warning then says so, and how to re-enable the daemon. It holds even when
  apt fails part-way or the installer is killed, because the next run
  finishes the check against what the interrupted one recorded. An
  enablement that was already there is left alone. One consequence: a
  player you removed without purging leaves its enablement behind, and
  when this installer brings the player back, the daemon starts at every
  login again. The installer warns when that happens; turn it off with
  `sudo systemctl --global disable fluidsynth.service`. This hold-off runs
  on every host with apt, Debian, Ubuntu and Plebian OS alike: there is no
  host detection.
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

Optional coding-agent skills are installed as described in the [README](../README.md#optional-coding-agent-skills) and [coding-agents.md](coding-agents.md).

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
kilix transcript clean <session>  # print what one pane showed, without control codes
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
kilix bonsai pull vibevoice-asr-bitnet   # the dictation model, once `kilix models install` took its licence
kilix rtsp list                    # cameras this machine is configured for
kilix rtsp view poolcam --tab      # one camera filling a new page
kilix rtsp mosaic yard             # several in a grid
kilix nvr cameras                  # what each camera is set to do
kilix nvr review                   # browse recorded events with pictures
kilix mask --image plate.png room.mask.png   # paint a region map over a picture
kilix mask --render frame.ppm room.mask.png  # compose one frame, no terminal
kilix voice install               # pinned Kilix Voice + default Vosk model
kilix stt --models                # all speech models, sizes, install/runtime state
kilix stt --models --json         # versioned machine-readable catalog contract
kilix stt --install lgraph-en-us --default lgraph-en-us
kilix stt --install vibevoice-asr-bitnet --default vibevoice-asr-bitnet  # weights + VibeASR runtime
kilix voice vibeasr               # (re)build the pinned VibeASR.cpp dictation runtime
kilix stt --install whisper-small-en --default whisper-small-en  # weights + Whisper runtime
kilix voice whisper               # (re)install the pinned Whisper dictation runtime
kilix voice whistle               # install the pinned Whistle native library
kilix tts --tiers                 # hardware-aware read-aloud choices
kilix tts --interactive --tier neural   # first-use Piper setup and session
kilix tts --interactive --tier pocket-cpu # measured Pocket/Alba CPU audition
kilix tts --interactive --tier qwen-cpu # locked CPU runtime, then first-use Qwen model
kilix tts --interactive --tier qwen-gpu # locked CUDA/FlashAttention runtime on an eligible GPU
kilix tts --interactive --tier qwen-base-gpu # 0.6B Base with a fixed synthetic reference
kilix tts --interactive --download-qwen qwen3-tts-0.6b-customvoice
kilix tts --interactive --download-qwen qwen3-tts-0.6b-base --synthetic-reference
kilix tts --interactive --download-qwen qwen3-tts-1.7b-voicedesign --description 'A warm clear voice'
kilix tts --speak 'Hello' --model qwen3-tts-0.6b-customvoice # explicit provider path
kilix voice doctor                # dependency and audio-device diagnostics
kilix fingerlock                  # fingerprint reader: install fprintd on first use, enroll, verify
kilix tts                         # read-aloud settings and test-phrase TUI
kilix stt                         # dictation settings and microphone-level TUI
kilix speak "hello"               # read explicit text aloud
kilix dictate                     # recognize one utterance; never presses Enter
kilix status                      # version/commit, engine, writable config, provider contract
```

Put `~/.local/gpu_terminal/sources/kilix` on your `PATH` (or
`ln -s ~/.local/gpu_terminal/sources/kilix/kilix ~/.local/bin/kilix`) to just type `kilix`.

The read-aloud tiers in the list above are described in [read-aloud-and-dictation.md](read-aloud-and-dictation.md#neural-read-aloud-tiers-and-the-speech-provider), and transcription and stem separation in [transcription-and-stems.md](transcription-and-stems.md).

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
doesn't appear immediately (icon caches are lazy). `kilix --uninstall-desktop`
takes it all back out again, removing only the files that install wrote and
that nothing has changed since — see [Uninstall](#uninstall).

## Uninstall

```bash
kilix --uninstall-desktop                                           # only if you ran --install-desktop
rm -rf ~/.local/gpu_terminal/sources/kilix                          # project source
# Optional: remove settings/state only if you do not want to preserve them:
rm -rf "$HOME/.local/gpu_terminal/kilix"
```

`--uninstall-desktop` is the counterpart to `--install-desktop`, and the
freedesktop directories it writes into are shared with every other application
on the machine, so it does not delete paths by name. The install records which
files it created and what they contained; the uninstall removes only the ones
still byte-identical to that record, refreshes the desktop and icon caches,
and prunes the directories it created if it left them empty. A `kilix.desktop`
you edited yourself, or a `kilix.png` some other package now owns, is named on
stderr and left in place — the run then exits non-zero and keeps its record so
it can be re-run once you have dealt with those files.

The record lives at `$KILIX_STORAGE_HOME/state/desktop-entry.manifest`. Without
it — an entry installed by hand, or by a kilix predating this verb — the
removal is manual:

```bash
rm -f  ~/.local/share/applications/kilix.desktop
rm -f  ~/.local/share/icons/hicolor/*/apps/kilix.png
update-desktop-database ~/.local/share/applications 2>/dev/null || true
gtk-update-icon-cache -f ~/.local/share/icons/hicolor 2>/dev/null || true
```
