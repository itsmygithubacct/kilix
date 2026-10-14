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
