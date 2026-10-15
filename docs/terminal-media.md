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
specified. Local files and direct network media streams are supported; websites
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
printf '\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[?25h'
```
