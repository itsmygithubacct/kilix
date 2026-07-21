# kilix 95 — the desktop environment

A Windows 95-style desktop rendered as **pixels** inside a kilix pane.
Launch with `kilix desktop` (opens in its own tab). Quit via
Start ▸ Shut Down…, or `Ctrl+Alt+Q`.

## How it works

The whole desktop is a PIL RGB framebuffer blitted through the kitty
graphics protocol — the same shared `FramePresenter` used by `kilix browse`:
bounded POSIX shared memory (`t=s`) locally, compressed inline data (`t=d`)
when `KILIX_STREAM=1` (inside `kilix serve` sessions). Input is the kitty
keyboard protocol plus
SGR-pixel mouse reporting (`?1003h`/`?1016h`), so mouse coordinates map
1:1 onto framebuffer pixels. Rendering is damage-driven: the loop only
repaints when something is dirty (input, clock tick, caret blink).

Reuses the host through `config/kilix_sdk`: `kilix_sdk.term` provides raw mode
and input parsing, while `kilix_sdk.graphics` provides the damage-aware shared
presenter. Nothing else — the toolkit below is self-contained.

## Modules

| file | what |
|---|---|
| `main.py` | entry point: `Desk` (event loop, dispatch, blit), `--screenshot` test mode |
| `theme.py` | Win95 palette, metrics, fonts, bevel primitives |
| `icons.py` | the icon set, drawn in code on a 16×16 grid (crisp at 16/32 px) |
| `widgets.py` | toolkit: Button, TextField, TextArea, ListBox, IconGrid, Menu/MenuHost, TabBar, Dropdown, Scrollbar… |
| `wm.py` | `Window` (chrome, sysbuttons) + `WM` (z-order, drags, modality) + `msgbox`/`inputbox` |
| `taskbar.py` | start bar: Start button/menu, task buttons, clock |
| `shell.py` | desktop surface: wallpaper, icon grid, launcher files, spawn verbs |
| `games.py` | Games/app registry + on-demand installers (Doom, Bashed Earth, kilix-amp) + CLI launcher |
| `apps/` | `filemgr` `notepad` `settings` `viewer` `amp`; `xpane` (X11-app-in-a-window) — each a `Window` subclass |

Input events flow Desk → (MenuHost | dragged owner | window | taskbar |
shell); windows capture the pressed widget until release, which is what
gives every widget drag behavior for free.

## The desktop folder

`~/.local/gpu_terminal/kilix/data/desktop` (override:
`$KILIX_DESKTOP_DIR`). Real files
and directories in it appear as desktop icons; `*.desktop` files (created by
**Create Launcher…**) appear as shortcuts. Launcher spec, freedesktop-style:

```ini
[Desktop Entry]
Type=Application          ; or Link (+ URL=…) for kilix browse
Name=htop
Exec=htop
Path=~/                   ; optional working dir
Icon=terminal             ; a name from icons.py
X-Kilix-Open=tab          ; tab | window | run (X11 via kilix run) | browse
```

## Settings app

Edits `$KITTY_CONFIG_DIRECTORY/kitty.conf` (normally the XDG per-user Kilix
config, which includes but never rewrites the tracked `config/kitty.conf`
defaults). Form tabs rewrite only the managed keys (last occurrence,
preserving the rest of the user file); the `kitty.conf` tab is the raw file.
Apply reloads the running kilix live via `kitten @ action load_config_file`,
falling back to `SIGUSR1` at `$KITTY_PID`.

This tree is the compatibility fallback. The external `kilix-95` repository is
authoritative; both providers declare `provider.json` and must pass the same
SDK/API and security-feature checks before the launcher executes them.

## Testing without a terminal

```bash
python3 desktop/main.py --screenshot /tmp/shot.png --scene all
# scenes: desktop start filemgr notepad settings dialog launcher menu all
```

## Fonts / authenticity

No Microsoft artwork or fonts are bundled; icons are original pixel art and
text is DejaVu Sans 11px rendered without antialiasing. If you own period
fonts, drop `.ttf` files into `desktop/assets/fonts/` (gitignored) and they
are picked up by preference.
