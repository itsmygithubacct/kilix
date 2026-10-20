# Images and video in the terminal

```sh
kilix image -- photo.png
kilix image --place 60x20@0x0 -- photo.png
kilix video clip.mp4
kilix video 'https://example.org/live/stream.m3u8'
kilix video 'rtsp://camera.example/live'
kilix video --mute --loop -- clip.mp4
```

`image` uses the active engine's `icat` renderer, accepts its options and multiple
files, and preserves Kilix's streamed-session image transfer behavior. It renders
in the calling terminal. Use `kilix icat --help` for renderer options.

`video` runs mpv in the calling terminal with Kitty graphics. Space pauses,
arrows seek when supported, and `q` quits. Sound is enabled unless `--mute` is
specified. In WSL, mpv streams silence while paused: WSLg's audio server can
otherwise hang playback on resume or quit after a pause of five seconds or more. Local files and direct network media streams are supported; websites
that need URL extraction may additionally require yt-dlp. Use `--` before media
names beginning with `-`. One source is accepted per invocation.

RC6 includes mpv. Standalone installations need the `mpv` package with its Kitty
video output. `kilix video --vo tct clip.mp4` explicitly selects text-cell video
for terminals without Kitty graphics. There is no automatic GUI fallback.
Playback ignores mpv configuration and user scripts to keep output in-terminal.
Run in an interactive terminal; SSH needs `-t`. Multiplexers must support passing
through Kitty graphics, or use `--vo tct`. Video does not inherit the special
Unicode-placeholder transfer mode of `kilix image`.

The default OS mouse pointer stays visible above images and video. The optional
`software_mouse_cursor block`/`pointer` is drawn in the text layer and can be
covered by terminal graphics. `mouse_hide_wait 0` disables pointer auto-hide.

## Player crash recovery and the mpv 0.40 resize fix

Kilix keeps a supervisor alive during playback. On player exit or abort it
restores the saved tty settings, disables mouse reporting, leaves the alternate
screen, and shows the text cursor. A signal termination returns 128 + signal.

The system mpv 0.40.0 Kitty renderer can reuse a freed command buffer on resize.
Kilix refuses that known version for Kitty output unless the private patched
player is installed. Text-cell output (`--vo tct`) remains available.

```sh
kilix video --install-player
```

This explicit installer downloads SHA-256-verified mpv 0.40.0 source, applies the
included one-line buffer reset patch, builds terminal output with Lua controls
and PulseAudio, and installs under Kilix storage at
`media/mpv-0.40.0-kilix1`. It does not replace system mpv. Source and licenses
are retained there. It requires build dependencies first; on Debian these are
`build-essential meson ninja-build pkg-config curl patch libavcodec-dev
libavfilter-dev libavformat-dev libavutil-dev libswresample-dev libswscale-dev
libplacebo-dev libass-dev libluajit-5.1-dev libpulse-dev` (including their package
dependencies). An RC6 image must preinstall this fixed player or provide the
build dependencies before pixel video can be used with its mpv 0.40 package.

For a shell left in mouse-reporting mode by an older launcher, run:

```sh
printf '\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[?1016l\033[?25h'
```

## Plebian-OS 0.2.2 named player

The next-release integration prefers the separately packaged `kilix-mpv` on
PATH, then the earlier private patched player, then supported system mpv.
The 0.2.2 stack package closure must install the verified `kilix-mpv` Debian
package; `kilix video` discovers it without changing `/usr/bin/mpv`. The package
is built from the maintained kilix-mpv fork and distributed with exact source
and checksums. Inclusion is required for Plebian-OS 0.2.2 after the frozen RC6
candidate. The `0.40.0+kilix.4` controls and transport fixes are the current
candidate; publication, coordinated OS integration, and target-image playback
acceptance remain pending.

The `0.40.0+kilix.2` player adds adaptive lossless inline compression, synchronized
image presentation, SGR mouse input and redraws while paused after a resize.
The supervisor suppresses text status overlays and clears synchronized-update
mode during recovery. The graphical seek bar and error messages remain available.

With Kitty graphics, warnings and errors stay in a panel at the top of the video
for the rest of the session, including while paused, seeking, or resizing. The
first four distinct messages stay pinned; repeats are collapsed and additional
messages are indicated.
Long messages are shortened in the panel. After playback, diagnostics are printed
again on the restored terminal (up to the last 64 KiB), so quitting or a player
crash does not erase them. This uses the bundled Lua overlay, supported by the
packaged player, and does not change the seek bar's normal timeout. Text-cell
output retains diagnostics after exit but cannot draw the graphical panel.
Player version `0.40.0+kilix.3` also defers clearing during a resize until the
replacement frame is ready, keeping the video and panel visible while paused.

The private display regression check requires Pillow, Xvfb, xdotool and
ImageMagick. Supply a video at least two seconds long:

```sh
python3 -B tests/check_terminal_video_errors.py /path/to/kilix-mpv \
  /path/to/kitty /path/to/video.mkv /path/to/evidence
# Repeat with --transport shm for local shared-memory playback.
```

For a local terminal that shares `/dev/shm` with the player:

```sh
kilix video --transport shm clip.mp4
```

Shared memory requires `kilix-mpv` version `0.40.0+kilix.2` or later. It uses a
bounded three-frame ring, bypassing base64 pixel transfer. The launcher defaults
to `--transport auto`: it verifies a local Kitty process owned by the current
user and the same `/dev/shm` directory before choosing shared memory. Unknown
terminals, inaccessible process information, separate namespaces, SSH and tmux
fall back to inline. `--transport inline` explicitly forces portable transfer.
Explicit `shm` is rejected for known SSH/tmux sessions and text-cell output.
Shared-memory objects are released on normal exit.

Player `0.40.0+kilix.4` negotiates pixel mouse reporting (DEC mode 1016), making
small controls targetable without terminal-cell rounding. Terminals without
support retain cell coordinates. Click the speaker to mute; scroll over it to
adjust volume. Click or drag the seek bar; click play/pause to toggle playback.
The fullscreen button and `f` use Kilix's existing tty-only fullscreen permission,
with requests serialized with the video packets. The launcher probes support so
older installed forks remain usable. Fullscreen changes made outside mpv are not
reflected in mpv's option state.

An A/V desynchronisation warning means mpv observed more than half a second of
drift. Inline transfer can fall behind at large pixel sizes or under CPU load.
For local Kilix, automatic shared-memory selection avoids the base64 and PTY
pixel copies. On an inline-only connection, reducing the terminal window size
reduces transfer load. The warning stays visible; it is not suppressed.
